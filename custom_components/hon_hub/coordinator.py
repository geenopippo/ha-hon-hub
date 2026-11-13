"""Keeps the appliances up to date (real-time channel + REST safety net) and sends the commands."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import model
from .appliance import Appliance
from .const import (COMMANDS_TTL, CONF_MOBILE_ID, DOMAIN, I18N_TTL, NO_MAINTENANCE, POLL_NO_PUSH, POLL_PUSH,
                    SIMPLE_COMMANDS, SLOW_TTL)
from .hon import HonAuthError, HonClient, HonCommandError, HonConnectionError, HonError
from .realtime import HonRealtime

_LOGGER = logging.getLogger(__name__)

HonConfigEntry = ConfigEntry["HonCoordinator"]
CycleListener = Callable[[str, dict[str, Any]], None]


def cycle_event(previous: str | None, new: str) -> str | None:
    """Event to fire when the state of the machine goes from `previous` to `new`."""
    if previous is None or previous == new:
        return None
    if new == "running":
        return "resumed" if previous == "paused" else "started"
    if new == "paused":
        return "paused" if previous == "running" else None
    if new == "finished":
        return "finished"
    if new in ("error", "scheduled"):
        return new
    return None


class HonCoordinator(DataUpdateCoordinator[dict[str, Appliance]]):
    """One hOn account."""

    config_entry: HonConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HonConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=POLL_NO_PUSH))
        self.client = HonClient(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD], async_create_clientsession(hass),
                                mobile_id=entry.data.get(CONF_MOBILE_ID))
        self.appliances: dict[str, Appliance] = {}
        self.realtime: HonRealtime | None = None
        self.i18n: dict[str, str] = {}
        self._definitions_at = 0.0
        self._machine: dict[str, str] = {}                       # last state seen while online, per appliance
        self._cycle_listeners: dict[str, list[CycleListener]] = {}
        self._timers: set[CALLBACK_TYPE] = set()
        self._closing = False

    # ── setup ────────────────────────────────────────────────────────────────
    async def _async_setup(self) -> None:
        try:
            infos = await self.client.appliances()
        except HonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HonError as err:
            raise UpdateFailed(str(err)) from err
        self.appliances = {info["macAddress"]: Appliance(info) for info in infos}
        self.i18n = await self._load_i18n()
        try:
            await self._load_commands()
        except HonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        # if the programs of an appliance could not be read, try again in ten minutes instead of tomorrow
        complete = all(a.commands for a in self.appliances.values())
        self._definitions_at = time.time() if complete else time.time() - COMMANDS_TTL + 600
        self.realtime = HonRealtime(self.client, infos, on_status=self._on_status, on_presence=self._on_presence,
                                    on_discovery=self._on_discovery, on_channel=self._on_channel)

    @callback
    def async_start_realtime(self) -> None:
        if self.realtime is not None:
            self.config_entry.async_create_background_task(self.hass, self.realtime.run(), f"{DOMAIN} realtime")

    async def _load_i18n(self) -> dict[str, str]:
        """Program names in the language of Home Assistant, cached on disk for a week."""
        lang = (self.hass.config.language or "en").split("-")[0].lower()
        store: Store[dict[str, Any]] = Store(self.hass, 1, f"{DOMAIN}.programs_{lang}")
        cached = await store.async_load() or {}
        names: dict[str, str] = cached.get("names") or {}
        if names and time.time() - cached.get("at", 0) < I18N_TTL:
            return names
        try:
            texts = await self.client.translations(lang)
            if not texts.get("PROGRAMS") and lang != "en":
                texts = await self.client.translations("en")
            fresh = {k: v for k, v in model.program_i18n(texts).items() if not k.endswith("_DESCRIPTION")}
        except HonError as err:
            _LOGGER.debug("hOn program names not downloaded: %s", err)
            fresh = {}
        if fresh:
            await store.async_save({"at": time.time(), "names": fresh})
            return fresh
        return names

    async def _load_commands(self) -> None:
        async def one(a: Appliance) -> None:
            try:
                a.set_commands(await self.client.commands(a.info), self.i18n)
            except HonAuthError:
                raise
            except HonError as err:
                _LOGGER.warning("Programs of %s not read: %s", a.nickname or a.type, err)
        await asyncio.gather(*(one(a) for a in self.appliances.values()))

    # ── polling ──────────────────────────────────────────────────────────────
    async def _async_update_data(self) -> dict[str, Appliance]:
        results = await asyncio.gather(*(self._update(a) for a in self.appliances.values()), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        for err in errors:
            if isinstance(err, HonAuthError):
                raise ConfigEntryAuthFailed(str(err)) from err
            if not isinstance(err, HonError):
                raise err
        if errors and len(errors) == len(results):
            raise UpdateFailed(str(errors[0])) from errors[0]
        for err in errors:
            _LOGGER.debug("hOn update failed for one appliance: %s", err)
        if time.time() - self._definitions_at > COMMANDS_TTL:
            await self._refresh_definitions()
        for a in self.appliances.values():
            self._track(a)
        return self.appliances

    async def _update(self, a: Appliance) -> None:
        asked = time.time()
        a.apply_context(await self.client.context(a.info), asked)
        if a.is_cycle and time.time() - a.slow_at > SLOW_TTL:
            await self._update_slow(a)

    async def _update_slow(self, a: Appliance) -> None:
        """Maintenance counters and statistics: they change once per cycle."""
        try:
            if a.type not in NO_MAINTENANCE:
                a.maintenance = await self.client.maintenance(a.info)
            a.statistics = await self.client.statistics(a.info)
            a.slow_at = time.time()
        except HonAuthError:
            raise
        except HonError as err:
            _LOGGER.debug("hOn maintenance/statistics of %s not read: %s", a.type, err)
            a.slow_at = time.time() - SLOW_TTL + 300        # try again in five minutes

    async def _refresh_definitions(self) -> None:
        """Once a day: new appliances, new firmware, new programs."""
        self._definitions_at = time.time()
        try:
            infos = await self.client.appliances()
        except HonError as err:
            _LOGGER.debug("hOn appliance list not refreshed: %s", err)
            return
        if {i["macAddress"] for i in infos} != set(self.appliances):
            _LOGGER.info("The hOn appliances of the account changed: reloading")
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return
        before = {mac: self._shape(a) for mac, a in self.appliances.items()}
        for info in infos:
            self.appliances[info["macAddress"]].info = info
        try:
            await self._load_commands()
        except HonError as err:
            _LOGGER.debug("hOn programs not refreshed: %s", err)
        if any(self._shape(a) != before[mac] for mac, a in self.appliances.items()):
            _LOGGER.info("The options of a hOn appliance changed: reloading to create the new entities")
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @staticmethod
    def _shape(a: Appliance) -> tuple:
        """What decides which entities exist: when it changes the entry is reloaded."""
        return (bool(a.programs), sorted((k, tuple(sorted(v))) for k, v in a.option_kinds().items()),
                [d["key"] for d in a.settings], list(a.simple_commands))

    # ── real-time channel ────────────────────────────────────────────────────
    @callback
    def _on_status(self, mac: str, params: dict[str, str]) -> None:
        if (a := self.appliances.get(mac)) is None:
            return
        a.apply_live(params)
        self._track(a)
        self.async_update_listeners()

    @callback
    def _on_presence(self, mac: str, connected: bool) -> None:
        if (a := self.appliances.get(mac)) is None:
            return
        a.apply_presence(connected)
        if connected:
            self._later(3, a)                 # back online: read its whole state
        self._track(a)
        self.async_update_listeners()

    @callback
    def _on_discovery(self, mac: str) -> None:
        # an appliance announced itself (new pairing, new firmware): re-read the definitions, at most once an hour
        if time.time() - self._definitions_at > 3600:
            self._definitions_at = 0.0
            self._later(5)

    @callback
    def _on_channel(self, up: bool) -> None:
        _LOGGER.debug("hOn real-time channel %s", "up" if up else "down")
        if self._closing:
            return
        self.update_interval = timedelta(seconds=POLL_PUSH if up else POLL_NO_PUSH)
        self._later(1)                        # catch up with what happened meanwhile and apply the new interval
        self.async_update_listeners()

    @callback
    def _later(self, seconds: float, appliance: Appliance | None = None) -> None:
        """Refresh from the cloud in a few seconds (one appliance or all of them)."""
        if self._closing:
            return
        cancel: CALLBACK_TYPE

        async def run(_now: Any) -> None:
            self._timers.discard(cancel)
            if appliance is None:
                await self.async_request_refresh()
                return
            try:
                await self._update(appliance)
            except HonError as err:
                _LOGGER.debug("hOn refresh failed: %s", err)
                return
            self._track(appliance)
            self.async_update_listeners()

        cancel = async_call_later(self.hass, seconds, run)
        self._timers.add(cancel)

    async def async_shutdown(self) -> None:
        self._closing = True
        for cancel in list(self._timers):
            cancel()
        self._timers.clear()
        await super().async_shutdown()

    # ── cycle events ─────────────────────────────────────────────────────────
    @callback
    def async_add_cycle_listener(self, mac: str, listener: CycleListener) -> CALLBACK_TYPE:
        self._cycle_listeners.setdefault(mac, []).append(listener)

        @callback
        def remove() -> None:
            self._cycle_listeners[mac].remove(listener)
        return remove

    @callback
    def _track(self, a: Appliance) -> None:
        """Compare the state with the previous one and fire the cycle events."""
        if not a.is_cycle or a.connected is False:
            return                            # while offline the last state seen is kept: no false start/end
        new = a.status
        previous = self._machine.get(a.mac)
        self._machine[a.mac] = new
        event = cycle_event(previous, new)
        if event is None:
            return
        program = a.current_program
        data = {"program": program["name"] if program else None}
        _LOGGER.debug("hOn %s: %s -> %s (%s)", a.type, previous, new, event)
        for listener in list(self._cycle_listeners.get(a.mac, [])):
            listener(event, data)
        if event == "finished":
            a.slow_at = 0.0                   # counters and statistics change at the end of a cycle
            self._later(60, a)

    # ── commands ─────────────────────────────────────────────────────────────
    def _check(self, a: Appliance, remote: bool) -> None:
        if a.connected is False:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="offline")
        if remote and a.params.get("remoteCtrValid") == "0":
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="remote_control_off")

    async def _send(self, a: Appliance, built: dict) -> None:
        try:
            await self.client.send(a.info, built["command"], built["parameters"], built["ancillary"],
                                   built.get("options"), built.get("program_name", ""), built.get("display_name", ""))
        except HonError as err:
            raise self._error(err) from err
        self._later(4, a)
        self._later(20, a)

    def _error(self, err: HonError) -> HomeAssistantError:
        if isinstance(err, HonAuthError):
            self.config_entry.async_start_reauth(self.hass)
            return HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_auth")
        if isinstance(err, HonConnectionError):
            return HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect")
        if isinstance(err, HonCommandError):
            return HomeAssistantError(translation_domain=DOMAIN, translation_key="command_rejected")
        return HomeAssistantError(translation_domain=DOMAIN, translation_key="cloud_error",
                                  translation_placeholders={"error": str(err)})

    async def async_start_program(self, a: Appliance, program: dict, options: dict[str, Any], *,
                                  validate_only: bool = False) -> dict[str, Any]:
        """Start a program with the given options. validate_only: build the command and do not send it."""
        if a.is_hob:
            # the official app sends hob recipes in a format of its own (one command per zone, zone bitmap):
            # until that is reproduced exactly, nothing is sent to an appliance that heats
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="hob_not_supported")
        try:
            built = model.build_start(a.commands, program["key"], {k: str(v) for k, v in options.items()})
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                         translation_placeholders={"error": str(err)}) from err
        result = {"program": program["name"], "program_key": program["key"], "parameters": built["parameters"],
                  "sent": False}
        if validate_only:
            return result
        self._check(a, remote=True)
        await self._send(a, {**built, "display_name": program["name"]})
        return {**result, "sent": True}

    async def async_simple(self, a: Appliance, name: str) -> None:
        """Pause, resume or stop."""
        if name not in SIMPLE_COMMANDS:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                         translation_placeholders={"error": name})
        try:
            built = model.build_simple(a.commands, name)
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                         translation_placeholders={"error": str(err)}) from err
        self._check(a, remote=True)
        await self._send(a, built)

    async def async_set_setting(self, a: Appliance, key: str, value: Any) -> None:
        """Change one setting of the machine. The others are sent with their current value, like the app."""
        self._check(a, remote=False)
        a.refresh()
        try:
            built = model.build_settings(a.commands, a.params, {key: str(value)})
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                         translation_placeholders={"error": str(err)}) from err
        await self._send(a, built)
        a.set_pending({key: built["parameters"][key]})
        self.async_update_listeners()

    async def async_reset_counter(self, a: Appliance, counter: str) -> None:
        how = a.resettable.get(counter)
        if how is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                         translation_placeholders={"error": counter})
        try:
            if how == "notification":
                await self.client.clear_filter_cleaning(a.info)
            else:
                await self.client.reset_counter(a.info, counter)
            a.maintenance = await self.client.maintenance(a.info)
        except HonError as err:
            raise self._error(err) from err
        self.async_update_listeners()

    async def async_set_tabs(self, a: Appliance, amount: int) -> None:
        try:
            await self.client.set_tabs(a.info, int(amount))
            a.maintenance = await self.client.maintenance(a.info)
        except HonError as err:
            raise self._error(err) from err
        self.async_update_listeners()
