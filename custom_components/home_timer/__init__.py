"""Home Timer: timers per area, with stations and presets.

This first version sets up the area: its hub device ("<area>: Home Timer") in the
area. The timer engine, stations, presets and entities follow.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from .const import CONF_AREA_ID, DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    area_id = entry.data[CONF_AREA_ID]
    if ar.async_get(hass).async_get_area(area_id) is None:
        _LOGGER.warning("%s: area '%s' no longer exists; its devices stay without an area", entry.title, area_id)
    registry = dr.async_get(hass)
    is_new = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id) is None
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{entry.title}: {NAME}",
        manufacturer=NAME,
        model="Area",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    if is_new:
        place_in_area(hass, device, area_id)
    _LOGGER.debug("%s: set up (area %s, hub device %s)", entry.title, area_id, device.id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


def place_in_area(hass: HomeAssistant, device: dr.DeviceEntry, area_id: str) -> None:
    """Put a newly created device into the area, if the area still exists.

    Only called when the device is created: afterwards its area belongs to the user
    (moved elsewhere or taken out of any area, it stays that way).
    """
    if ar.async_get(hass).async_get_area(area_id) is not None:
        dr.async_get(hass).async_update_device(device.id, area_id=area_id)
