from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerData

_LOGGER = logging.getLogger(__name__)

_ACTIVITY_NAMES: dict[int, str] = {
    0: "None",
    1: "Charging",
    2: "Going Out",
    3: "Mowing",
    4: "Going Home",
    5: "Parked",
    6: "Stopped in Garden",
}

_STATE_NAMES: dict[int, str] = {
    0: "Off",
    1: "Wait for Safety Pin",
    2: "Stopped",
    3: "Fatal Error",
    4: "Pending Start",
    5: "Paused",
    6: "In Operation",
    7: "Restricted",
    8: "Error",
}

_MODE_NAMES: dict[int, str] = {
    0: "Auto",
    1: "Manual",
    2: "Home",
    3: "Demo",
}


@dataclass(frozen=True)
class MowerSensorEntityDescription(SensorEntityDescription):
    """Sensor description with optional integer-to-string value map."""

    value_map: dict[int, str] | None = field(default=None, compare=False)


SENSOR_DESCRIPTIONS: tuple[MowerSensorEntityDescription, ...] = (
    MowerSensorEntityDescription(
        key="battery_level",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorEntityDescription(
        key="remaining_charge_time",
        name="Remaining charge time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="min",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorEntityDescription(
        key="activity",
        name="Activity",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_map=_ACTIVITY_NAMES,
    ),
    MowerSensorEntityDescription(
        key="state",
        name="State",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_map=_STATE_NAMES,
    ),
    MowerSensorEntityDescription(
        key="mode",
        name="Mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_map=_MODE_NAMES,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MowerSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class MowerSensor(CoordinatorEntity[MowerCoordinator], SensorEntity):
    """Sensor entity for the Husqvarna Automower."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MowerCoordinator,
        entry: ConfigEntry,
        description: MowerSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    @property
    def native_value(self) -> int | str | None:
        data: MowerData | None = self.coordinator.data
        if data is None:
            return None
        value = getattr(data, self.entity_description.key, None)
        if value is None:
            return None
        value_map = self.entity_description.value_map
        if value_map is not None:
            return value_map.get(value, str(value))
        return value

    @property
    def available(self) -> bool:
        if not self.coordinator.connection_available:
            return False
        if self.entity_description.key == "remaining_charge_time":
            data = self.coordinator.data
            return data is not None and bool(data.is_charging)
        return True
