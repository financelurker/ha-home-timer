"""Home Timer: timers per area, with stations and presets.

Each config entry is an area: its hub device ("Home Timer") in the area, the
area's timer engine (engine.py) and the area's entities. Actions are registered once
for all areas (services.py).
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import CONF_AREA_ID, DOMAIN, NAME, STORAGE_VERSION
from .engine import AreaTimers, storage_key
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HomeTimerConfigEntry = ConfigEntry[AreaTimers]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HomeTimerConfigEntry) -> bool:
    area_id = entry.data[CONF_AREA_ID]
    if ar.async_get(hass).async_get_area(area_id) is None:
        _LOGGER.warning("%s: area '%s' no longer exists; its devices stay without an area", entry.title, area_id)
    registry = dr.async_get(hass)
    is_new = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id) is None
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,  # the area is the context: entity ids become sensor.<area>_home_timer_…
        manufacturer=NAME,
        model="Area",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    if is_new:
        place_in_area(hass, device, area_id)

    engine = AreaTimers(hass, entry)
    await engine.async_load()
    entry.runtime_data = engine
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await engine.async_unload()
        raise
    # timers are scheduled, and missed ones reported, once automations listen
    entry.async_on_unload(async_at_started(hass, engine.async_start))
    _LOGGER.debug("%s: set up (area %s, %d stored timers)", entry.title, area_id, len(engine.timers))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeTimerConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_unload()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: HomeTimerConfigEntry) -> None:
    """The area is deleted: its stored timers go with it."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()


def place_in_area(hass: HomeAssistant, device: dr.DeviceEntry, area_id: str) -> None:
    """Put a newly created device into the area, if the area still exists.

    Only called when the device is created: afterwards its area belongs to the user
    (moved elsewhere or taken out of any area, it stays that way).
    """
    if ar.async_get(hass).async_get_area(area_id) is not None:
        dr.async_get(hass).async_update_device(device.id, area_id=area_id)
