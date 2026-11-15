"""Sensor of an area: how many timers there are, and all of them as an attribute."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HomeTimerConfigEntry
from .const import STATUS_ACTIVE, STATUS_RINGING
from .entity import AreaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([TimersSensor(entry.runtime_data, "timers")])


class TimersSensor(AreaEntity, SensorEntity):
    """How many timers the area has (active, paused or ringing); ``timers`` lists them."""

    _unrecorded_attributes = frozenset({"timers"})

    @property
    def native_value(self) -> int:
        return len(self.engine.timers)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        timers = sorted(self.engine.timers.values(), key=lambda t: (t.status != STATUS_RINGING, t.remaining(now)))
        active = [t for t in timers if t.status == STATUS_ACTIVE and t.ends_at is not None]
        nxt = min(active, key=lambda t: t.ends_at) if active else None
        return {
            "ringing": sum(1 for t in timers if t.status == STATUS_RINGING),
            "next_end": nxt.ends_at.isoformat() if nxt else None,
            "next_name": nxt.name if nxt else None,
            "timers": [t.as_dict(now) for t in timers],
        }
