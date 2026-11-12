"""Base entities: one Home Assistant device per appliance."""
from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .appliance import Appliance
from .const import DOMAIN, KNOWN_PARAMS, MANUFACTURER_FALLBACK, TYPE_NAMES
from .coordinator import HonCoordinator

PARAM_ICONS = {
    "temp": "mdi:thermometer", "spinSpeed": "mdi:rotate-right", "dryLevel": "mdi:tumble-dryer", "dryTime": "mdi:timer-sand",
    "dryOption": "mdi:tumble-dryer", "dirtyLevel": "mdi:liquid-spot", "steamLevel": "mdi:weather-fog",
    "delayTime": "mdi:timer-outline", "extraRinse1": "mdi:water-plus", "extraRinse2": "mdi:water-plus",
    "extraRinse3": "mdi:water-plus", "acquaplus": "mdi:water-plus", "goodNight": "mdi:weather-night",
    "prewash": "mdi:water-sync", "hygiene": "mdi:shield-check", "anticrease": "mdi:iron-outline",
    "autoDetergentStatus": "mdi:cup-water", "autoSoftenerStatus": "mdi:flower", "waterHard": "mdi:water-opacity",
    "extraDry": "mdi:hair-dryer", "openDoor": "mdi:door-open", "halfLoad": "mdi:circle-half-full",
    "ecoExpress": "mdi:leaf", "tabStatus": "mdi:pill", "intensive": "mdi:arm-flex", "lightStatus": "mdi:lightbulb",
    "buzzerDisabled": "mdi:volume-off", "lastCycleSavingStatus": "mdi:content-save",
    "multiDosingLevelRA": "mdi:shimmer", "powerManagement": "mdi:flash", "nightWashStatus": "mdi:weather-night",
}


class HonEntity(CoordinatorEntity[HonCoordinator]):
    """An entity of one appliance."""

    _attr_has_entity_name = True
    needs_online = False     # controls need the machine online; sensors keep the last known value

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, key: str) -> None:
        super().__init__(coordinator)
        self.appliance = appliance
        self._attr_unique_id = f"{appliance.mac}_{key}"
        kind = TYPE_NAMES.get(appliance.type)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, appliance.mac)},
            name=appliance.nickname or (None if kind else appliance.model_name or appliance.type or "hOn"),
            translation_key=None if appliance.nickname else kind,
            manufacturer=appliance.brand or MANUFACTURER_FALLBACK,
            model=appliance.model_name or None,
            sw_version=appliance.info.get("fwVersion"),
            serial_number=appliance.info.get("serialNumber"),
        )

    @property
    def available(self) -> bool:
        return super().available and (not self.needs_online or self.appliance.connected is not False)


class HonParamEntity(HonEntity):
    """An entity for a program option ('option') or a setting of the machine ('setting')."""

    _attr_entity_category = EntityCategory.CONFIG
    needs_online = True

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, role: str, key: str) -> None:
        super().__init__(coordinator, appliance, f"{role}_{key}")
        self.role = role
        self.param = key
        if key in KNOWN_PARAMS:
            self._attr_translation_key = f"{role}_{KNOWN_PARAMS[key]}"
        else:
            # a parameter this integration has never seen: it keeps its hOn name and starts disabled
            self._attr_name = key
            self._attr_entity_registry_enabled_default = False
        self._attr_icon = PARAM_ICONS.get(key, "mdi:tune-variant")

    @property
    def definition(self) -> dict | None:
        """Current definition of the parameter (None = not available right now)."""
        if self.role == "option":
            return self.appliance.option(self.param)
        return next((d for d in self.appliance.settings if d["key"] == self.param), None)

    @property
    def value(self) -> str | None:
        if self.role == "option":
            return self.appliance.draft_values.get(self.param) if self.definition else None
        return self.appliance.params.get(self.param)

    async def async_set(self, value) -> None:
        if self.role == "option":
            try:
                self.appliance.set_option(self.param, value)
            except ValueError as err:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_option",
                                             translation_placeholders={"error": str(err)}) from err
            self.coordinator.async_update_listeners()      # the other options may depend on this one
        else:
            await self.coordinator.async_set_setting(self.appliance, self.param, value)
