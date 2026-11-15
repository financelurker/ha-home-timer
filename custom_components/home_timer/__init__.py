"""Home Timer: timers per area, with stations and presets.

This first version sets up the area: its hub device ("<area>: Home Timer") in the
area. The timer engine, stations, presets and entities follow.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_AREA_ID, DOMAIN, NAME


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{entry.title}: {NAME}",
        manufacturer=NAME,
        model="Area",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    _assign_area(hass, entry, device)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


def _assign_area(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry) -> None:
    """Put the hub device into the entry's area, unless the user moved it."""
    if device.area_id is None:
        dr.async_get(hass).async_update_device(device.id, area_id=entry.data[CONF_AREA_ID])
