"""The timer engine of one area: timers, their life cycle, storage and events.

A timer is active (counting down), paused or ringing (ran out, waiting to be
acknowledged). Cancelled and acknowledged timers are removed – no timer is "idle".

Every change is stored (end time, not remaining time, so a restart loses nothing).
Loading only restores the timers; ``async_start`` – run once Home Assistant has
started, so automations listen – schedules them and reports what happened meanwhile:
- an active timer that ran out finishes with ``late: true`` (or is dropped, if
  "report missed timers" is off); its reminders and auto-dismiss count from now,
- a ringing timer keeps ringing; reminders and auto-dismiss continue from when it
  started ringing (past auto-dismiss: acknowledged with ``by: timeout``),
- with "ring until acknowledged" off, ringing timers are acknowledged (``timeout``),
- a paused timer stays paused,
- a timer of a station that no longer exists is cancelled (``station_removed``).
"""

from __future__ import annotations

import asyncio

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
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
    EVENT_ASSIGNED,
    EVENT_CANCELLED,
    EVENT_FINISHED,
    EVENT_PAUSED,
    EVENT_REMINDER,
    EVENT_RESUMED,
    EVENT_STARTED,
    EVENT_TIME_CHANGED,
    REASON_MANUAL,
    REASON_REPLACED,
    REASON_STATION_REMOVED,
    SIGNAL_TIMERS_CHANGED,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_RINGING,
    STATUSES,
    STORAGE_VERSION,
)
from .learn import forget_searches
from .runs import RunStore, async_import_history, async_refill_runs
from .stations import CONF_PRESETS, Preset, Station, area_presets, find_preset, stations

AREA = ""  # scope key of the area itself (timers without a station)
CUSTOM_NAME = "Timer"
QUICK_CUSTOM = "custom"  # Timer.quick of a custom timer (else the preset's id)
LINKED = 2  # stored "quick_linked": 1 area timers linked to their switches, 2 stations' too
DEFAULT_CUSTOM_SECONDS = 300.0

_LOGGER = logging.getLogger(__name__)
REFILL_DELAY = 30  # s after a run is stored: completing it from the history (the recorder commits in batches)


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
    finished_at: datetime | None = None  # ringing: when it ran out
    ring_anchor: datetime | None = None  # ringing: reminders and auto-dismiss count from here
    station: str | None = None
    station_name: str | None = None  # when started or assigned; kept for events after the station is gone
    preset: str | None = None
    note: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    open: bool = False  # runs until something finishes it (a power station), no end time
    # the preset (its id) or custom timer (QUICK_CUSTOM) this timer is the run of – a quick
    # timer of the area, or a preset of its station; shown by that switch. None for other timers
    quick: str | None = None

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
            "remaining": None if self.open and self.status == STATUS_ACTIVE else round(self.remaining(now)),
            "elapsed": round((now - self.started_at).total_seconds()),
            "open": self.open,
            "started_at": self.started_at.isoformat(),
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "overtime": round(self.overtime(now)),
            "note": self.note,
            "source": _copy(self.source),
        }

    def to_store(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "status": self.status,
            "started_at": iso(self.started_at),
            "ends_at": iso(self.ends_at),
            "paused_remaining": self.paused_remaining,
            "finished_at": iso(self.finished_at),
            "ring_anchor": iso(self.ring_anchor),
            "station": self.station,
            "station_name": self.station_name,
            "preset": self.preset,
            "note": self.note,
            "source": self.source,
            "open": self.open,
            "quick": self.quick,
        }

    @classmethod
    def from_store(cls, data: dict[str, Any]) -> Timer:
        """Raises ValueError (or KeyError/TypeError) for data that does not make a timer."""

        def stamp(key: str) -> datetime | None:
            value = data.get(key)
            if not value:
                return None
            parsed = dt_util.parse_datetime(value)
            if parsed is None:
                raise ValueError(f"{key}: not a timestamp")
            return dt_util.as_utc(parsed)

        status = data["status"]
        if status not in STATUSES:
            raise ValueError(f"unknown status {status}")
        timer = cls(
            id=str(data["id"]),
            name=str(data["name"]),
            duration=float(data["duration"]),
            status=status,
            started_at=stamp("started_at") or dt_util.utcnow(),
            ends_at=stamp("ends_at"),
            paused_remaining=None if data.get("paused_remaining") is None else float(data["paused_remaining"]),
            finished_at=stamp("finished_at"),
            ring_anchor=stamp("ring_anchor"),
            station=data.get("station"),
            station_name=data.get("station_name"),
            preset=data.get("preset"),
            note=data.get("note"),
            source=data.get("source") or {},
            open=bool(data.get("open", False)),
            quick=str(data["quick"]) if data.get("quick") else None,
        )
        if status == STATUS_ACTIVE and timer.ends_at is None and not timer.open:
            raise ValueError("active timer without end")
        if status == STATUS_PAUSED and timer.paused_remaining is None:
            raise ValueError("paused timer without remaining time")
        if status == STATUS_RINGING and timer.finished_at is None:
            raise ValueError("ringing timer without finish time")
        return timer


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
        self._running = False
        # per scope (AREA or a station id): the custom duration and the last custom timer started
        self.custom_seconds: dict[str, float] = {}
        self._last_custom: dict[str, dict[str, Any]] = {}
        # per power station: {"runs": n, "durations": [seconds, …] (last 20), "last": seconds}
        self.power_stats: dict[str, dict[str, Any]] = {}
        self._watchers: list[Any] = []
        self._unsub_stop: Any = None
        self._later: list[CALLBACK_TYPE] = []  # runs to complete from the history, a little later
        self.runs = RunStore(hass, entry.entry_id)  # power stations' runs with their curves
        self._imports: list[asyncio.Task[Any]] = []  # reading earlier runs from the history

    # ------------------------------------------------------------- settings

    @property
    def running(self) -> bool:
        """Started (timers scheduled) and not unloaded."""
        return self._running

    def option(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULT_OPTIONS[key])

    @property
    def hub_device_id(self) -> str | None:
        return self._device_id(self.entry.entry_id)

    @property
    def stations(self) -> dict[str, Station]:
        return stations(self.entry)

    def station_device_id(self, station_id: str) -> str | None:
        return self._device_id(station_id)

    def _device_id(self, identifier: str) -> str | None:
        device = dr.async_get(self.hass).async_get_device_by_identifier((DOMAIN, identifier), self.entry.entry_id)
        return device.id if device else None

    def timer_at(self, station_id: str) -> Timer | None:
        """The station's timer, if it has one."""
        return next((t for t in self.timers.values() if t.station == station_id), None)

    # ------------------------------------------------------------ life cycle

    async def async_load(self) -> None:
        """Restore the stored timers; nothing is scheduled or reported yet."""
        await self.runs.async_load()
        data = await self._store.async_load()
        if isinstance(data, dict):
            custom = data.get("custom")
            if isinstance(custom, dict):
                self.custom_seconds = {
                    str(k): float(v) for k, v in custom.items() if isinstance(v, (int, float)) and v > 0
                }
            stats = data.get("power_stats")
            if isinstance(stats, dict):
                self.power_stats = {str(k): v for k, v in stats.items() if isinstance(v, dict)}
            last = data.get("last_custom")
            if isinstance(last, dict):
                self._last_custom = {
                    str(k): v
                    for k, v in last.items()
                    if isinstance(v, dict) and v.get("timer_id") and isinstance(v.get("duration"), (int, float))
                }
        raw_timers = data.get("timers", []) if isinstance(data, dict) else []
        for raw in raw_timers if isinstance(raw_timers, list) else []:
            try:
                timer = Timer.from_store(raw)
            except (KeyError, TypeError, ValueError, AttributeError) as err:
                _LOGGER.warning("%s: skipping a stored timer (%s): %s", self.entry.title, err, raw)
                continue
            self.timers[timer.id] = timer
        linked = data.get("quick_linked") if isinstance(data, dict) else None
        linked = 1 if linked is True else linked if isinstance(linked, int) else 0
        if linked < 1:
            self._link_quick_timers()  # once, on the update that brought the area's switches
        if linked < 2:
            self._link_station_timers()  # once, on the update that brought the stations' switches

    def _link_quick_timers(self) -> None:
        """Timers stored before quick timers had switches: link a running quick timer (by its
        preset's name) and the last custom timer of the area to their switch – one run each,
        the newest wins; the others go on as plain timers."""
        quick = {p.name.casefold(): p.id for p in area_presets(self.entry)}
        last = self._last_custom.get(AREA, {}).get("timer_id")
        taken = {t.quick for t in self.timers.values() if t.quick and t.station is None}
        for timer in sorted(self.timers.values(), key=lambda t: t.started_at, reverse=True):
            if timer.station is not None or timer.quick:
                continue
            key = quick.get(timer.preset.casefold()) if timer.preset else None
            if key is None and timer.id == last:
                key = QUICK_CUSTOM
            if key is not None and key not in taken:
                timer.quick = key
                taken.add(key)

    def _link_station_timers(self) -> None:
        """Stations' timers stored before their presets had switches: a preset's timer (by its
        name) and the station's last custom timer are linked to their switch."""
        known = self.stations
        for timer in self.timers.values():
            station = known.get(timer.station) if timer.station else None
            if station is None or timer.quick:
                continue
            preset = station.preset(timer.preset) if timer.preset else None
            if preset is not None:
                timer.quick = preset.id
            elif timer.id == self._last_custom.get(station.id, {}).get("timer_id"):
                timer.quick = QUICK_CUSTOM

    def quick_timer(self, key: str, station_id: str | None = None) -> Timer | None:
        """The run of a preset (by id) or of the custom timer (QUICK_CUSTOM) – a quick timer
        of the area, or (``station_id``) a preset of that station – if it has one."""
        return next((t for t in self.timers.values() if t.quick == key and t.station == station_id), None)

    @callback
    def async_start(self, _hass: HomeAssistant | None = None) -> None:
        """Home Assistant has started (or the area was reloaded): schedule and catch up."""
        if self._running:
            return
        self._running = True
        now = dt_util.utcnow()
        known = self.stations
        for scope in [
            s for s in (*self.custom_seconds, *self._last_custom, *self.power_stats) if s != AREA and s not in known
        ]:
            self.custom_seconds.pop(scope, None)  # a station deleted meanwhile
            self._last_custom.pop(scope, None)
            self.power_stats.pop(scope, None)
        self.runs.prune(set(known))
        forget_searches(self.entry.entry_id, set(known))  # of stations deleted meanwhile
        before = self._data()
        for timer in list(self.timers.values()):
            if timer.id not in self.timers:
                continue
            if timer.station is not None and timer.station not in known:
                self.cancel(timer.id, reason=REASON_STATION_REMOVED)
            elif timer.status == STATUS_ACTIVE and timer.ends_at is not None and timer.ends_at <= now:
                if self.option(CONF_REPORT_MISSED):
                    self._finish(timer, late=True)
                else:
                    _LOGGER.debug("%s: dropping '%s', it ran out during the restart", self.entry.title, timer.name)
                    self._remove(timer)
            elif timer.status == STATUS_RINGING and not self.option(CONF_RING_UNTIL_ACK):
                self.acknowledge(timer.id, by=BY_TIMEOUT)
            else:
                self._schedule(timer)
        if self._data() != before:
            self._changed()  # missed timers reported, removed, acknowledged: store it
        else:
            self._notify()
        from .watcher import PowerWatcher  # noqa: PLC0415 - watcher imports the engine

        if any(station.power is not None for station in known.values()):
            # Home Assistant stops without unloading the area: keep the curves of runs going on
            self._unsub_stop = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._keep_curves)

        for station in known.values():
            if station.power is not None:
                watcher = PowerWatcher(self, station)
                self._watchers.append(watcher)
                watcher.start()
                self.runs.fill_sensor(station.id, station.power.sensor)
                self._background(async_refill_runs(self.hass, self.runs, station.id, station.power), f"complete runs of {station.name}")
                if station.id not in self.runs.imported:
                    self._imports.append(self.entry.async_create_background_task(
                        self.hass,
                        async_import_history(self.hass, self.runs, station.id, station.power),
                        f"{DOMAIN} history of {station.name}",
                    ))

    def _background(self, coro: Any, name: str) -> None:
        """A task reading the history: cancelled when the area unloads (before its store is saved)."""
        task = self.entry.async_create_background_task(self.hass, coro, f"{DOMAIN} {name}")
        self._imports.append(task)
        task.add_done_callback(lambda t: t in self._imports and self._imports.remove(t))

    @callback
    def _keep_curves(self, _event: Event) -> None:
        self._unsub_stop = None
        for watcher in self._watchers:
            watcher.keep_curve()

    async def async_unload(self) -> None:
        self._running = False
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None
        for unsub in self._later:
            unsub()
        self._later.clear()
        for task in self._imports:
            task.cancel()  # before the store is saved: a late import must not write to it
        self._imports.clear()
        for watcher in self._watchers:
            watcher.stop()
        self._watchers.clear()
        for timer_id in list(self._scheduled):
            self._unschedule(timer_id)
        self._listeners.clear()
        async_dispatcher_send(self.hass, SIGNAL_TIMERS_CHANGED)  # no longer running: leaves the to-do list
        await self._store.async_save(self._data())
        await self.runs.async_save()

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
        open_ended: bool = False,
        quick: str | None = None,
    ) -> Timer:
        """Start a timer; ``open_ended``: no duration, it runs until ``finish`` (power stations).

        ``quick``: the preset (or custom timer) this is the run of; without a station, that
        quick timer's run so far is replaced (a station has one timer anyway)."""
        if duration <= 0 and not open_ended:
            raise TimerError("invalid_duration")
        known = self.stations
        if station is not None and station not in known:
            raise TimerError("unknown_station", station=station)
        try:
            source = _copy(source or {})
        except (TypeError, ValueError):
            raise TimerError("invalid_source") from None
        if station is not None:
            for other in list(self.timers.values()):
                if other.station == station:
                    self.cancel(other.id, reason=REASON_REPLACED)
        elif quick is not None and (other := self.quick_timer(quick)) is not None:
            self.cancel(other.id, reason=REASON_REPLACED)  # one run per quick timer
        now = dt_util.utcnow()
        timer = Timer(
            id=ulid_now(),
            name=name,
            duration=0.0 if open_ended else float(duration),
            status=STATUS_ACTIVE,
            started_at=now,
            ends_at=None if open_ended else now + timedelta(seconds=duration),
            open=open_ended,
            station=station,
            station_name=known[station].name if station is not None else None,
            preset=preset,
            note=note,
            source=source,
            quick=quick,
        )
        self.timers[timer.id] = timer
        self._schedule(timer)
        self._fire(EVENT_STARTED, timer)
        self._changed()
        return timer

    def presets(self, station_id: str | None) -> tuple[Preset, ...]:
        """The presets of a station, or the area's quick timers (``None``)."""
        if station_id is None:
            return area_presets(self.entry)
        station = self.stations.get(station_id)
        if station is None:
            raise TimerError("unknown_station", station=station_id)
        return station.presets

    def scope_name(self, station_id: str | None) -> str:
        station = self.stations.get(station_id) if station_id else None
        return station.name if station else self.entry.title

    @callback
    def start_preset(
        self,
        station_id: str | None,
        preset_key: str,
        *,
        note: str | None = None,
        source: dict[str, Any] | None = None,
    ) -> Timer:
        """Start a preset (by id or name): a station's (replacing its timer) or a quick timer of
        the area (replacing that quick timer's run).

        ``note`` replaces the preset's note.
        """
        preset = find_preset(self.presets(station_id), preset_key)
        if preset is None and station_id is None:
            raise TimerError("unknown_quick_timer", preset=preset_key, area=self.entry.title)
        if preset is None:
            raise TimerError("unknown_preset", preset=preset_key, station=self.scope_name(station_id))
        return self.start(
            preset.name,
            preset.duration,
            station=station_id,
            preset=preset.name,
            note=note if note is not None else preset.note,
            source=source,
            quick=preset.id,
        )

    # -------------------------------------------------------- power stations

    @callback
    def notify_power(self) -> None:
        """A power station started or stopped running (its entities update)."""
        self._notify()

    def watcher(self, station_id: str) -> Any:
        return next((w for w in self._watchers if w.station.id == station_id), None)

    @callback
    def record_power_run(self, station_id: str, seconds: float) -> None:
        """A power station's run (until done, or until it stopped): for the statistics."""
        stats = self.power_stats.setdefault(station_id, {"runs": 0, "durations": []})
        stats["runs"] = int(stats.get("runs", 0)) + 1
        stats["last"] = round(seconds, 1)
        stats["durations"] = [*stats.get("durations", []), round(seconds, 1)][-20:]
        self._changed()

    @callback
    def record_run(self, station_id: str, run: dict[str, Any]) -> None:
        """A power station's run with its curve (for the panel). A run picked up after a
        restart misses its start: completed from the history (the recorder has it)."""
        self.runs.add(station_id, run)
        station = self.stations.get(station_id)
        if run.get("from") and station is not None and station.power is not None and self._running:
            # a little later: the recorder writes the last readings in batches
            power, name = station.power, station.name

            @callback
            def _refill(_now: Any) -> None:
                self._later.remove(handle)
                self._background(async_refill_runs(self.hass, self.runs, station_id, power), f"complete a run of {name}")

            handle = async_call_later(self.hass, REFILL_DELAY, _refill)
            self._later.append(handle)

    def usual_duration(self, station_id: str) -> float | None:
        """Median of the last runs, or None before the first."""
        durations = sorted(self.power_stats.get(station_id, {}).get("durations", []))
        if not durations:
            return None
        middle = len(durations) // 2
        return durations[middle] if len(durations) % 2 else (durations[middle - 1] + durations[middle]) / 2

    def expected_end(self, timer: Timer) -> datetime | None:
        """End of an active timer: its end, or for an open one the start plus the station's usual duration."""
        if timer.status != STATUS_ACTIVE:
            return None
        if not timer.open:
            return timer.ends_at
        usual = self.usual_duration(timer.station) if timer.station else None
        return timer.started_at + timedelta(seconds=usual) if usual else None

    # ------------------------------------------------------- custom timers

    def custom_duration(self, station_id: str | None) -> float:
        return self.custom_seconds.get(station_id or AREA, DEFAULT_CUSTOM_SECONDS)

    @callback
    def set_custom_duration(self, station_id: str | None, seconds: float) -> None:
        if seconds <= 0:
            raise TimerError("invalid_duration")
        self.custom_seconds[station_id or AREA] = float(seconds)
        self._changed()

    @callback
    def start_custom(self, station_id: str | None) -> Timer:
        """Start a timer "Timer" with the custom duration (on the station, or in the area)."""
        seconds = self.custom_duration(station_id)
        timer = self.start(CUSTOM_NAME, seconds, station=station_id, quick=QUICK_CUSTOM)
        self._last_custom[station_id or AREA] = {"timer_id": timer.id, "duration": seconds}
        self._changed()
        return timer

    def last_custom(self, station_id: str | None) -> Timer | None:
        """The last custom timer started on the station (or in the area), while it is still there.

        A timer that has moved to another station (assign) no longer counts.
        """
        info = self._last_custom.get(station_id or AREA)
        timer = self.timers.get(info["timer_id"]) if info else None
        return timer if timer is not None and timer.station == station_id else None

    def save_duration(self, station_id: str | None) -> float:
        """What "save as preset" saves: the last custom timer's duration as started, else the custom duration."""
        info = self._last_custom.get(station_id or AREA)
        if info and self.last_custom(station_id) is not None:
            return float(info["duration"])
        return self.custom_duration(station_id)

    @callback
    def save_preset(self, station_id: str | None, name: str) -> None:
        """Save as preset: the last custom timer's duration (else the custom duration) under ``name``.

        A running custom timer is renamed to ``name``. A preset of the same name gets the
        new duration. Saving changes the configuration, so the area reloads (timers go on).
        """
        name = name.strip()
        if not name:
            raise TimerError("name_required")
        timer = self.last_custom(station_id)
        seconds = self.save_duration(station_id)
        presets = [p.to_data() for p in self.presets(station_id)]
        existing = next((p for p in presets if p["name"].casefold() == name.casefold()), None)
        if existing is not None:
            existing["duration"] = seconds
            name = existing["name"]
            preset_id = existing["id"]
        else:
            preset_id = ulid_now()
            presets.append({"id": preset_id, "name": name, "duration": seconds})
        if timer is not None:
            timer.name = name
            timer.preset = name
            # the custom timer's run becomes the preset's (an area's quick timer: if it has none)
            timer.quick = preset_id if self.quick_timer(preset_id, station_id) is None else None
            if existing is not None and not timer.note:
                timer.note = existing.get("note")
            self._last_custom.pop(station_id or AREA, None)
            self._changed()
        if station_id is None:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_PRESETS: presets}
            )
        else:
            subentry = self.entry.subentries[station_id]
            self.hass.config_entries.async_update_subentry(
                self.entry, subentry, data={**subentry.data, CONF_PRESETS: presets}
            )

    @callback
    def finish(self, timer_id: str, *, at: datetime | None = None, **extra: Any) -> Timer:
        """Finish an active timer (e.g. a power station's run is done); it rings.

        ``at``: when it really ended (default now). An open timer gets its duration and
        is a normal timer from then on (snoozing it counts down).
        """
        timer = self.get(timer_id)
        if timer.status != STATUS_ACTIVE:
            raise TimerError("not_active", name=timer.name)
        now = dt_util.utcnow()
        end = min(at, now) if at is not None else now
        end = max(end, timer.started_at)
        if timer.open:
            timer.duration = (end - timer.started_at).total_seconds()
            timer.open = False
        timer.ends_at = end
        self._unschedule(timer.id)
        self._finish(timer, **extra)
        self._changed()
        return timer

    @callback
    def pause(self, timer_id: str) -> Timer:
        timer = self.get(timer_id)
        if timer.open:
            raise TimerError("open_timer", name=timer.name)
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
        """Add (or, with a negative value, subtract) time.

        Subtracting more than is left takes it down to zero: the timer runs out now.
        A ringing timer starts again with added time; subtracting from it is refused.
        """
        if seconds == 0:
            raise TimerError("invalid_duration")
        timer = self.get(timer_id)
        if timer.open and timer.status == STATUS_ACTIVE:
            raise TimerError("open_timer", name=timer.name)
        now = dt_util.utcnow()
        if timer.status == STATUS_RINGING:
            if seconds < 0:
                raise TimerError("ringing", name=timer.name)
            self._unschedule(timer.id)
            timer.status = STATUS_ACTIVE
            timer.finished_at = None
            timer.ring_anchor = None
            timer.ends_at = now + timedelta(seconds=seconds)
            change = seconds
        else:
            left = timer.remaining(now)
            change = max(seconds, -left)
            if timer.status == STATUS_PAUSED:
                timer.paused_remaining = left + change
            else:
                timer.ends_at = now + timedelta(seconds=left + change)
        timer.duration = max(0.0, timer.duration + change)
        self._fire(EVENT_TIME_CHANGED, timer, change=round(change))
        if timer.remaining(now) <= 0:  # down to zero, also when paused: it runs out now
            timer.ends_at = now
            self._unschedule(timer.id)
            self._finish(timer)
        elif timer.status == STATUS_ACTIVE:
            self._schedule(timer)
        self._changed()
        return timer

    @callback
    def assign(self, timer_id: str, station_id: str) -> Timer:
        """Attach a timer to a station of the area; it keeps its state and time.

        A timer the station already has is replaced (``reason: replaced``). A timer of
        another station moves; assigning it to its own station does nothing. Its preset
        is kept only if the new station has a preset of that name.
        """
        timer = self.get(timer_id)
        station = self.stations.get(station_id)
        if station is None:
            raise TimerError("unknown_station", station=station_id)
        if timer.station == station_id:
            return timer
        if (other := self.timer_at(station_id)) is not None:
            self.cancel(other.id, reason=REASON_REPLACED)
        known = self.stations
        previous_id = timer.station
        previous_name = (known[previous_id].name if previous_id in known else timer.station_name) if previous_id else None
        timer.station = station_id
        timer.station_name = station.name
        timer.quick = None  # moved: no longer the run of a preset …
        if timer.preset is not None and station.preset(timer.preset) is None:
            timer.preset = None  # not a preset of the new station
        elif timer.preset is not None:
            timer.quick = station.preset(timer.preset).id  # … unless the station has one of that name
        self._fire(EVENT_ASSIGNED, timer, previous_station=previous_id, previous_station_name=previous_name)
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
    def acknowledge_all(self, *, by: str, stations: bool = True) -> int:
        """Acknowledge the ringing timers of the area; with ``stations=False`` only those without a station."""
        ringing = [t for t in self.timers.values() if t.status == STATUS_RINGING and (stations or t.station is None)]
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

    def _finish(self, timer: Timer, *, late: bool = False, **extra: Any) -> None:
        now = dt_util.utcnow()
        timer.finished_at = min(timer.ends_at or now, now)
        timer.ring_anchor = now if late else timer.finished_at
        timer.ends_at = None
        timer.paused_remaining = None
        timer.status = STATUS_RINGING
        if not self.option(CONF_RING_UNTIL_ACK):
            self._remove(timer)
            self._fire(EVENT_FINISHED, timer, late=late, **extra)
            return
        self._fire(EVENT_FINISHED, timer, late=late, **extra)
        self._schedule(timer)

    def _remove(self, timer: Timer) -> None:
        self._unschedule(timer.id)
        self.timers.pop(timer.id, None)

    def _schedule(self, timer: Timer) -> None:
        """(Re)schedule what comes next for the timer: its end, or reminder and auto-dismiss."""
        self._unschedule(timer.id)
        if not self._running:
            return
        now = dt_util.utcnow()
        jobs: list[CALLBACK_TYPE] = []
        if timer.status == STATUS_ACTIVE and timer.ends_at is not None:
            jobs.append(async_track_point_in_utc_time(self.hass, self._ended_cb(timer.id), timer.ends_at))
        elif timer.status == STATUS_RINGING and timer.finished_at is not None:
            anchor = timer.ring_anchor or timer.finished_at
            dismiss_at: datetime | None = None
            if dismiss := self.option(CONF_AUTO_DISMISS_MINUTES):
                dismiss_at = anchor + timedelta(minutes=dismiss)
                if dismiss_at <= now:
                    self.acknowledge(timer.id, by=BY_TIMEOUT)
                    return
                jobs.append(async_track_point_in_utc_time(self.hass, self._dismiss_cb(timer.id), dismiss_at))
            if interval := self.option(CONF_REMINDER_MINUTES):
                step = timedelta(minutes=interval)
                at = anchor + step * (int((now - anchor) / step) + 1)
                if dismiss_at is None or at < dismiss_at:
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
            self._unschedule(timer_id)
            self._finish(timer)
            self._changed()

        return ended

    def _reminder_cb(self, timer_id: str) -> Callable[[datetime], None]:
        @callback
        def remind(_now: datetime) -> None:
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
            timer = self.timers.get(timer_id)
            if timer is not None and timer.status == STATUS_RINGING:
                self.acknowledge(timer_id, by=BY_TIMEOUT)

        return dismiss

    def event_data(self, timer: Timer, **extra: Any) -> dict[str, Any]:
        station = self.stations.get(timer.station) if timer.station else None
        device_id = self.station_device_id(timer.station) if timer.station else None
        data: dict[str, Any] = {
            "timer_id": timer.id,
            "name": timer.name,
            "area_id": self.area_id,
            "entry_id": self.entry.entry_id,
            "station": timer.station,
            "station_name": station.name if station else timer.station_name,
            "device_id": device_id or self.hub_device_id,
            "preset": timer.preset,
            "duration": round(timer.duration),
            "note": timer.note,
            "source": _copy(timer.source),
        }
        data.update(extra)
        return data

    def _fire(self, event_type: str, timer: Timer, **extra: Any) -> None:
        self.hass.bus.async_fire(event_type, self.event_data(timer, **extra))

    def _data(self) -> dict[str, Any]:
        return {
            "timers": [t.to_store() for t in self.timers.values()],
            "custom": dict(self.custom_seconds),
            "last_custom": {k: dict(v) for k, v in self._last_custom.items()},
            "power_stats": {k: dict(v) for k, v in self.power_stats.items()},
            "quick_linked": LINKED,  # timers are linked to their preset's switch when started
        }

    def _changed(self) -> None:
        self._store.async_delay_save(self._data, 1)
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
        async_dispatcher_send(self.hass, SIGNAL_TIMERS_CHANGED)


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    """A deep copy that is also proof the value can be stored (JSON)."""
    return json.loads(json.dumps(value))


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"
