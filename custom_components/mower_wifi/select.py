from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MowerCoordinator

_LOGGER = logging.getLogger(__name__)

_MODE_AUTO = 0
_MODE_HOME = 2
_MODE_DEMO = 3

_OPTION_TO_MODE: dict[str, int] = {
    "auto": _MODE_AUTO,
    "home": _MODE_HOME,
    "demo": _MODE_DEMO,
}
_MODE_TO_OPTION: dict[int, str] = {v: k for k, v in _OPTION_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MowerModeSelect(coordinator, entry)])


class MowerModeSelect(CoordinatorEntity[MowerCoordinator], SelectEntity):
    """Select entity to get/set the mower's ModeOfOperation."""

    _attr_has_entity_name = True
    _attr_name = "Operating mode"
    _attr_options = list(_OPTION_TO_MODE.keys())

    def __init__(self, coordinator: MowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_mode_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    @property
    def available(self) -> bool:
        return self.coordinator.connection_available

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        if data is None or data.mode is None:
            return None
        return _MODE_TO_OPTION.get(data.mode)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.mower.send_command("SetMode", mode=_OPTION_TO_MODE[option])
        await self.coordinator.async_request_refresh()
