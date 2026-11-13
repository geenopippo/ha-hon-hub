"""Selects: the program to start, its multiple-choice options and the multiple-choice settings."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .appliance import Appliance, param_kind, param_values
from .const import DOMAIN
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity, HonParamEntity


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for a in coordinator.appliances.values():
        if a.is_cycle and a.programs:
            entities.append(HonProgramSelect(coordinator, a))
            entities += [HonParamSelect(coordinator, a, "option", key)
                         for key, kinds in a.option_kinds().items() if "select" in kinds]
        entities += [HonParamSelect(coordinator, a, "setting", d["key"]) for d in a.settings if param_kind(d) == "select"]
    async_add_entities(entities)


class HonProgramSelect(HonEntity, SelectEntity, RestoreEntity):
    """The program that the start button will start. Choosing one resets its options to the defaults."""

    _attr_translation_key = "program"

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance) -> None:
        super().__init__(coordinator, appliance, "program_select")

    @property
    def options(self) -> list[str]:
        return list(self.appliance.program_labels())

    @property
    def current_option(self) -> str | None:
        return self.appliance.program_label(self.appliance.draft_key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and not self.appliance.draft_key and (p := self.appliance.program_labels().get(last.state)):
            self.appliance.select_program(p["key"])
            self.coordinator.async_update_listeners()

    async def async_select_option(self, option: str) -> None:
        program = self.appliance.program_labels().get(option)
        if program is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_program")
        self.appliance.select_program(program["key"])
        self.coordinator.async_update_listeners()


class HonParamSelect(HonParamEntity, SelectEntity):
    @property
    def _enum(self) -> dict | None:
        d = self.definition
        return d if d and param_kind(d) == "select" else None

    @property
    def available(self) -> bool:
        return super().available and self._enum is not None

    @property
    def options(self) -> list[str]:
        return param_values(self._enum) if self._enum else []

    @property
    def current_option(self) -> str | None:
        value = self.value
        return str(value) if value is not None and str(value) in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.async_set(option)
