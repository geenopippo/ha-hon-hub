"""Buttons: start the chosen program, pause / resume / stop, reset the maintenance counters."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance import Appliance
from .const import DOMAIN, SIMPLE_COMMANDS
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity
from .sensor import COUNTER_KEYS

SIMPLE_KEYS = {"pauseProgram": "pause", "resumeProgram": "resume", "stopProgram": "stop"}


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    for a in coordinator.appliances.values():
        if a.is_cycle and a.programs:
            entities.append(HonStartButton(coordinator, a))
        entities += [HonSimpleButton(coordinator, a, c) for c in SIMPLE_COMMANDS if c in a.simple_commands]
        entities += [HonResetButton(coordinator, a, c) for c in a.resettable if c in COUNTER_KEYS]
    async_add_entities(entities)


class HonStartButton(HonEntity, ButtonEntity):
    """Start the program chosen in the select, with the options set in the option entities."""

    _attr_translation_key = "start"
    needs_online = True

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance) -> None:
        super().__init__(coordinator, appliance, "start")

    async def async_press(self) -> None:
        a = self.appliance
        program = a.program(a.draft_key)
        if program is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_program")
        await self.coordinator.async_start_program(a, program, a.draft_options())


class HonSimpleButton(HonEntity, ButtonEntity):
    needs_online = True

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, command: str) -> None:
        super().__init__(coordinator, appliance, command)
        self._command = command
        self._attr_translation_key = SIMPLE_KEYS[command]

    async def async_press(self) -> None:
        await self.coordinator.async_simple(self.appliance, self._command)


class HonResetButton(HonEntity, ButtonEntity):
    """Tell hOn that a maintenance operation has been done: its counter starts again."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, counter: str) -> None:
        super().__init__(coordinator, appliance, f"reset_{counter}")
        self._counter = counter
        self._attr_translation_key = f"reset_{COUNTER_KEYS[counter]}"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_counter(self.appliance, self._counter)
