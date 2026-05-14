from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MowerCoordinator

_LOGGER = logging.getLogger(__name__)

_ACTIVITY_GOING_OUT = 2
_ACTIVITY_MOWING = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ParkUntilNextStartButton(coordinator, entry),
        ResumeScheduleButton(coordinator, entry),
    ])


class _MowerButton(CoordinatorEntity[MowerCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available


class ParkUntilNextStartButton(_MowerButton):
    """Set park-until-next-start override; recalls mower if it is out mowing."""

    _attr_name = "Park Until Next Start"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_park_until_next_start"

    async def async_press(self) -> None:
        mower = self.coordinator.mower
        data = self.coordinator.data
        out_mowing = data is not None and data.activity in (
            _ACTIVITY_GOING_OUT,
            _ACTIVITY_MOWING,
        )
        await mower.send_command("SetOverrideParkUntilNextStart")
        if out_mowing:
            await mower.send_command("StartTrigger")
        await self.coordinator.async_request_refresh()


class ResumeScheduleButton(_MowerButton):
    """Clear any active override and return to the normal mowing schedule."""

    _attr_name = "Resume Schedule"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_resume_schedule"

    async def async_press(self) -> None:
        await self.coordinator.mower.send_command("ClearOverride")
        await self.coordinator.async_request_refresh()
