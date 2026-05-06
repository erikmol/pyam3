from __future__ import annotations

import logging
from dataclasses import dataclass

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


@dataclass(frozen=True)
class MowerSensorEntityDescription(SensorEntityDescription):
    """Sensor description with optional availability check."""


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
    ),
    MowerSensorEntityDescription(
        key="state",
        name="State",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    MowerSensorEntityDescription(
        key="mode",
        name="Mode",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    def native_value(self) -> int | None:
        data: MowerData | None = self.coordinator.data
        if data is None:
            return None
        return getattr(data, self.entity_description.key, None)

    @property
    def available(self) -> bool:
        if not self.coordinator.connection_available:
            return False
        if self.entity_description.key == "remaining_charge_time":
            data = self.coordinator.data
            return data is not None and bool(data.is_charging)
        return True
