"""Binary sensors: connection, remote control, door, salt, rinse aid, key lock, residual heat, maintenance."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass, BinarySensorEntity,
                                                    BinarySensorEntityDescription)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .appliance import Appliance
from .coordinator import HonConfigEntry, HonCoordinator
from .entity import HonEntity


@dataclass(frozen=True, kw_only=True)
class HonBinaryDescription(BinarySensorEntityDescription):
    is_on: Callable[[Appliance], bool | None]
    exists: Callable[[Appliance], bool] = lambda a: True


def _flag(key: str, on: str = "1") -> Callable[[Appliance], bool | None]:
    return lambda a: a.params[key] == on if key in a.params else None


def _has(*keys: str) -> Callable[[Appliance], bool]:
    return lambda a: any(k in a.params for k in keys)


def _lock(a: Appliance) -> bool | None:
    """Device class lock: on means unlocked."""
    for key in ("hobLockStatus", "lockStatus"):
        if key in a.params:
            return a.params[key] != "1"
    return None


BINARY_SENSORS: tuple[HonBinaryDescription, ...] = (
    HonBinaryDescription(key="connected", translation_key="connected", device_class=BinarySensorDeviceClass.CONNECTIVITY,
                         entity_category=EntityCategory.DIAGNOSTIC, is_on=lambda a: a.connected),
    HonBinaryDescription(key="remote_control", translation_key="remote_control", exists=_has("remoteCtrValid"),
                         is_on=lambda a: a.connected is not False and a.params.get("remoteCtrValid") == "1"),
    HonBinaryDescription(key="door", translation_key="door", device_class=BinarySensorDeviceClass.DOOR,
                         exists=_has("doorStatus"), is_on=_flag("doorStatus")),
    HonBinaryDescription(key="salt", translation_key="salt", device_class=BinarySensorDeviceClass.PROBLEM,
                         exists=_has("saltStatus"), is_on=_flag("saltStatus")),
    HonBinaryDescription(key="rinse_aid", translation_key="rinse_aid", device_class=BinarySensorDeviceClass.PROBLEM,
                         exists=_has("rinseAidStatus"), is_on=_flag("rinseAidStatus")),
    HonBinaryDescription(key="detergent", translation_key="detergent", device_class=BinarySensorDeviceClass.PROBLEM,
                         entity_registry_enabled_default=False, exists=_has("detWarn"), is_on=_flag("detWarn")),
    HonBinaryDescription(key="softener", translation_key="softener", device_class=BinarySensorDeviceClass.PROBLEM,
                         entity_registry_enabled_default=False, exists=_has("softWarn"), is_on=_flag("softWarn")),
    HonBinaryDescription(key="key_lock", translation_key="key_lock", device_class=BinarySensorDeviceClass.LOCK,
                         exists=lambda a: a.is_hob and _has("hobLockStatus", "lockStatus")(a), is_on=_lock),
    HonBinaryDescription(key="maintenance", translation_key="maintenance", device_class=BinarySensorDeviceClass.PROBLEM,
                         exists=lambda a: bool(a.counters), is_on=lambda a: a.maintenance_due),
)


async def async_setup_entry(hass: HomeAssistant, entry: HonConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for a in coordinator.appliances.values():
        entities += [HonBinarySensor(coordinator, a, d) for d in BINARY_SENSORS if d.exists(a)]
        entities += [HonZoneHot(coordinator, a, z) for z in range(1, a.zones + 1) if f"hotStatusZ{z}" in a.params]
    async_add_entities(entities)


class HonBinarySensor(HonEntity, BinarySensorEntity):
    entity_description: HonBinaryDescription

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, description: HonBinaryDescription) -> None:
        super().__init__(coordinator, appliance, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on(self.appliance)


class HonZoneHot(HonEntity, BinarySensorEntity):
    """Residual heat of one zone of the hob."""

    _attr_translation_key = "zone_hot"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coordinator: HonCoordinator, appliance: Appliance, zone: int) -> None:
        super().__init__(coordinator, appliance, f"zone_{zone}_hot")
        self._zone = zone
        self._attr_translation_placeholders = {"zone": str(zone)}

    @property
    def is_on(self) -> bool | None:
        return self.appliance.zone(self._zone)["hot"]
