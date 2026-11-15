"""Sensors: the area's timers, and per station its timer's state and end."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HomeTimerConfigEntry
from .const import STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING
from .entity import AreaEntity, StationEntity, remove_orphans

STATE_IDLE = "idle"


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    async_add_entities([TimersSensor(engine, "timers")])
    wanted = {f"{engine.entry.entry_id}_timers"}
    for station in engine.stations.values():
        sensors: list[SensorEntity] = [StationTimerSensor(engine, station, "station"), StationEndSensor(engine, station, "ends")]
        if station.by_power:
            sensors += [RunsSensor(engine, station, "runs"), UsualDurationSensor(engine, station, "usual_duration")]
        wanted.update(s.unique_id for s in sensors if s.unique_id)
        async_add_entities(sensors, config_subentry_id=station.id)
    remove_orphans(hass, entry.entry_id, "sensor", wanted)


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


class StationTimerSensor(StationEntity, SensorEntity):
    """The station's timer: idle, active, paused or ringing; details as attributes.

    Named like the station itself (e.g. sensor.kitchen_hob_1).
    """

    _attr_name = None
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_IDLE, STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING]
    _unrecorded_attributes = frozenset({"source", "remaining", "overtime"})

    def __init__(self, engine, station, key) -> None:
        super().__init__(engine, station, key)
        if station.icon:
            self._attr_icon = station.icon

    @property
    def native_value(self) -> str:
        timer = self.timer
        return timer.status if timer else STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timer = self.timer
        extra = self._power_attributes()
        if timer is None:
            return {"presets": [p.name for p in self.station.presets], **extra}
        info = timer.as_dict(dt_util.utcnow())
        return {
            **extra,
            "timer_id": info["id"],
            "timer_name": info["name"],
            "preset": info["preset"],
            "duration": info["duration"],
            "remaining": info["remaining"],
            "started_at": info["started_at"],
            "ends_at": info["ends_at"],
            "finished_at": info["finished_at"],
            "overtime": info["overtime"],
            "note": info["note"],
            "source": info["source"],
            "elapsed": info["elapsed"],
            "presets": [p.name for p in self.station.presets],
        }

    def _power_attributes(self) -> dict[str, Any]:
        power = self.station.power
        if power is None:
            return {}
        watcher = self.engine.watcher(self.station.id)
        attrs: dict[str, Any] = {
            "by_power": power.kind,
            "running": bool(watcher and watcher.running),
            "power_sensor": power.sensor,
        }
        if power.kind == "until_off":
            attrs["done_rule"] = power.done.describe()
        return attrs


class StationEndSensor(StationEntity, SensorEntity):
    """When the station's timer runs out (while it is active); the frontend shows "in 6 minutes"."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        timer = self.timer
        return self.engine.expected_end(timer) if timer else None


class RunsSensor(StationEntity, SensorEntity):
    """How often the power station's appliance ran."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int:
        return int(self.engine.power_stats.get(self.station.id, {}).get("runs", 0))


class UsualDurationSensor(StationEntity, SensorEntity):
    """Median duration of the last runs (until done, or until it stopped)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float | None:
        usual = self.engine.usual_duration(self.station.id)
        return round(usual) if usual is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stats = self.engine.power_stats.get(self.station.id, {})
        return {"last": stats.get("last"), "durations": stats.get("durations", [])}
