import asyncio
import logging
from dataclasses import dataclass, replace

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pyam3.client import WifiSerialMower

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class MowerData:
    battery_level: int | None = None
    is_charging: bool | None = None
    remaining_charge_time: int | None = None  # raw value from mower
    state: int | None = None     # raw uint8 from GetState
    activity: int | None = None  # raw uint8 from GetActivity
    mode: int | None = None      # raw uint8 from GetMode
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
        mower.on_event(self._handle_event)

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
                self.mower.send_command("GetError"),
                return_exceptions=True,
            )
        except Exception as err:
            raise UpdateFailed(f"Error communicating with mower: {err}") from err

        battery_level, is_charging, remaining_charge_time, state, activity, mode, error_code = results

        def _val(v, cast=None):
            if isinstance(v, Exception) or v is None:
                return None
            return cast(v) if cast else v

        self._data = MowerData(
            battery_level=_val(battery_level, int),
            is_charging=_val(is_charging, bool),
            remaining_charge_time=_val(remaining_charge_time, int),
            state=_val(state, int),
            activity=_val(activity, int),
            mode=_val(mode, int),
            error_code=_val(error_code, int),
        )
        return self._data
