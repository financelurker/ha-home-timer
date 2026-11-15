"""Watches a power station's sensor: runs, done, stopped – and drives its timer.

=============  ==================================  ===================================  ===========================
kind           switched on                         done                                 switched off
=============  ==================================  ===================================  ===========================
until_off      an open timer starts (counts up)    the done rule fires → it rings       not done yet: it rings now
start_preset   the chosen preset starts            the preset's countdown               cancels it (optional)
warn           a countdown "warn after" starts     the countdown → it rings             cancels / acknowledges it
=============  ==================================  ===================================  ===========================

A run starts when the power rises above "runs above" and stops after it stayed
below for "stopped after" seconds. Unavailable or unknown readings are ignored.
After a restart a running appliance is picked up (its earlier samples are gone).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import BY_POWER, REASON_POWER_OFF, STATUS_ACTIVE, STATUS_RINGING
from .power import Trace
from .stations import KIND_START_PRESET, KIND_UNTIL_OFF, KIND_WARN, Station

if TYPE_CHECKING:
    from .engine import AreaTimers

_LOGGER = logging.getLogger(__name__)
TICK = timedelta(seconds=5)


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
        self._below_since: datetime | None = None
        self._unsubs: list[CALLBACK_TYPE] = []

    # ------------------------------------------------------------ life cycle

    @callback
    def start(self) -> None:
        hass = self.engine.hass
        self._unsubs.append(async_track_state_change_event(hass, self.config.sensor, self._changed))
        self._unsubs.append(async_track_time_interval(hass, self._tick, TICK))
        existing = self.engine.timer_at(self.station.id)
        watts = self._watts(hass.states.get(self.config.sensor))
        if watts is not None and watts > self.config.on_above:
            # running across a restart: pick it up
            self.running = True
            self.run_start = existing.started_at if existing else dt_util.utcnow()
            self.timer_id = existing.id if existing else None
            self.done = existing is not None and existing.status == STATUS_RINGING
            self._add(watts, dt_util.utcnow())
            if existing is None:
                self._start_run(dt_util.utcnow(), watts)
        elif existing is not None and existing.open and existing.status == STATUS_ACTIVE:
            # it stopped while Home Assistant was down
            self.timer_id = existing.id
            self.run_start = existing.started_at
            self._stop_run(dt_util.utcnow())

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

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
        watts = self._watts(event.data["new_state"])
        if watts is None:
            return
        now = event.data["new_state"].last_updated
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

    def _add(self, watts: float, when: datetime) -> None:
        if self.run_start is not None:
            self.trace.add(max(0.0, (when - self.run_start).total_seconds()), watts)

    def elapsed(self, when: datetime | None = None) -> float:
        if self.run_start is None:
            return 0.0
        return ((when or dt_util.utcnow()) - self.run_start).total_seconds()

    # ------------------------------------------------------------------- runs

    def _timer(self) -> Any:
        return self.engine.timers.get(self.timer_id) if self.timer_id else None

    def _start_run(self, when: datetime, watts: float) -> None:
        self.running = True
        self.run_start = when
        self.trace = Trace()
        self.trace.add(0.0, watts)
        self.done = False
        self._below_since = None
        timer = None
        if self.config.kind == KIND_UNTIL_OFF:
            timer = self.engine.start(self.station.name, 0, station=self.station.id, open_ended=True)
        elif self.config.kind == KIND_START_PRESET:
            if self.config.preset and self.station.preset(self.config.preset):
                timer = self.engine.start_preset(self.station.id, self.config.preset)
            else:
                _LOGGER.warning("%s: no preset chosen to start on power", self.station.name)
        elif self.config.kind == KIND_WARN:
            timer = self.engine.start(self.station.name, self.config.warn_after * 60, station=self.station.id)
        self.timer_id = timer.id if timer else None
        self.engine.notify_power()

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
                self.engine.record_power_run(self.station.id, self.elapsed(now))
                self.engine.finish(timer.id, trigger="rule")
        elif self.config.kind == KIND_WARN and self._timer() is None:
            # acknowledged while it still runs: warn again after the same time
            timer = self.engine.start(self.station.name, self.config.warn_after * 60, station=self.station.id)
            self.timer_id = timer.id

    def _stop_run(self, when: datetime) -> None:
        timer = self._timer()
        seconds = self.elapsed(when)
        if self.config.kind == KIND_UNTIL_OFF:
            if timer is not None and timer.status == STATUS_ACTIVE and not self.done:
                self.engine.record_power_run(self.station.id, seconds)
                self.engine.finish(timer.id, trigger="off")
        else:
            self.engine.record_power_run(self.station.id, seconds)
            if timer is not None and timer.status == STATUS_RINGING and self.config.kind == KIND_WARN:
                self.engine.acknowledge(timer.id, by=BY_POWER)
            elif timer is not None and timer.status != STATUS_RINGING and (
                self.config.kind == KIND_WARN or self.config.cancel_on_off
            ):
                self.engine.cancel(timer.id, reason=REASON_POWER_OFF)
        self.running = False
        self.run_start = None
        self._below_since = None
        self.done = False
        self.timer_id = None
        self.engine.notify_power()
