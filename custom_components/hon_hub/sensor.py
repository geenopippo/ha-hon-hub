"""Sensors: state, program, phase, times, consumption, maintenance counters, hob zones."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import (MATCH_ALL, REVOLUTIONS_PER_MINUTE, EntityCategory, UnitOfEnergy, UnitOfTemperature,
                                 UnitOfTime, UnitOfVolume)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .appliance import Appliance, to_float, to_int
from .const import DRY_LEVELS, PHASES, STATES, STATES_HOB, WASHING
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity

# icon of the state sensor while the machine is idle (the other states have their own icon in icons.json)
IDLE_ICONS = {"DW": "mdi:dishwasher", "TD": "mdi:tumble-dryer", "WM": "mdi:washing-machine", "WD": "mdi:washing-machine"}
COUNTER_KEYS = {"filterCleaning": "filter_cleaning", "drumCleaning": "drum_cleaning",
                "drawerCleaning": "drawer_cleaning", "sprayArmsCleaning": "spray_arms_cleaning"}


@dataclass(frozen=True, kw_only=True)
class HonSensorDescription(SensorEntityDescription):
    value: Callable[[Appliance], Any]
    exists: Callable[[Appliance], bool] = lambda a: True
    attributes: Callable[[Appliance], dict[str, Any]] | None = None


def _active(key: str, cast: Callable[[Any], Any] = to_int) -> Callable[[Appliance], Any]:
    return lambda a: cast(a.params.get(key)) if a.active else None


def _program_attributes(a: Appliance) -> dict[str, Any]:
    p = a.current_program if a.active else None
    return {"program_key": p["key"] if p else None, "program_code": a.params.get("prCode")}


SENSORS: tuple[HonSensorDescription, ...] = (
    HonSensorDescription(key="status", translation_key="status", device_class=SensorDeviceClass.ENUM, options=STATES,
                         exists=lambda a: not a.is_hob, value=lambda a: a.status,
                         attributes=lambda a: {"error_code": a.error_code}),
    HonSensorDescription(key="status", translation_key="hob_status", device_class=SensorDeviceClass.ENUM,
                         options=STATES_HOB, exists=lambda a: a.is_hob, value=lambda a: a.status),
    HonSensorDescription(key="program", translation_key="program", exists=lambda a: a.is_cycle and bool(a.programs),
                         value=lambda a: a.program_name, attributes=_program_attributes),
    HonSensorDescription(key="phase", translation_key="phase", device_class=SensorDeviceClass.ENUM, options=PHASES,
                         exists=lambda a: a.is_cycle, value=lambda a: a.phase),
    HonSensorDescription(key="remaining_time", translation_key="remaining_time", device_class=SensorDeviceClass.DURATION,
                         native_unit_of_measurement=UnitOfTime.MINUTES, exists=lambda a: a.is_cycle,
                         value=lambda a: a.remaining if a.active else 0),
    HonSensorDescription(key="end_time", translation_key="end_time", device_class=SensorDeviceClass.TIMESTAMP,
                         exists=lambda a: a.is_cycle,
                         value=lambda a: dt_util.utc_from_timestamp(a.end_at) if a.end_at else None),
    HonSensorDescription(key="cycle_water", translation_key="cycle_water", device_class=SensorDeviceClass.WATER,
                         native_unit_of_measurement=UnitOfVolume.LITERS, state_class=SensorStateClass.TOTAL_INCREASING,
                         suggested_display_precision=1, exists=lambda a: "currentWaterUsed" in a.params,
                         value=lambda a: to_float(a.params.get("currentWaterUsed"))),
    HonSensorDescription(key="cycle_energy", translation_key="cycle_energy", device_class=SensorDeviceClass.ENERGY,
                         native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                         state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=2,
                         exists=lambda a: "currentElectricityUsed" in a.params,
                         value=lambda a: to_float(a.params.get("currentElectricityUsed"))),
    HonSensorDescription(key="total_cycles", translation_key="total_cycles", state_class=SensorStateClass.TOTAL_INCREASING,
                         exists=lambda a: a.is_cycle, value=lambda a: to_int(a.statistics.get("programsCounter"))),
    HonSensorDescription(key="temperature", translation_key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                         native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                         exists=lambda a: a.type in WASHING and "temp" in a.params, value=_active("temp")),
    HonSensorDescription(key="spin_speed", translation_key="spin_speed", native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
                         exists=lambda a: "spinSpeed" in a.params, value=_active("spinSpeed")),
    HonSensorDescription(key="dry_level", translation_key="dry_level", device_class=SensorDeviceClass.ENUM,
                         options=sorted(set(DRY_LEVELS.values())),
                         exists=lambda a: a.type in ("WD", "TD") and "dryLevel" in a.params,
                         value=lambda a: a.dry_level if a.active else None),
    HonSensorDescription(key="error_code", translation_key="error_code", entity_category=EntityCategory.DIAGNOSTIC,
                         exists=lambda a: a.is_cycle or a.is_hob, value=lambda a: a.error_code),
    HonSensorDescription(key="tabs", translation_key="tabs", exists=lambda a: a.maintenance.get("tabs") is not None,
                         value=lambda a: to_int(a.maintenance.get("tabs"))),
)


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for a in coordinator.appliances.values():
        entities += [HonSensor(coordinator, a, d) for d in SENSORS if d.exists(a)]
        entities += [HonCounterSensor(coordinator, a, name) for name in a.counters if name in COUNTER_KEYS]
        entities += [HonZoneSensor(coordinator, a, z) for z in range(1, a.zones + 1)]
        entities += [HonZoneTemperature(coordinator, a, z) for z in range(1, a.zones + 1) if f"tempZ{z}" in a.params]
        entities.append(HonRawSensor(coordinator, a))
    async_add_entities(entities)


class HonSensor(HonEntity, SensorEntity):
    entity_description: HonSensorDescription

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, description: HonSensorDescription) -> None:
        super().__init__(coordinator, appliance, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | datetime | None:
        return self.entity_description.value(self.appliance)

    @property
    def icon(self) -> str | None:
        if self.entity_description.key == "status" and self.appliance.status == "ready":
            return IDLE_ICONS.get(self.appliance.type)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attributes
        return fn(self.appliance) if fn else None


class HonCounterSensor(HonEntity, SensorEntity):
    """Cycles left before a maintenance operation is due (negative = overdue)."""

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, name: str) -> None:
        super().__init__(coordinator, appliance, f"maintenance_{name}")
        self._name = name
        self._attr_translation_key = COUNTER_KEYS[name]

    @property
    def native_value(self) -> int | None:
        return to_int((self.appliance.counter(self._name) or {}).get("remaining"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.appliance.counter(self._name) or {}
        return {"cycles_done": c.get("count"), "interval": c.get("tot"), "percentage": c.get("percentage"),
                "due": (to_int(c.get("remaining"), 1) or 0) <= 0}


class HonZoneSensor(HonEntity, SensorEntity):
    """Power level of one zone of the hob (0 = off)."""

    _attr_translation_key = "zone_power"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, zone: int) -> None:
        super().__init__(coordinator, appliance, f"zone_{zone}")
        self._zone = zone
        self._attr_translation_placeholders = {"zone": str(zone)}

    @property
    def native_value(self) -> int:
        return self.appliance.zone(self._zone)["power"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.appliance.zone(self._zone)
        return {"on": z["on"], "pan_detected": z["pan"], "timer_minutes": z["timer"]}


class HonZoneTemperature(HonEntity, SensorEntity):
    """Temperature that the hob reports for one zone."""

    _attr_translation_key = "zone_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, zone: int) -> None:
        super().__init__(coordinator, appliance, f"zone_{zone}_temperature")
        self._zone = zone
        self._attr_translation_placeholders = {"zone": str(zone)}

    @property
    def native_value(self) -> float | None:
        return self.appliance.zone(self._zone)["temperature"]


class HonRawSensor(HonEntity, SensorEntity):
    """Every parameter as hOn reports it, as attributes: for templates that need the raw codes.

    Disabled by default. The attributes change all the time and are kept out of the recorder."""

    _attr_translation_key = "raw_parameters"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance) -> None:
        super().__init__(coordinator, appliance, "raw_parameters")

    @property
    def native_value(self) -> int:
        return len(self.appliance.params)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.appliance.raw()
