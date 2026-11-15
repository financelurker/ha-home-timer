"""Stored runs of power stations: each run with its power curve.

A run is stored when its appliance stops (blips are not). The panel shows them,
and learning (later) marks them. Per station the newest ``KEEP`` runs without a
mark are kept; a marked run is never dropped on its own.

A run (JSON)::

    {"id", "start": ISO time, "duration": s (until it stopped),
     "rang": s after the start or None, "trigger": "rule" | "off" | "countdown" | None,
     "source": "live" | "history", "simulated": True if "rang" was worked out afterwards,
     "from": s – the curve is known from here (later than 0 for a run picked up after a restart),
     "interval": median s between readings, "noise": W,
     "t": [s, …], "w": [W, …]  (a step curve: each reading holds until the next),
     "marks": {}, "skip": False}

The first time a power station is seen, its earlier runs are read once from the
recorder's history of its sensor (``async_import_history``), so there is something
to look at right away. The rule's ring time of those runs is worked out with the
station's settings at that moment ("simulated").
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import DOMAIN
from .power import RULE_ENERGY, RULE_TIME, DoneRule, Trace
from .stations import KIND_UNTIL_OFF, PowerConfig

if TYPE_CHECKING:
    from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
KEEP = 30  # runs without a mark, per station
MAX_POINTS = 2000  # per stored curve
HISTORY_DAYS = 30  # how far back the first import looks (the recorder keeps 10 days by default)
MONOTONE = (RULE_TIME, RULE_ENERGY)
TICK = 5.0  # seconds: how often a running station checks its rule (as the watcher does)
MIN_RUN = 10  # seconds above "runs above"; shorter runs are blips
CURRENT = "current"  # the id of the run going on (not stored)


def runs_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.runs"


# ------------------------------------------------------------------ curves


def compact(times: list[float], watts: list[float], max_points: int = MAX_POINTS) -> tuple[list[float], list[float]]:
    """Round, drop readings that repeat the one before (same step curve), thin out long runs.

    Thinning keeps the lowest and the highest reading of each stretch, so peaks and
    dips stay visible.
    """
    t_out: list[float] = []
    w_out: list[float] = []
    for t, w in zip(times, watts, strict=True):
        t, w = round(t, 1), _round_w(w)
        if w_out and w == w_out[-1]:
            continue
        if t_out and t <= t_out[-1]:
            w_out[-1] = w
            continue
        t_out.append(t)
        w_out.append(w)
    if len(t_out) <= max_points:
        return t_out, w_out
    size = len(t_out) / (max_points // 2)
    keep: set[int] = {0, len(t_out) - 1}
    start = 0.0
    while int(start) < len(t_out):
        lo, hi = int(start), min(len(t_out), int(start + size))
        if hi <= lo:
            hi = lo + 1
        chunk = range(lo, hi)
        keep.add(min(chunk, key=lambda i: w_out[i]))
        keep.add(max(chunk, key=lambda i: w_out[i]))
        start += size
    order = sorted(keep)
    return [t_out[i] for i in order], [w_out[i] for i in order]


def _round_w(w: float) -> float:
    """0.1 W for watts, more digits for small values (a sensor in kW)."""
    return round(w, 1) if abs(w) >= 10 else round(w, 3)


def interval(times: list[float]) -> float | None:
    """Median time between readings."""
    gaps = [b - a for a, b in zip(times, times[1:], strict=False) if b > a]
    return round(median(gaps), 1) if gaps else None


def noise(watts: list[float], above: float) -> float | None:
    """How much single readings scatter, in W, while it runs (robust: from steps between readings).

    The median absolute deviation of the steps, scaled to a standard deviation of one
    reading. A slow trend hardly counts; a real jump is one step and is ignored.
    """
    steps = [b - a for a, b in zip(watts, watts[1:], strict=False) if a > above and b > above]
    if len(steps) < 5:
        return None
    mid = median(steps)
    return round(1.4826 * median(abs(s - mid) for s in steps) / 2**0.5, 2)


def first_fire(rule: DoneRule, trace: Trace, until: float) -> float | None:
    """When the rule would have rung in this run (checked at each reading and every ``TICK`` s)."""
    checks = [t for t in sorted({*trace.times, *(trace.start + TICK * i for i in range(int((until - trace.start) / TICK) + 1))}) if t <= until]
    if rule.type in MONOTONE:  # once it fires it stays fired: bisect instead of trying each check
        lo, hi = 0, len(checks)
        while lo < hi:
            mid = (lo + hi) // 2
            if rule.fires(trace, checks[mid]):
                hi = mid
            else:
                lo = mid + 1
        return checks[lo] if lo < len(checks) else None
    for t in checks:
        if rule.fires(trace, t):
            return t
    return None


def make_run(
    start: datetime,
    duration: float,
    trace: Trace,
    *,
    on_above: float,
    rang: float | None,
    trigger: str | None,
    source: str = "live",
    simulated: bool = False,
) -> dict[str, Any]:
    times, watts = compact(trace.times, trace.watts)
    return {
        "id": ulid_now(),
        "start": start.isoformat(),
        "duration": round(duration, 1),
        "rang": round(rang, 1) if rang is not None else None,
        "trigger": trigger,
        "source": source,
        "simulated": simulated,
        "from": round(trace.start, 1),
        "interval": interval(trace.times),
        "noise": noise(trace.watts, on_above),
        "t": times,
        "w": watts,
        "marks": {},
        "skip": False,
    }


def summary(run: dict[str, Any]) -> dict[str, Any]:
    """A run without its curve (for the list)."""
    return {k: v for k, v in run.items() if k not in ("t", "w")} | {"points": len(run.get("t", []))}


# ------------------------------------------------------------------ the store


class RunStore:
    """The stored runs of one area's power stations."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, runs_key(entry_id))
        self.runs: dict[str, list[dict[str, Any]]] = {}  # station id → runs, oldest first
        self.imported: set[str] = set()  # stations whose history was read

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        runs = data.get("runs")
        if isinstance(runs, dict):
            self.runs = {
                str(k): [r for r in v if isinstance(r, dict) and r.get("id")] for k, v in runs.items() if isinstance(v, list)
            }
        self.imported = {str(s) for s in data.get("imported", []) if isinstance(s, str)}

    def list(self, station_id: str) -> list[dict[str, Any]]:
        return self.runs.get(station_id, [])

    def get(self, station_id: str, run_id: str) -> dict[str, Any] | None:
        return next((r for r in self.list(station_id) if r["id"] == run_id), None)

    def add(self, station_id: str, run: dict[str, Any] | list[dict[str, Any]]) -> None:
        runs = [*self.list(station_id), *(run if isinstance(run, list) else [run])]
        runs.sort(key=lambda r: r["start"])
        unmarked = [r["id"] for r in runs if not r.get("marks")]
        drop = set(unmarked[: max(0, len(unmarked) - KEEP)])
        self.runs[station_id] = [r for r in runs if r["id"] not in drop]
        self._save()

    def delete(self, station_id: str, run_id: str) -> bool:
        runs = self.list(station_id)
        kept = [r for r in runs if r["id"] != run_id]
        if len(kept) == len(runs):
            return False
        self.runs[station_id] = kept
        self._save()
        return True

    def mark_imported(self, station_id: str) -> None:
        self.imported.add(station_id)
        self._save()

    def prune(self, known: set[str]) -> None:
        """Forget the runs of deleted stations."""
        gone = [s for s in {*self.runs, *self.imported} if s not in known]
        for station_id in gone:
            self.runs.pop(station_id, None)
            self.imported.discard(station_id)
        if gone:
            self._save()

    def _data(self) -> dict[str, Any]:
        return {"runs": self.runs, "imported": sorted(self.imported)}

    def _save(self) -> None:
        self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    async def async_remove(self) -> None:
        await self._store.async_remove()


# ------------------------------------------------------------------ history


class RunDetector:
    """Finds runs in a sensor's history as the watcher does, reading by reading.

    Keeps only the newest ``keep`` runs (start, duration, trace), so a long history
    with a sensor reporting every second stays small.
    """

    def __init__(self, config: PowerConfig, keep: int = KEEP) -> None:
        self.config = config
        self.keep = keep
        self.found: list[tuple[datetime, float, Trace]] = []
        self.first = True  # the next valid reading is the first of the history
        self._start: datetime | None = None
        self._cut = False  # the run was already going at the first reading: its start is unknown
        self._trace = Trace()
        self._below: datetime | None = None

    def feed(self, when: datetime, watts: float | None) -> None:
        if watts is None:
            return
        config = self.config
        if self._start is not None and self._below is not None and (when - self._below).total_seconds() >= config.off_after:
            self._end(self._below)
        if self._start is None:
            if watts > config.on_above:
                self._start, self._trace, self._below, self._cut = when, Trace(), None, self.first
                self._trace.add(0.0, watts)
            self.first = False
            return
        self.first = False
        self._trace.add((when - self._start).total_seconds(), watts)
        self._below = (self._below or when) if watts <= config.on_above else None

    def finish(self, until: datetime) -> list[tuple[datetime, float, Trace]]:
        """The runs found; a run still going at ``until`` is left out (the watcher has it)."""
        if self._start is not None and self._below is not None and (until - self._below).total_seconds() >= self.config.off_after:
            self._end(self._below)
        return self.found

    def _end(self, below: datetime) -> None:
        assert self._start is not None
        duration = (below - self._start).total_seconds()
        if duration >= MIN_RUN and not self._cut:
            self.found = [*self.found, (self._start, duration, self._trace)][-self.keep :]
        self._start, self._below = None, None


def detect_runs(
    samples: list[tuple[datetime, float | None]], config: PowerConfig, until: datetime
) -> list[tuple[datetime, float, Trace]]:
    """Runs in a list of readings: (start, duration, trace)."""
    detector = RunDetector(config)
    for when, watts in samples:
        detector.feed(when, watts)
    return detector.finish(until)


def _number(state: State) -> float | None:
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def build_runs(found: list[tuple[datetime, float, Trace]], config: PowerConfig) -> list[dict[str, Any]]:
    """Stored runs from detected ones; for "until it switches off" when its rule would have rung."""
    runs = []
    for start, duration, trace in found:
        rang = trigger = None
        if config.kind == KIND_UNTIL_OFF:
            # live, the rule is checked until the stop is noticed ("stopped after" later)
            rang = first_fire(config.done, trace, duration + config.off_after)
            trigger = "rule" if rang is not None else "off"
            rang = rang if rang is not None else duration
        runs.append(
            make_run(
                start, duration, trace, on_above=config.on_above, rang=rang, trigger=trigger,
                source="history", simulated=rang is not None,
            )
        )
    return runs


def runs_from_history(states: list[State], config: PowerConfig, until: datetime) -> list[dict[str, Any]]:
    return build_runs(detect_runs([(s.last_changed, _number(s)) for s in states], config, until), config)


async def async_import_history(hass: HomeAssistant, store: RunStore, station_id: str, config: PowerConfig) -> int:
    """Read a station's earlier runs from the recorder once; returns how many were found.

    Reads day by day in the recorder's executor (a sensor may report every second)
    and only up to the first run stored live, so no run is stored twice.
    """
    if "recorder" not in hass.config.components:
        store.mark_imported(station_id)  # no history to read, now or later
        return 0
    from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415 - optional

    instance = get_instance(hass)
    if not await instance.async_db_ready:
        return 0
    stored = [dt_util.parse_datetime(r["start"]) for r in store.list(station_id)]
    end = min([dt_util.utcnow(), *[d for d in stored if d is not None]])
    begin = end - timedelta(days=HISTORY_DAYS)

    def _read() -> list[dict[str, Any]]:
        detector = RunDetector(config)
        day = begin
        while day < end:
            nxt = min(day + timedelta(days=1), end)
            states = history.state_changes_during_period(
                hass, day, nxt, config.sensor, no_attributes=True, include_start_time_state=day == begin
            ).get(config.sensor, [])
            for state in states:
                detector.feed(state.last_changed, _number(state))
            day = nxt
        return build_runs(detector.finish(end), config)

    try:
        runs = await instance.async_add_executor_job(_read)
    except Exception:  # noqa: BLE001 - history is a nice-to-have; never break the area
        _LOGGER.warning("Could not read the history of %s", config.sensor, exc_info=True)
        return 0
    if station_id in store.imported:  # imported meanwhile (a reload)
        return 0
    if runs:
        store.add(station_id, runs)
    store.mark_imported(station_id)
    return len(runs)
