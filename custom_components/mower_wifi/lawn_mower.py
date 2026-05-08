import logging

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MOW_DURATION, DOMAIN
from .coordinator import MowerCoordinator

_LOGGER = logging.getLogger(__name__)

# MowerState enum (GetState / StateEvent, uint8)
_STATE_OFF = 0
_STATE_WAIT_FOR_SAFETYPIN = 1
_STATE_STOPPED = 2       # stopped, requires manual action
_STATE_FATAL_ERROR = 3
_STATE_PENDING_START = 4
_STATE_PAUSED = 5        # paused by user
_STATE_IN_OPERATION = 6  # see activity for details
_STATE_RESTRICTED = 7    # calendar/override park restriction
_STATE_ERROR = 8         # error, check error code

# MowerActivity enum (GetActivity / ActivityEvent, uint8)
_ACTIVITY_NONE = 0
_ACTIVITY_CHARGING = 1
_ACTIVITY_GOING_OUT = 2     # leaving charging station to mow
_ACTIVITY_MOWING = 3
_ACTIVITY_GOING_HOME = 4    # returning to charging station
_ACTIVITY_PARKED = 5
_ACTIVITY_STOPPED_IN_GARDEN = 6  # stopped in garden, needs manual action

# ModeOfOperation enum (GetMode, uint8)
_MODE_AUTO = 0
_MODE_MANUAL = 1
_MODE_HOME = 2   # parked forever, no schedule
_MODE_DEMO = 3

# OverrideAction enum (GetOverride, uint8)
_OVERRIDE_NONE = 0
_OVERRIDE_FORCEDPARK = 1
_OVERRIDE_FORCEDMOW = 2


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowerLawnMower(coordinator, entry)])


class MowerLawnMower(CoordinatorEntity[MowerCoordinator], LawnMowerEntity):
    """Lawn mower entity representing the Husqvarna Automower."""

    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.DOCK
        | LawnMowerEntityFeature.PAUSE
    )
    _attr_has_entity_name = True
    _attr_name = None  # use device name as entity name

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Husqvarna",
            "model": "Automower (WiFi)",
        }

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available

    @property
    def activity(self) -> LawnMowerActivity | None:
        data = self.coordinator.data
        if data is None:
            return None

        error_code = data.error_code
        state = data.state
        activity = data.activity

        if (error_code and error_code != 0) or state in (
            _STATE_FATAL_ERROR,
            _STATE_ERROR,
            _STATE_WAIT_FOR_SAFETYPIN,
        ):
            return LawnMowerActivity.ERROR

        if activity in (_ACTIVITY_GOING_OUT, _ACTIVITY_MOWING):
            return LawnMowerActivity.MOWING

        if activity == _ACTIVITY_GOING_HOME:
            return LawnMowerActivity.RETURNING

        if state in (_STATE_PAUSED, _STATE_STOPPED) or activity == _ACTIVITY_STOPPED_IN_GARDEN:
            return LawnMowerActivity.PAUSED

        if activity in (_ACTIVITY_CHARGING, _ACTIVITY_PARKED) or state in (
            _STATE_RESTRICTED,
            _STATE_PENDING_START,
            _STATE_OFF,
        ):
            return LawnMowerActivity.DOCKED

        return LawnMowerActivity.DOCKED

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_start_mowing(self) -> None:
        mower = self.coordinator.mower
        data = self.coordinator.data
        if data is not None and data.state == _STATE_PAUSED:
            # Mower is paused mid-mow — StartTrigger resumes without disturbing mode/override.
            await mower.send_command("StartTrigger")
        else:
            await mower.send_command("SetMode", mode=_MODE_AUTO)
            await mower.send_command("SetOverrideMow", duration=DEFAULT_MOW_DURATION)
            # StartTrigger response is expected to return a non-OK status — handled gracefully
            await mower.send_command("StartTrigger")
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        """Park until next scheduled start, then trigger."""
        mower = self.coordinator.mower
        await mower.send_command("SetOverrideParkUntilNextStart")
        await mower.send_command("StartTrigger")
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        await self.coordinator.mower.send_command("Pause")
        await self.coordinator.async_request_refresh()
