"""Binary sensor of an area: any of its timers rings."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import STATUS_RINGING
from .entity import AreaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RingingSensor(entry.runtime_data, "ringing")])


class RingingSensor(AreaEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return any(t.status == STATUS_RINGING for t in self.engine.timers.values())
