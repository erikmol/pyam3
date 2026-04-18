from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from pyam3.client import WifiSerialMower

from .const import DEFAULT_PORT, DOMAIN, PLATFORMS
from .coordinator import MowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    # Quick reachability check before spending time on full connect + first refresh
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot reach bridge at {host}:{port}") from err

    mower = WifiSerialMower(host, port)
    await mower.connect()

    coordinator = MowerCoordinator(hass, mower)

    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        await mower.disconnect()
        raise ConfigEntryNotReady from err

    await mower.send_command("SubscribeMowerAppEvents")
    await mower.send_command("SubscribePowerEvents")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: MowerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.mower.disconnect()
    return unloaded
