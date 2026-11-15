"""The timer engine of one area: timers, their life cycle, storage and events.

A timer is active (counting down), paused or ringing (ran out, waiting to be
acknowledged). Cancelled and acknowledged timers are removed – no timer is "idle".

Every change is stored (end time, not remaining time, so a restart loses nothing):
- an active timer that ran out while Home Assistant was down finishes right after
  the start, with ``late: true`` (or is dropped, if "report missed timers" is off),
- a ringing timer keeps ringing; its reminders and auto-dismiss continue from the
  time it ran out,
- a paused timer stays paused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import (
    BY_TIMEOUT,
    CONF_AREA_ID,
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_REPORT_MISSED,
    CONF_RING_UNTIL_ACK,
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_ACKNOWLEDGED,
    EVENT_CANCELLED,
    EVENT_FINISHED,
    EVENT_PAUSED,
    EVENT_REMINDER,
    EVENT_RESUMED,
    EVENT_STARTED,
    EVENT_TIME_CHANGED,
    REASON_MANUAL,
    REASON_REPLACED,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_RINGING,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class TimerError(Exception):
    """An operation that does not fit the timer's state (translated by the actions)."""

    def __init__(self, key: str, **placeholders: str) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders


@dataclass
class Timer:
    id: str
    name: str
    duration: float  # seconds in total, including added or subtracted time
    status: str
    started_at: datetime
    ends_at: datetime | None = None  # active
    paused_remaining: float | None = None  # paused
    finished_at: datetime | None = None  # ringing
    station: str | None = None
    preset: str | None = None
    note: str | None = None
    source: dict[str, Any] = field(default_factory=dict)

    def remaining(self, now: datetime) -> float:
        if self.status == STATUS_ACTIVE and self.ends_at is not None:
            return max(0.0, (self.ends_at - now).total_seconds())
        if self.status == STATUS_PAUSED and self.paused_remaining is not None:
            return self.paused_remaining
        return 0.0

    def overtime(self, now: datetime) -> float:
        if self.status == STATUS_RINGING and self.finished_at is not None:
            return max(0.0, (now - self.finished_at).total_seconds())
        return 0.0

    def as_dict(self, now: datetime) -> dict[str, Any]:
        """The timer as shown in attributes and action responses."""
        return {
            "id": self.id,
            "name": self.name,
            "station": self.station,
            "preset": self.preset,
            "status": self.status,
            "duration": round(self.duration),
            "remaining": round(self.remaining(now)),
            "started_at": self.started_at.isoformat(),
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "overtime": round(self.overtime(now)),
            "note": self.note,
            "source": self.source,
        }

    def to_store(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "paused_remaining": self.paused_remaining,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "station": self.station,
            "preset": self.preset,
            "note": self.note,
            "source": self.source,
        }

    @classmethod
    def from_store(cls, data: dict[str, Any]) -> Timer:
        def stamp(key: str) -> datetime | None:
            value = data.get(key)
            return dt_util.parse_datetime(value) if value else None

        return cls(
            id=data["id"],
            name=data["name"],
            duration=float(data["duration"]),
            status=data["status"],
            started_at=stamp("started_at") or dt_util.utcnow(),
            ends_at=stamp("ends_at"),
            paused_remaining=data.get("paused_remaining"),
            finished_at=stamp("finished_at"),
            station=data.get("station"),
            preset=data.get("preset"),
            note=data.get("note"),
            source=data.get("source") or {},
        )


class AreaTimers:
    """All timers of one area (config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.area_id: str = entry.data[CONF_AREA_ID]
        self.timers: dict[str, Timer] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._scheduled: dict[str, list[CALLBACK_TYPE]] = {}
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------- settings

    def option(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULT_OPTIONS[key])

    @property
    def hub_device_id(self) -> str | None:
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, self.entry.entry_id), self.entry.entry_id
        )
        return device.id if device else None

    # ------------------------------------------------------------ life cycle

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        now = dt_util.utcnow()
        for raw in data.get("timers", []):
            try:
                timer = Timer.from_store(raw)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("%s: skipping an unreadable stored timer: %s", self.entry.title, raw)
                continue
            self.timers[timer.id] = timer
        for timer in list(self.timers.values()):
            if timer.status == STATUS_ACTIVE and timer.ends_at is not None and timer.ends_at <= now:
                if self.option(CONF_REPORT_MISSED):
                    self._finish(timer, late=True)
                else:
                    _LOGGER.debug("%s: dropping '%s', it ran out during the restart", self.entry.title, timer.name)
                    self._remove(timer)
            else:
                self._schedule(timer)
        self._changed()

    async def async_unload(self) -> None:
        for timer_id in list(self._scheduled):
            self._unschedule(timer_id)
        self._listeners.clear()
        await self._store.async_save(self._data())

    # ---------------------------------------------------------- operations

    @callback
    def start(
        self,
        name: str,
        duration: float,
        *,
        station: str | None = None,
        preset: str | None = None,
        note: str | None = None,
        source: dict[str, Any] | None = None,
    ) -> Timer:
        if duration <= 0:
            raise TimerError("invalid_duration")
        if station is not None:
            for other in list(self.timers.values()):
                if other.station == station:
                    self.cancel(other.id, reason=REASON_REPLACED)
        now = dt_util.utcnow()
        timer = Timer(
            id=ulid_now(),
            name=name,
            duration=float(duration),
            status=STATUS_ACTIVE,
            started_at=now,
            ends_at=now + timedelta(seconds=duration),
            station=station,
            preset=preset,
            note=note,
            source=dict(source or {}),
        )
        self.timers[timer.id] = timer
        self._schedule(timer)
        self._fire(EVENT_STARTED, timer)
        self._changed()
        return timer

    @callback
    def pause(self, timer_id: str) -> Timer:
        timer = self.get(timer_id)
        if timer.status != STATUS_ACTIVE:
            raise TimerError("not_active", name=timer.name)
        timer.paused_remaining = timer.remaining(dt_util.utcnow())
        timer.ends_at = None
        timer.status = STATUS_PAUSED
        self._unschedule(timer.id)
        self._fire(EVENT_PAUSED, timer)
        self._changed()
        return timer

    @callback
    def resume(self, timer_id: str) -> Timer:
        timer = self.get(timer_id)
        if timer.status != STATUS_PAUSED:
            raise TimerError("not_paused", name=timer.name)
        timer.ends_at = dt_util.utcnow() + timedelta(seconds=timer.paused_remaining or 0)
        timer.paused_remaining = None
        timer.status = STATUS_ACTIVE
        self._schedule(timer)
        self._fire(EVENT_RESUMED, timer)
        self._changed()
        return timer

    @callback
    def add_time(self, timer_id: str, seconds: float) -> Timer:
        """Add (or, with a negative value, subtract) time. Down to zero finishes the timer.

        A ringing timer starts again with the added time.
        """
        timer = self.get(timer_id)
        now = dt_util.utcnow()
        if timer.status == STATUS_RINGING:
            if seconds <= 0:
                raise TimerError("ringing", name=timer.name)
            self._unschedule(timer.id)
            timer.status = STATUS_ACTIVE
            timer.finished_at = None
            timer.ends_at = now + timedelta(seconds=seconds)
        elif timer.status == STATUS_PAUSED:
            timer.paused_remaining = max(0.0, (timer.paused_remaining or 0) + seconds)
        else:
            timer.ends_at = (timer.ends_at or now) + timedelta(seconds=seconds)
        timer.duration = max(0.0, timer.duration + seconds)
        self._fire(EVENT_TIME_CHANGED, timer, change=round(seconds))
        if timer.status == STATUS_PAUSED and not timer.paused_remaining:
            timer.status = STATUS_ACTIVE
            timer.ends_at = now
            timer.paused_remaining = None
        if timer.status == STATUS_ACTIVE and timer.ends_at is not None and timer.ends_at <= now:
            self._unschedule(timer.id)
            self._finish(timer)
        elif timer.status == STATUS_ACTIVE:
            self._schedule(timer)
        self._changed()
        return timer

    @callback
    def cancel(self, timer_id: str, *, reason: str = REASON_MANUAL) -> None:
        timer = self.get(timer_id)
        self._remove(timer)
        self._fire(EVENT_CANCELLED, timer, reason=reason)
        self._changed()

    @callback
    def acknowledge(self, timer_id: str, *, by: str) -> None:
        timer = self.get(timer_id)
        if timer.status != STATUS_RINGING:
            raise TimerError("not_ringing", name=timer.name)
        overtime = timer.overtime(dt_util.utcnow())
        self._remove(timer)
        self._fire(EVENT_ACKNOWLEDGED, timer, by=by, overtime=round(overtime))
        self._changed()

    @callback
    def acknowledge_all(self, *, by: str) -> int:
        ringing = [t for t in self.timers.values() if t.status == STATUS_RINGING]
        for timer in ringing:
            self.acknowledge(timer.id, by=by)
        return len(ringing)

    def get(self, timer_id: str) -> Timer:
        try:
            return self.timers[timer_id]
        except KeyError:
            raise TimerError("unknown_timer", timer_id=timer_id) from None

    # ------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # ------------------------------------------------------------- internals

    def _finish(self, timer: Timer, *, late: bool = False) -> None:
        finished_at = timer.ends_at or dt_util.utcnow()
        timer.ends_at = None
        timer.paused_remaining = None
        if not self.option(CONF_RING_UNTIL_ACK):
            self._remove(timer)
            timer.status = STATUS_RINGING
            timer.finished_at = finished_at
            self._fire(EVENT_FINISHED, timer, late=late)
            return
        timer.status = STATUS_RINGING
        timer.finished_at = finished_at
        self._fire(EVENT_FINISHED, timer, late=late)
        self._schedule(timer)

    def _remove(self, timer: Timer) -> None:
        self._unschedule(timer.id)
        self.timers.pop(timer.id, None)

    def _schedule(self, timer: Timer) -> None:
        """(Re)schedule what comes next for the timer: its end, or reminder and auto-dismiss."""
        self._unschedule(timer.id)
        now = dt_util.utcnow()
        jobs: list[CALLBACK_TYPE] = []
        if timer.status == STATUS_ACTIVE and timer.ends_at is not None:
            jobs.append(async_track_point_in_utc_time(self.hass, self._ended_cb(timer.id), timer.ends_at))
        elif timer.status == STATUS_RINGING and timer.finished_at is not None:
            dismiss = self.option(CONF_AUTO_DISMISS_MINUTES)
            if dismiss:
                at = timer.finished_at + timedelta(minutes=dismiss)
                if at <= now:
                    self.acknowledge(timer.id, by=BY_TIMEOUT)
                    return
                jobs.append(async_track_point_in_utc_time(self.hass, self._dismiss_cb(timer.id), at))
            interval = self.option(CONF_REMINDER_MINUTES)
            if interval:
                step = timedelta(minutes=interval)
                count = int((now - timer.finished_at) / step) + 1
                at = timer.finished_at + step * count
                jobs.append(async_track_point_in_utc_time(self.hass, self._reminder_cb(timer.id), at))
        if jobs:
            self._scheduled[timer.id] = jobs

    def _unschedule(self, timer_id: str) -> None:
        for cancel in self._scheduled.pop(timer_id, []):
            cancel()

    def _ended_cb(self, timer_id: str) -> Callable[[datetime], None]:
        @callback
        def ended(_now: datetime) -> None:
            timer = self.timers.get(timer_id)
            if timer is None or timer.status != STATUS_ACTIVE:
                return
            self._scheduled.pop(timer_id, None)
            self._finish(timer)
            self._changed()

        return ended

    def _reminder_cb(self, timer_id: str) -> Callable[[datetime], None]:
        @callback
        def remind(now: datetime) -> None:
            timer = self.timers.get(timer_id)
            if timer is None or timer.status != STATUS_RINGING:
                return
            self._fire(EVENT_REMINDER, timer, overtime=round(timer.overtime(dt_util.utcnow())))
            self._schedule(timer)
            self._notify()

        return remind

    def _dismiss_cb(self, timer_id: str) -> Callable[[datetime], None]:
        @callback
        def dismiss(_now: datetime) -> None:
            if timer_id in self.timers and self.timers[timer_id].status == STATUS_RINGING:
                self.acknowledge(timer_id, by=BY_TIMEOUT)

        return dismiss

    def event_data(self, timer: Timer, **extra: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "timer_id": timer.id,
            "name": timer.name,
            "area_id": self.area_id,
            "entry_id": self.entry.entry_id,
            "station": timer.station,
            "station_name": timer.station,
            "device_id": self.hub_device_id,
            "preset": timer.preset,
            "duration": round(timer.duration),
            "note": timer.note,
            "source": timer.source,
        }
        data.update(extra)
        return data

    def _fire(self, event_type: str, timer: Timer, **extra: Any) -> None:
        self.hass.bus.async_fire(event_type, self.event_data(timer, **extra))

    def _data(self) -> dict[str, Any]:
        return {"timers": [t.to_store() for t in self.timers.values()]}

    def _changed(self) -> None:
        self._store.async_delay_save(self._data, 1)
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"
