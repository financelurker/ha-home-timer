"""Base class of the area's entities: on the hub device, updated by the engine."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .engine import AreaTimers


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
