"""Switches: on/off program options and on/off settings of the machine."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance import param_kind
from .coordinator import HonConfigEntry
from .entity import HonParamEntity


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    for a in coordinator.appliances.values():
        if a.is_cycle and a.programs:
            entities += [HonParamSwitch(coordinator, a, "option", key)
                         for key, kinds in a.option_kinds().items() if "switch" in kinds]
        entities += [HonParamSwitch(coordinator, a, "setting", d["key"]) for d in a.settings if param_kind(d) == "switch"]
    async_add_entities(entities)


class HonParamSwitch(HonParamEntity, SwitchEntity):
    @property
    def available(self) -> bool:
        d = self.definition
        return super().available and d is not None and param_kind(d) == "switch"

    @property
    def is_on(self) -> bool | None:
        value = self.value
        return None if value is None else str(value) == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set("1")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set("0")
