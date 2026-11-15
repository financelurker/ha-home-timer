"""Watches a power station's sensor: runs, done, stopped – and drives its timer.

=============  ==================================  ===================================  ===========================
kind           switched on                         done                                 switched off
=============  ==================================  ===================================  ===========================
until_off      an open timer starts (counts up)    the done rule fires → it rings       not done yet: it rings now
start_preset   the chosen preset starts            the preset's countdown               cancels it (optional)
warn           a countdown "warn after" starts     the countdown → it rings             cancels / acknowledges it
=============  ==================================  ===================================  ===========================

A run starts when the power rises above "runs above" and stops after it stayed
at or below it for "stopped after" seconds. Unavailable or unknown readings are
ignored. A run shorter than ``MIN_RUN`` seconds (a blip) is dropped quietly: its
timer is cancelled and it is not counted. Every other run is stored with its power
curve when it stops (``runs.py``).

Timers the watcher starts carry ``source: {"home_timer": "power"}``; only those are
driven by it. After a restart (or a reload) the first valid reading decides: still
running → its timer is picked up (rules only look at samples from then on); off →
it is handled like a switch-off, without counting the run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import BY_POWER, EVENT_ACKNOWLEDGED, EVENT_FINISHED, REASON_POWER_OFF, STATUS_ACTIVE, STATUS_RINGING
from .power import Trace
from .runs import MAX_POINTS, MIN_RUN, compact, make_run
from .stations import KIND_START_PRESET, KIND_UNTIL_OFF, KIND_WARN, Station

if TYPE_CHECKING:
    from .engine import AreaTimers

_LOGGER = logging.getLogger(__name__)
TICK = timedelta(seconds=5)
POWER_SOURCE = {"home_timer": "power"}
THIN_ABOVE = 10 * MAX_POINTS  # readings kept of a run whose curve no rule reads


class PowerWatcher:
    def __init__(self, engine: AreaTimers, station: Station) -> None:
        assert station.power is not None
        self.engine = engine
        self.station = station
        self.config = station.power
        self.running = False
        self.run_start: datetime | None = None
        self.trace = Trace()
        self.done = False
        self.timer_id: str | None = None
        self.rang: float | None = None  # seconds after the start when the run's timer rang
        self.trigger: str | None = None  # how: rule, off, countdown
        self._below_since: datetime | None = None
        self._resumed = False  # waiting for the first valid reading after a (re)start
        self._rearm = False  # warn: our timer was acknowledged while the appliance runs
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------ life cycle

    @callback
    def start(self) -> None:
        hass = self.engine.hass
        self._unsubs.append(async_track_state_change_event(hass, self.config.sensor, self._changed))
        self._unsubs.append(async_track_time_interval(hass, self._tick, TICK))
        self._unsubs.append(hass.bus.async_listen(EVENT_ACKNOWLEDGED, self._acknowledged))
        self._unsubs.append(hass.bus.async_listen(EVENT_FINISHED, self._finished))
        self._resumed = True
        watts = self._watts(hass.states.get(self.config.sensor))
        if watts is not None:
            self._resume(watts, dt_util.utcnow())

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def _ours(self) -> Any:
        """The station's timer, if this watcher started it."""
        timer = self.engine.timer_at(self.station.id)
        return timer if timer is not None and timer.source == POWER_SOURCE else None

    def _resume(self, watts: float, now: datetime) -> None:
        """First valid reading after a (re)start: pick up the run, or end it."""
        self._resumed = False
        existing = self._ours()
        if watts > self.config.on_above:
            if existing is None:
                self._start_run(now, watts)
                return
            self.running = True
            self.run_start = existing.started_at
            self.timer_id = existing.id
            self.done = existing.status == STATUS_RINGING
            if existing.finished_at is not None:  # it rang before the restart
                self.rang = (existing.finished_at - existing.started_at).total_seconds()
                self.trigger = "rule" if self.config.kind == KIND_UNTIL_OFF else "countdown"
            self.trace = Trace()
            self._add(watts, now)  # earlier samples are gone: rules look only from here
            self.engine.notify_power()
        elif existing is not None:
            # it stopped while Home Assistant was down (or the station reloaded)
            self.timer_id = existing.id
            self.run_start = existing.started_at
            self.done = existing.status == STATUS_RINGING
            self._stop_run(now, count=False)

    # --------------------------------------------------------------- readings

    @staticmethod
    def _watts(state: Any) -> float | None:
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state:
            return  # only its attributes changed: not a new reading
        watts = self._watts(new)
        if watts is None:
            return
        now = event.data["new_state"].last_updated
        if self._resumed:
            self._resume(watts, now)
            return
        if not self.running:
            if watts > self.config.on_above:
                self._start_run(now, watts)
            return
        self._add(watts, now)
        self._below_since = (self._below_since or now) if watts <= self.config.on_above else None
        self._check(now)

    @callback
    def _tick(self, _now: datetime) -> None:
        if self.running:
            self._check(dt_util.utcnow())

    @callback
    def _acknowledged(self, event: Event) -> None:
        if self.timer_id is not None and event.data.get("timer_id") == self.timer_id:
            self._rearm = self.running and self.config.kind == KIND_WARN

    @callback
    def _finished(self, event: Event) -> None:
        """Our timer rang: a countdown (start_preset, warn) – the rule and "off" note it themselves."""
        if (
            self.running
            and self.rang is None
            and self.config.kind in (KIND_START_PRESET, KIND_WARN)
            and self.timer_id is not None
            and event.data.get("timer_id") == self.timer_id
        ):
            self.rang, self.trigger = self.elapsed(), "countdown"

    def _add(self, watts: float, when: datetime) -> None:
        # the curve of every run is stored; only "until it switches off" has rules reading it
        if self.run_start is not None:
            self.trace.add(max(0.0, (when - self.run_start).total_seconds()), watts)
            if self.config.kind != KIND_UNTIL_OFF and len(self.trace.times) > THIN_ABOVE:
                # no rule reads it: a long run is thinned as it will be stored
                self.trace.times, self.trace.watts = compact(self.trace.times, self.trace.watts)

    def elapsed(self, when: datetime | None = None) -> float:
        if self.run_start is None:
            return 0.0
        return ((when or dt_util.utcnow()) - self.run_start).total_seconds()

    # ------------------------------------------------------------------- runs

    def _timer(self) -> Any:
        """Our timer of the current run – only while it is still at this station."""
        timer = self.engine.timers.get(self.timer_id) if self.timer_id else None
        return timer if timer is not None and timer.station == self.station.id else None

    def _start_run(self, when: datetime, watts: float) -> None:
        self.running = True
        self.run_start = when
        self.trace = Trace()
        self._add(watts, when)
        self.done = False
        self._rearm = False
        self._below_since = None
        self.rang = self.trigger = None
        timer = None
        if self.config.kind == KIND_UNTIL_OFF:
            timer = self.engine.start(
                self.station.name, 0, station=self.station.id, open_ended=True, source=POWER_SOURCE
            )
        elif self.config.kind == KIND_START_PRESET:
            if self.config.preset and self.station.preset(self.config.preset):
                timer = self.engine.start_preset(self.station.id, self.config.preset, source=POWER_SOURCE)
            else:
                _LOGGER.warning("%s: no preset chosen to start on power", self.station.name)
        elif self.config.kind == KIND_WARN:
            timer = self._warn()
        self.timer_id = timer.id if timer else None
        self.engine.notify_power()

    def _warn(self) -> Any:
        return self.engine.start(
            self.station.name, self.config.warn_after * 60, station=self.station.id, source=POWER_SOURCE
        )

    def _check(self, now: datetime) -> None:
        if self._below_since is not None and (now - self._below_since).total_seconds() >= self.config.off_after:
            self._stop_run(self._below_since)
            return
        if self.config.kind == KIND_UNTIL_OFF and not self.done:
            timer = self._timer()
            if timer is not None and timer.status == STATUS_ACTIVE and self.config.done.fires(
                self.trace, self.elapsed(now)
            ):
                self.done = True
                self.rang, self.trigger = self.elapsed(now), "rule"
                self.engine.record_power_run(self.station.id, self.elapsed(now))
                self.engine.finish(timer.id, trigger="rule")
        elif self.config.kind == KIND_WARN and self._rearm:
            # our warning was acknowledged while it still runs: warn again after the same time
            self._rearm = False
            if self.engine.timer_at(self.station.id) is None:
                self.timer_id = self._warn().id

    def _stop_run(self, when: datetime, *, count: bool = True) -> None:
        """The appliance stopped at ``when``; ``count``: record the run in the statistics."""
        timer = self._timer()
        seconds = self.elapsed(when)
        blip = count and seconds < MIN_RUN
        if blip:
            if timer is not None and timer.status != STATUS_RINGING:
                self.engine.cancel(timer.id, reason=REASON_POWER_OFF)
        elif self.config.kind == KIND_UNTIL_OFF:
            if timer is not None and timer.status == STATUS_ACTIVE and not self.done:
                if count:
                    self.engine.record_power_run(self.station.id, seconds)
                self.rang, self.trigger = seconds, "off"
                self.engine.finish(timer.id, at=when, trigger="off")
        else:
            if count:
                self.engine.record_power_run(self.station.id, seconds)
            if timer is not None and timer.status == STATUS_RINGING and self.config.kind == KIND_WARN:
                self.engine.acknowledge(timer.id, by=BY_POWER)
            elif timer is not None and timer.status != STATUS_RINGING and (
                self.config.kind == KIND_WARN or self.config.cancel_on_off
            ):
                self.engine.cancel(timer.id, reason=REASON_POWER_OFF)
        if count and not blip and self.run_start is not None:
            self.engine.record_run(
                self.station.id,
                make_run(
                    self.run_start, seconds, self.trace, on_above=self.config.on_above,
                    rang=self.rang, trigger=self.trigger, sensor=self.config.sensor,
                ),
            )
        self.running = False
        self.run_start = None
        self._below_since = None
        self.done = False
        self._rearm = False
        self.timer_id = None
        self.rang = self.trigger = None
        self.trace = Trace()
        self.engine.notify_power()
