"""Base classes: the area's entities (on the hub device) and a station's entities."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .engine import AreaTimers, Timer
from .stations import Station


class AreaEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: AreaTimers, key: str) -> None:
        self.engine = engine
        self._attr_translation_key = key
        self._attr_unique_id = f"{engine.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, engine.entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.engine.async_add_listener(self.async_write_ha_state))


class StationEntity(Entity):
    """An entity of a station's device; the station's timer is ``self.timer``."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: AreaTimers, station: Station, key: str) -> None:
        self.engine = engine
        self.station = station
        self._attr_translation_key = key
        self._attr_unique_id = f"{station.id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, station.id)})

    @property
    def timer(self) -> Timer | None:
        return self.engine.timer_at(self.station.id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.engine.async_add_listener(self.async_write_ha_state))


@callback
def remove_orphans(hass: HomeAssistant, entry_id: str, domain: str, wanted: set[str]) -> None:
    """Remove this entry's entities of a platform that it no longer has (deleted preset,
    a station switched between by hand and by power …)."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry_id):
        if entity.domain == domain and entity.platform == DOMAIN and entity.unique_id not in wanted:
            registry.async_remove(entity.entity_id)
