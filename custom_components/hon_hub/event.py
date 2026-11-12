"""Event entity: start, pause, end and error of a cycle, to trigger automations without watching states."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance import Appliance
from .const import CYCLE_EVENTS
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HonCycleEvent(coordinator, a) for a in coordinator.appliances.values() if a.is_cycle)


class HonCycleEvent(HonEntity, EventEntity):
    _attr_translation_key = "cycle"
    _attr_event_types = CYCLE_EVENTS

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance) -> None:
        super().__init__(coordinator, appliance, "cycle")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_cycle_listener(self.appliance.mac, self._fire))

    @callback
    def _fire(self, event: str, data: dict[str, Any]) -> None:
        self._trigger_event(event, data)
        self.async_write_ha_state()
