"""Numbers: numeric program options, numeric settings and the dishwasher tablets in the pantry."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance import Appliance, param_kind, to_float, to_int
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity, HonParamEntity

MINUTES = ("delayTime", "mainWashTime")


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for a in coordinator.appliances.values():
        if a.is_cycle and a.programs:
            entities += [HonParamNumber(coordinator, a, "option", key)
                         for key, kinds in a.option_kinds().items() if "number" in kinds]
        entities += [HonParamNumber(coordinator, a, "setting", d["key"]) for d in a.settings if param_kind(d) == "number"]
        if a.maintenance.get("tabs") is not None:
            entities.append(HonTabsNumber(coordinator, a))
    async_add_entities(entities)


class HonParamNumber(HonParamEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, role: str, key: str) -> None:
        super().__init__(coordinator, appliance, role, key)
        if key in MINUTES:
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def _range(self) -> dict | None:
        d = self.definition
        return d if d and param_kind(d) == "number" else None

    @property
    def available(self) -> bool:
        return super().available and self._range is not None

    @property
    def native_min_value(self) -> float:
        return float((self._range or {}).get("min", 0))

    @property
    def native_max_value(self) -> float:
        return float((self._range or {}).get("max", 100))

    @property
    def native_step(self) -> float:
        return float((self._range or {}).get("step", 1) or 1)

    @property
    def native_value(self) -> float | None:
        return to_float(self.value)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_set(int(value) if float(value).is_integer() else value)


class HonTabsNumber(HonEntity, NumberEntity):
    """How many tablets are left: set it when you buy a new box, hOn counts down at every cycle."""

    _attr_translation_key = "tabs"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 999
    _attr_native_step = 1

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance) -> None:
        super().__init__(coordinator, appliance, "set_tabs")

    @property
    def native_value(self) -> int | None:
        return to_int(self.appliance.maintenance.get("tabs"))

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_tabs(self.appliance, int(value))
