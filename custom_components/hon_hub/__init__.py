"""hOn Hub: Candy, Hoover and Haier appliances of the hOn app, straight from the hOn cloud.

Real-time updates on the same channel as the official app, programs with their options, maintenance.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .appliance import Appliance
from .const import DOMAIN
from .coordinator import HonConfigEntry, HonCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.NUMBER, Platform.SELECT,
             Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_START_PROGRAM = "start_program"
START_SCHEMA = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("program"): cv.string,
    vol.Optional("options", default=dict): vol.Schema({cv.string: vol.Any(str, int, float, bool)}),
    vol.Optional("validate_only", default=False): cv.boolean,
})


def _find(hass: HomeAssistant, device_id: str) -> tuple[HonCoordinator, Appliance]:
    """The appliance behind a Home Assistant device."""
    device = dr.async_get(hass).async_get(device_id)
    mac = next((i[1] for i in (device.identifiers if device else ()) if i[0] == DOMAIN), None)
    if mac is not None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED and mac in entry.runtime_data.appliances:
                return entry.runtime_data, entry.runtime_data.appliances[mac]
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_device")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def start_program(call: ServiceCall) -> ServiceResponse:
        coordinator, appliance = _find(hass, call.data["device_id"])
        program = appliance.find_program(call.data["program"])
        if program is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="program_not_found",
                                         translation_placeholders={"program": call.data["program"]})
        options: dict[str, Any] = {k: (int(v) if isinstance(v, bool) else v) for k, v in call.data["options"].items()}
        result = await coordinator.async_start_program(appliance, program, options,
                                                       validate_only=call.data["validate_only"])
        return result if call.return_response else None

    hass.services.async_register(DOMAIN, SERVICE_START_PROGRAM, start_program, schema=START_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry) -> bool:
    if CONF_EMAIL not in entry.data:
        # entry of the first version, which talked to the hOn Hub web app: ask for the hOn account
        raise ConfigEntryAuthFailed("hOn account needed")
    coordinator = HonCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_realtime()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HonConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: HonConfigEntry, device: dr.DeviceEntry) -> bool:
    """Allow deleting the device of an appliance that is no longer in the account."""
    coordinator: HonCoordinator | None = getattr(entry, "runtime_data", None)
    known = coordinator.appliances if coordinator else {}
    return not any(i[0] == DOMAIN and i[1] in known for i in device.identifiers)
