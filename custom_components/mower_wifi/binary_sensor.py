from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MowerCoordinator, MowerData
from .mower_error import MowerError

_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_DESCRIPTIONS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="is_charging",
        name="Charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

_ERROR_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="error_code",
    name="Error",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        MowerBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    ]
    entities.append(MowerErrorBinarySensor(coordinator, entry, _ERROR_SENSOR_DESCRIPTION))
    async_add_entities(entities)


class MowerBinarySensor(CoordinatorEntity[MowerCoordinator], BinarySensorEntity):
    """Binary sensor entity for the Husqvarna Automower."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MowerCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available

    @property
    def is_on(self) -> bool | None:
        data: MowerData | None = self.coordinator.data
        if data is None:
            return None
        value = getattr(data, self.entity_description.key, None)
        return bool(value) if value is not None else None


class MowerErrorBinarySensor(CoordinatorEntity[MowerCoordinator], BinarySensorEntity):
    """Binary sensor that is on when the mower reports a non-zero error code."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MowerCoordinator,
        entry: ConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available

    @property
    def is_on(self) -> bool | None:
        data: MowerData | None = self.coordinator.data
        if data is None or data.error_code is None:
            return None
        return data.error_code != 0

    @property
    def extra_state_attributes(self) -> dict:
        data: MowerData | None = self.coordinator.data
        if data is None or data.error_code is None:
            return {}
        try:
            return {"error_code": MowerError(data.error_code).name}
        except ValueError:
            return {"error_code": data.error_code}
