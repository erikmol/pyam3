import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from pyam3.client import WifiSerialMower

from .const import CONNECTION_STALE_TIMEOUT, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class MowerData:
    battery_level: int | None = None
    is_charging: bool | None = None
    remaining_charge_time: int | None = None  # minutes (raw wire value is seconds)
    state: int | None = None     # raw uint8 from GetState
    activity: int | None = None  # raw uint8 from GetActivity
    mode: int | None = None      # raw uint8 from GetMode
    override: int | None = None  # raw uint8 from GetOverride (OverrideAction)
    error_code: int | None = None


class MowerCoordinator(DataUpdateCoordinator[MowerData]):
    """Coordinator polling mower state every SCAN_INTERVAL seconds."""

    def __init__(self, hass: HomeAssistant, mower: WifiSerialMower) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.mower = mower
        self._data = MowerData()
        self._last_contact: datetime | None = None
        mower.on_event(self._handle_event)

    @property
    def connection_available(self) -> bool:
        """True if the mower was reachable within the stale timeout window."""
        if self._last_contact is None:
            return False
        return (dt_util.utcnow() - self._last_contact) < CONNECTION_STALE_TIMEOUT

    @callback
    def _handle_event(self, event: dict) -> None:
        """Handle push events from the linked protocol (state/activity changes)."""
        name = event.get("name")
        data = event.get("data")
        if not data:
            return

        updated = False
        if name == "StateEvent":
            self._data = replace(self._data, state=data.get("response"))
            updated = True
        elif name == "ActivityEvent":
            self._data = replace(self._data, activity=data.get("response"))
            updated = True

        if updated:
            self._last_contact = dt_util.utcnow()
            self.async_set_updated_data(self._data)

    async def _async_update_data(self) -> MowerData:
        try:
            results = await asyncio.gather(
                self.mower.send_command("GetBatteryLevel"),
                self.mower.send_command("IsCharging"),
                self.mower.send_command("GetRemainingChargingTime"),
                self.mower.send_command("GetState"),
                self.mower.send_command("GetActivity"),
                self.mower.send_command("GetMode"),
                self.mower.send_command("GetOverride"),
                self.mower.send_command("GetError"),
                return_exceptions=True,
            )
        except Exception as err:
            _LOGGER.debug("Error communicating with mower: %s", err)
            return self._data

        # If every command failed, the mower is unreachable — keep cached data.
        if all(isinstance(r, Exception) for r in results):
            _LOGGER.debug("All mower commands failed, keeping last known data")
            return self._data

        old = self._data

        def _val(v, cast, old_val):
            if isinstance(v, Exception) or v is None:
                return old_val
            return cast(v) if cast else v

        battery_level, is_charging, remaining_charge_time, state, activity, mode, override, error_code = results

        self._last_contact = dt_util.utcnow()
        self._data = MowerData(
            battery_level=_val(battery_level, int, old.battery_level),
            is_charging=_val(is_charging, bool, old.is_charging),
            remaining_charge_time=_val(remaining_charge_time, lambda v: int(v) // 60, old.remaining_charge_time),
            state=_val(state, int, old.state),
            activity=_val(activity, int, old.activity),
            mode=_val(mode, int, old.mode),
            override=_val(override, int, old.override),
            error_code=_val(error_code, int, old.error_code),
        )
        return self._data
