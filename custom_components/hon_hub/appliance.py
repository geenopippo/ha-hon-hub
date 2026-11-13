"""One appliance: the data read from the cloud and everything derived from it.

No Home Assistant imports: this module is tested standalone.
"""
from __future__ import annotations

import time
from typing import Any

from . import model
from .const import (ACTIVE_STATES, CYCLE_TYPES, DRY_LEVELS, HOB, LEVELS, MACH_MODE, MAINTENANCE, PENDING_TTL,
                    PHASE_DISH, PHASE_DRYER, PHASE_WASH, RESETTABLE, WASHING)


def to_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def param_values(definition: dict) -> list[str]:
    """The values a parameter can take, when they are few enough to be listed."""
    if definition.get("type") == "enum":
        return [str(v) for v in definition.get("values") or []]
    if definition.get("type") == "range":
        low, high, step = definition.get("min"), definition.get("max"), definition.get("step") or 1
        if all(isinstance(x, int) for x in (low, high, step)) and 0 < (high - low) // step <= 10:
            return [str(v) for v in range(low, high + 1, step)]
    return []


def param_kind(definition: dict) -> str | None:
    """How an option is shown: 'switch', 'number' or 'select' (None = not editable)."""
    kind, key = definition.get("type"), definition.get("key")
    if kind == "range":
        if key in LEVELS:
            # a few levels with a name each (little / normal / very dirty) read better as a list than as a number
            return "select" if param_values(definition) else "number"
        return "switch" if (definition.get("min"), definition.get("max")) == (0, 1) else "number"
    if kind == "enum":
        on_off = sorted(definition.get("values") or []) == ["0", "1"]
        return "switch" if on_off and key not in LEVELS else "select"
    return None


class Appliance:
    """State of one appliance. The coordinator feeds it, the entities read it."""

    def __init__(self, info: dict) -> None:
        self.info = info
        self.commands: dict = {}
        self.desc: dict = {"programs": [], "settings": None, "simple": []}
        self.connected: bool | None = None
        self.activity: dict = {}             # current activity of the cloud (program started from an app)
        self.last_command: dict = {}         # last command received by the cloud
        self.maintenance: dict = {}
        self.statistics: dict = {}
        self.params: dict[str, str] = {}     # current parameters: what everybody reads
        self.end_at: float | None = None     # expected end of the cycle (epoch seconds)
        self.slow_at = 0.0                   # last read of maintenance and statistics
        self.context_at = 0.0
        self.live_messages = 0
        self._base: dict[str, str] = {}      # shadow read with REST + real-time updates
        self._live_at: dict[str, float] = {}
        self._presence_at = 0.0
        self._pending: dict[str, tuple[str, float]] = {}   # settings just sent: value and when, until confirmed
        self._end_key: tuple | None = None
        self.seen: set[str] = set()          # every parameter name ever received: it decides which entities exist
        self._by_key: dict[str, dict] = {}
        self._labels: dict[str, dict] = {}
        # program chosen in Home Assistant and its options, waiting for "start"
        self.draft_key = ""
        self.draft_values: dict[str, str] = {}
        self._draft_defs: dict[str, dict] = {}
        self._draft_forced: set[str] = set()   # options currently locked by a rule of the program

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def mac(self) -> str:
        return self.info["macAddress"]

    @property
    def type(self) -> str:
        return self.info.get("applianceTypeName") or ""

    @property
    def nickname(self) -> str:
        return (self.info.get("nickName") or "").strip()

    @property
    def model_name(self) -> str:
        return (self.info.get("modelName") or "").strip()

    @property
    def brand(self) -> str:
        return (self.info.get("brand") or "").strip().capitalize()

    @property
    def is_cycle(self) -> bool:
        return self.type in CYCLE_TYPES

    @property
    def is_hob(self) -> bool:
        return self.type == HOB

    @property
    def zones(self) -> int:
        if not self.is_hob:
            return 0
        n = to_int(self.info.get("zone"))
        if n:
            return n
        return max((to_int(k[len("onOffStatusZ"):], 0) or 0 for k in self._base if k.startswith("onOffStatusZ")),
                   default=0)

    # ── updates ──────────────────────────────────────────────────────────────
    def set_commands(self, commands: dict, i18n: dict | None = None) -> None:
        self.commands = commands or {}
        self.desc = model.describe(self.info, self.commands, i18n or {})
        self._by_key = {p["key"].upper(): p for p in self.desc["programs"]}
        self._labels = self._make_labels()
        if self.draft_key and self.draft_key.upper() not in self._by_key:
            self.draft_key, self.draft_values, self._draft_defs = "", {}, {}
        elif self.draft_key:
            self._apply_draft()

    def apply_context(self, ctx: dict, requested_at: float | None = None) -> None:
        """Result of commands/v1/context. Values received in real time after the request win."""
        if not ctx:
            return                            # an empty answer must not wipe what we know
        requested_at = requested_at or time.time()
        shadow = model.flat_params(ctx)
        if shadow:
            fresh = {k: v for k, v in self._base.items() if self._live_at.get(k, 0) > requested_at}
            self._base = {**{k: str(v) for k, v in shadow.items() if v is not None}, **fresh}
            self._live_at = {k: t for k, t in self._live_at.items() if k in fresh}
        event = ((ctx.get("lastConnEvent") or {}).get("category") or "").upper()
        if event and self._presence_at <= requested_at:
            self.connected = event != "DISCONNECTED"
        self.activity = ctx.get("activity") if isinstance(ctx.get("activity"), dict) else {}
        history = ctx.get("commandHistory") if isinstance(ctx.get("commandHistory"), dict) else {}
        self.last_command = history.get("command") if isinstance(history.get("command"), dict) else {}
        self.context_at = time.time()
        self._rebuild()

    def apply_live(self, params: dict[str, str]) -> None:
        """Parameters received on the real-time channel."""
        now = time.time()
        for key, value in params.items():
            self._base[key] = str(value)
            self._live_at[key] = now
            self._pending.pop(key, None)      # the machine itself says what the value is now
        if self.connected is not True:        # it is talking, so it is online, whatever an older answer says
            self.connected = True
            self._presence_at = now
        self.live_messages += 1
        self._rebuild()

    def apply_presence(self, connected: bool) -> None:
        self.connected = connected
        self._presence_at = time.time()
        self._rebuild()

    def set_pending(self, values: dict[str, str]) -> None:
        now = time.time()
        self._pending.update({k: (str(v), now) for k, v in values.items()})
        self._rebuild()

    def drop_pending(self, keys) -> None:
        """The command was not sent after all: show the real values again."""
        for key in keys:
            self._pending.pop(key, None)
        self._rebuild()

    def _rebuild(self) -> None:
        now = time.time()
        self._pending = {k: (v, t) for k, (v, t) in self._pending.items() if now - t <= PENDING_TTL}
        self.params = {**self._base, **{k: v for k, (v, _) in self._pending.items()}}
        self.seen.update(self.params)
        self._track_end()

    def refresh(self) -> None:
        """Re-evaluate what depends on the clock (expired pending settings)."""
        self._rebuild()

    def _track_end(self) -> None:
        status, remaining = self.status, self.remaining
        if status not in ("running", "scheduled") or remaining <= 0:
            self.end_at, self._end_key = None, None
            return
        key = (status, remaining)
        if key == self._end_key and self.end_at is not None:
            return      # same figures as before (some phases do not count down): the estimate does not move
        self._end_key = key
        target = time.time() + remaining * 60
        # the machine counts minutes: one minute less after one minute is the same end, keep the previous estimate
        if self.end_at is None or abs(self.end_at - target) > 90:
            self.end_at = round(target / 60) * 60

    # ── derived state ────────────────────────────────────────────────────────
    @property
    def error_code(self) -> str | None:
        """Error code shown by the machine, None when there is no error."""
        keys = ["errors", "error"] + [f"{k}Z{z}" for z in range(1, self.zones + 1) for k in ("errors", "error")]
        for key in keys:
            code = (self.params.get(key) or "").strip()
            if code.strip("0"):
                return code
        return None

    @property
    def machine_status(self) -> str:
        """What the last data received say, even if the machine is offline now."""
        if self.is_hob:
            return "on" if any(self.params.get(f"onOffStatusZ{z}") == "1" for z in range(1, self.zones + 1)) else "off"
        if self.error_code:
            return "error"
        return MACH_MODE.get(to_int(self.params.get("machMode"), 0), "ready")

    @property
    def status(self) -> str:
        return "offline" if self.connected is False else self.machine_status

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATES

    @property
    def remaining(self) -> int:
        """Minutes to the end of the cycle, including the delayed start."""
        minutes = to_int(self.params.get("remainingTimeMM"), 0) or 0
        if to_int(self.params.get("machMode")) in (4, 5):
            minutes += to_int(self.params.get("delayTime"), 0) or 0
        return max(minutes, 0)

    @property
    def phase(self) -> str | None:
        if not self.active:
            return None
        code = to_int(self.params.get("prPhase"), 0)
        if self.type == "DW":
            table = PHASE_DISH
        elif self.type == "TD":
            table = PHASE_DRYER
        elif self.type in WASHING:
            table = PHASE_WASH
        else:
            return None
        if self.status == "scheduled":
            return "scheduled"
        return table.get(code)

    @property
    def dry_level(self) -> str | None:
        return DRY_LEVELS.get(to_int(self.params.get("dryLevel"), -1)) if "dryLevel" in self.params else None

    # ── programs ─────────────────────────────────────────────────────────────
    @property
    def programs(self) -> list[dict]:
        return self.desc.get("programs") or []

    def program(self, key: str) -> dict | None:
        return self._by_key.get((key or "").upper())

    def find_program(self, text: str) -> dict | None:
        """Program by key (PROGRAMS.X.Y), code (y) or name as shown in the app; case-insensitive."""
        wanted = (text or "").strip().lower()
        if not wanted:
            return None
        if (p := self.program(wanted)) is not None:
            return p
        for p in self.programs:
            if wanted in (p["id"].lower(), p["name"].lower()):
                return p
        for label, p in self.program_labels().items():
            if wanted == label.lower():
                return p
        return None

    def program_labels(self) -> dict[str, dict]:
        """Readable, unique label -> program. Programs with the same name get the code in brackets."""
        return self._labels

    def program_label(self, key: str) -> str | None:
        return next((label for label, p in self._labels.items() if p["key"] == key), None) if key else None

    def _make_labels(self) -> dict[str, dict]:
        startable = [p for p in self.programs if p.get("remote", True)]     # the others only from the machine
        count: dict[str, int] = {}
        for p in startable:
            count[p["name"]] = count.get(p["name"], 0) + 1
        out: dict[str, dict] = {}
        for p in startable:
            label = p["name"] if count[p["name"]] == 1 else f"{p['name']} ({p['id']})"
            while label in out:
                label += " *"
            out[label] = p
        return out

    @property
    def current_program(self) -> dict | None:
        """The program the machine is set on, worked out from prCode/prPosition.

        Many app programs share the code of a program of the dial. If the cloud has an activity for the same
        code (cycle started from an app or from here) its name wins, otherwise it is the dial program.
        """
        code = self.params.get("prCode")
        if code in (None, "", "0") or not self.programs:
            return None
        position = self.params.get("prPosition")
        same = [p for p in self.programs if str(p.get("prCode")) == str(code)]
        if not same:
            return None
        placed = [p for p in same if str(p["fixed"].get("prPosition")) == str(position)] or same
        attrs = self.activity.get("attributes") if isinstance(self.activity.get("attributes"), dict) else {}
        ttl = to_int(self.activity.get("ttl"), 0) or 0
        if attrs.get("programName") and str(attrs.get("prCode")) == str(code) and (not ttl or ttl > time.time()):
            name = str(attrs["programName"]).upper()
            for p in placed:
                if name in (p["key"].upper(), p["id"].upper(), p["key"].upper().split(".")[-1]):
                    return p
        dial = [p for p in placed if not p["id"].startswith("iot_")]
        return (dial or placed)[0]

    def note_started(self, program: dict) -> None:
        """A start was sent from here: until the cloud tells its own version, this is the program running.

        Without it the first real-time update would name the dial program that shares the same code."""
        self.activity = {"category": "CYCLE", "ttl": int(time.time()) + 3600, "attributes": {
            "prCode": str(program.get("prCode")), "programName": program["key"], "channel": "homeAssistant"}}

    @property
    def program_name(self) -> str | None:
        if not self.active:
            return None
        p = self.current_program
        return p["name"] if p else None

    # ── program to start (draft) ─────────────────────────────────────────────
    def select_program(self, key: str) -> None:
        p = self.program(key)
        if p is None:
            raise ValueError("Program not found")
        self.draft_key = p["key"]
        self.draft_values = {}
        self._draft_forced = set()
        self._apply_draft()

    def _apply_draft(self) -> None:
        p = self.program(self.draft_key)
        if p is None:
            self._draft_defs = {}
            return
        defs = {d["key"]: d for d in p["params"]}

        def locked(effective: dict) -> set[str]:
            return {k for k, d in effective.items() if d.get("type") == "fixed"}

        effective, values = model.apply_rules(defs, {k: v for k, v in self.draft_values.items() if k in defs}, p["rules"])
        released = self._draft_forced - locked(effective)
        if released:
            # an option a rule had locked is free again: it goes back to the default of the program, like in the
            # app (drying turned on -> the level is the default one, not the "no drying" the rule had forced)
            for key in released:
                values[key] = defs[key].get("default") if key in defs else None
            effective, values = model.apply_rules(defs, values, p["rules"])
        if str(values.get("dryOption")) == "0":
            values.pop("dryTime", None)       # drying off: forget its duration, so turning it on again starts clean
        self._draft_forced = locked(effective)
        self._draft_defs = effective
        # optional parameters (timed drying, water hardness) have no value until the user gives one
        self.draft_values = {k: str(v) for k, v in values.items() if v is not None}

    def option(self, key: str) -> dict | None:
        """Definition of an option for the chosen program, after its rules. None = not available."""
        d = self._draft_defs.get(key)
        if key == "dryTime" and self.draft_values.get("dryOption") == "0":
            return None                       # no drying asked: its duration makes no sense
        return d if d and d.get("type") != "fixed" else None

    def set_option(self, key: str, value: Any) -> None:
        d = self.option(key)
        if d is None:
            raise ValueError(f"{key}: option not available for this program")
        self.draft_values[key] = model._coerce(d, value)   # noqa: SLF001
        self._apply_draft()

    def draft_options(self) -> dict[str, str]:
        """Options to send with the chosen program (only the editable ones)."""
        return {k: v for k, v in self.draft_values.items() if self.option(k) is not None}

    def option_kinds(self) -> dict[str, set[str]]:
        """Every option of every program -> how it can show up (switch / number / select)."""
        kinds: dict[str, set[str]] = {}
        for p in self.programs:
            for d in [*p["params"], *(r["set"] for r in p["rules"])]:
                if (kind := param_kind(d)) is not None:
                    kinds.setdefault(d["key"], set()).add(kind)
        return kinds

    # ── settings and maintenance ─────────────────────────────────────────────
    @property
    def settings(self) -> list[dict]:
        return (self.desc.get("settings") or {}).get("params") or []

    @property
    def simple_commands(self) -> list[str]:
        return self.desc.get("simple") or []

    def counter(self, name: str) -> dict | None:
        c = self.maintenance.get(name)
        return c if isinstance(c, dict) and "remaining" in c else None

    @property
    def counters(self) -> list[str]:
        return [n for n in MAINTENANCE if self.counter(n) is not None]

    @property
    def resettable(self) -> dict[str, str]:
        return {n: how for n, how in RESETTABLE.get(self.type, {}).items() if self.counter(n) is not None}

    @property
    def maintenance_due(self) -> bool | None:
        counters = [self.counter(n) for n in self.counters]
        if not counters:
            return None
        return any((to_int(c.get("remaining"), 1) or 0) <= 0 for c in counters)

    def raw(self) -> dict[str, Any]:
        """Every parameter exactly as hOn reports it, for templates that need the raw codes."""
        def short(name: Any) -> str | None:
            return str(name).split(".")[-1].lower() if name else None
        attrs = self.activity.get("attributes") if isinstance(self.activity.get("attributes"), dict) else {}
        return {**self.params, "hon_connected": self.connected,
                "hon_activity_program": short(attrs.get("programName")),
                "hon_last_program": short(self.last_command.get("programName"))}

    # ── hob ──────────────────────────────────────────────────────────────────
    def zone(self, z: int) -> dict:
        p = self.params
        online = self.connected is not False
        on = online and p.get(f"onOffStatusZ{z}") == "1"
        hours, minutes = to_int(p.get(f"remainingTimeHHZ{z}"), 0) or 0, to_int(p.get(f"remainingTimeMMZ{z}"), 0) or 0
        return {
            "on": on,
            "power": (to_int(p.get(f"powerZ{z}"), 0) or 0) if on else 0,
            "pan": p.get(f"panStatusZ{z}") == "1" if online else None,
            "hot": p.get(f"hotStatusZ{z}") == "1" if online and f"hotStatusZ{z}" in p else None,
            "temperature": to_float(p.get(f"tempZ{z}")) if online and f"tempZ{z}" in p else None,
            "timer": hours * 60 + minutes if on else 0,
        }
