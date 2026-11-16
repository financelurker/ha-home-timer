"""Stored runs of power stations: each run with its power curve.

A run is stored when its appliance stops (blips are not). The panel shows them,
and learning (later) marks them. Per station the newest ``KEEP`` runs without a
mark are kept; a marked run is never dropped on its own.

A run (JSON)::

    {"id", "start": ISO time, "duration": s (until it stopped),
     "rang": s after the start or None, "trigger": "rule" | "off" | "countdown" | None,
     "source": "live" | "history", "simulated": True if "rang" was worked out afterwards,
     "sensor": the power sensor it was recorded with,
     "from": s – the curve is known from here (later than 0 for a run picked up after a restart),
     "interval": median s between readings, "noise": W,
     "t": [s, …], "w": [W, …]  (a step curve: each reading holds until the next),
     "marks": {"done": s on the curve – when it was really done, marked by the user;
               other names: further points (phase starts and ends, B14);
               "ranges": {name: [from, to]} – stretches of the curve, e.g. a phase (B14)},
     "skip": True to leave it out of learning}

The first time a power station is seen, its earlier runs are read once from the
recorder's history of its sensor (``async_import_history``), so there is something
to look at right away. The rule's ring time of those runs is worked out with the
station's settings at that moment ("simulated").
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import DOMAIN
from .power import RULE_ENERGY, RULE_OFF, RULE_TIME, DoneRule, DoneSteps, Trace
from .stations import KIND_START_PRESET, KIND_UNTIL_OFF, KIND_WARN, PowerConfig

if TYPE_CHECKING:
    from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
KEEP = 30  # runs without a mark, per station
MAX_POINTS = 2000  # per stored curve
REREAD_BACK = timedelta(hours=24)  # how far before a run "read again from the history" looks for its real start
HISTORY_DAYS = 30  # how far back the first import looks (the recorder keeps 10 days by default)
MONOTONE = (RULE_TIME, RULE_ENERGY)
TICK = 5.0  # seconds: how often a running station checks its rule (as the watcher does)
MIN_RUN = 10  # seconds above "runs above"; shorter runs are blips
CURRENT = "current"  # the id of the run going on (not stored)
CURVE_KEEP = 20000  # readings of a run going on kept over a reload (a rule may read them all)


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
    size = len(t_out) / (max_points // 4)  # up to four readings kept per stretch
    keep: set[int] = {0, len(t_out) - 1}
    start = 0.0
    while int(start) < len(t_out):
        lo, hi = int(start), min(len(t_out), int(start + size))
        if hi <= lo:
            hi = lo + 1
        chunk = range(lo, hi)
        for k in (min(chunk, key=lambda i: w_out[i]), max(chunk, key=lambda i: w_out[i])):
            keep.update((k, min(k + 1, len(t_out) - 1)))  # and the reading after it: a step keeps its length
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


def _checks(trace: Trace, until: float) -> list[float]:
    """The moments a running station checks its rule: each reading and every ``TICK`` s."""
    return [t for t in sorted({*trace.times, *(trace.start + TICK * i for i in range(int((until - trace.start) / TICK) + 1))}) if t <= until]


def _first(rule: DoneRule, trace: Trace, checks: list[float], after: float | None) -> float | None:
    """The first check (after ``after``, the step before) at which the rule is met."""
    begin = 0.0
    if after is not None:
        checks = checks[bisect_right(checks, after) :]
        begin = after
    if rule.type in MONOTONE:  # once it fires it stays fired: bisect instead of trying each check
        lo, hi = 0, len(checks)
        while lo < hi:
            mid = (lo + hi) // 2
            if rule.fires(trace, checks[mid], begin=begin):
                hi = mid
            else:
                lo = mid + 1
        return checks[lo] if lo < len(checks) else None
    return next((t for t in checks if rule.fires(trace, t, begin=begin)), None)


def step_times(done: DoneRule | DoneSteps, trace: Trace, until: float) -> list[float | None]:
    """When each step was met (None from the first one that was not)."""
    steps = done.steps if isinstance(done, DoneSteps) else (done,)
    checks = _checks(trace, until)
    out: list[float | None] = []
    after: float | None = None
    for rule in steps:
        at = _first(rule, trace, checks, after) if (after is not None or not out) else None
        out.append(at)
        if at is None:
            out.extend([None] * (len(steps) - len(out)))
            break
        after = at
    return out


def first_fire(done: DoneRule | DoneSteps, trace: Trace, until: float) -> float | None:
    """When the rule – or the last of its steps – would have rung in this run."""
    return step_times(done, trace, until)[-1]


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
    sensor: str | None = None,
    marks: dict[str, float] | None = None,
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
        "sensor": sensor,
        "from": round(trace.start, 1),
        "interval": interval(trace.times),
        "noise": noise(trace.watts, on_above),
        "t": times,
        "w": watts,
        "marks": dict(marks or {}),
        "skip": False,
    }


def summary(run: dict[str, Any]) -> dict[str, Any]:
    """A run without its curve (for the list)."""
    return {k: v for k, v in run.items() if k not in ("t", "w")} | {"points": len(run.get("t", []))}


# ------------------------------------------------------------------ preview


def _detect(times: list[float], watts: list[float], config: PowerConfig, ended: bool, duration: float) -> dict[str, Any] | None:
    """The first run in a stored curve with these thresholds, as the watcher finds it.

    Blips are skipped (as the watcher drops them and starts afresh at the next rise).
    Returns {first, start, end, open, split} or None (no run at all).
    """
    i = 0
    n = len(times)
    while i < n:
        first = next((k for k in range(i, n) if watts[k] > config.on_above), None)
        if first is None:
            return None
        below: float | None = None
        end: float | None = None
        nxt = n
        for k in range(first, n):
            t, w = times[k], watts[k]
            if below is not None and t - below >= config.off_after:
                end, nxt = below, k
                break
            below = (below if below is not None else t) if w <= config.on_above else None
        is_open = False
        if end is None:
            if not ended:
                end = max(times[-1], duration)
            elif below is not None:
                end = below  # a stored run ends switched off: the power stays below
            else:
                end, is_open = times[-1], True  # still above at the end of the curve
        # a run picked up after a restart: its curve starts late, the run did not
        start = 0.0 if first == 0 and times[0] > 0 else times[first]
        if ended and not is_open and end - start < MIN_RUN:
            i = nxt if nxt > first else first + 1
            continue
        split = any(w > config.on_above for w in watts[nxt:])
        return {"first": first, "start": start, "end": end, "open": is_open, "split": split}
    return None


def simulate(run: dict[str, Any], config: PowerConfig, preset_seconds: float | None, *, ended: bool = True) -> dict[str, Any]:
    """What a station set up like ``config`` would make of a stored run (the panel's preview).

    The run is detected again within its curve ("runs above", "stopped after"): its
    start, its end, blips before it. Then, by kind: when it is done (the rule,
    checked until the stop is noticed, as live), when the preset rings or is
    cancelled, or when it warns – a countdown due before the stop is noticed
    ("stopped after" later) still rings, as live. A stored run ends switched off;
    beyond its curve the power stays where it ended. ``ended``: False for the run
    going on.

    Returns {start, duration, rang, trigger, blip, split, open} (and ``steps``: when each
    step was met, for more than one): ``start`` in seconds
    of the stored curve, ``rang`` in seconds after that start (or None), ``trigger``
    one of rule, off (done when it switched off), never (the rule did not fire: it
    rings at switch-off), countdown, cancelled, none. ``open``: the run does not stop
    within the curve with these settings.
    """
    times, watts = run.get("t") or [], run.get("w") or []
    out: dict[str, Any] = {"start": None, "duration": 0.0, "rang": None, "trigger": "none", "blip": False, "split": False, "open": False}
    found = _detect(times, watts, config, ended, float(run.get("duration") or 0.0))
    if found is None:
        out["blip"] = True
        return out
    start, end = found["start"], found["end"]
    duration = end - start
    out.update(start=round(start, 1), duration=round(duration, 1), split=found["split"], open=found["open"])
    noticed = duration + config.off_after if ended and not found["open"] else None  # when the stop is noticed
    if config.kind == KIND_UNTIL_OFF:
        if config.done.type == RULE_OFF:
            if noticed is not None:
                out.update(rang=round(duration, 1), trigger="off")
            return out
        trace = Trace()
        for t, w in zip(times[found["first"] :], watts[found["first"] :], strict=True):
            trace.add(t - start, w)
        until = noticed - 1e-6 if noticed is not None else duration  # the stop check comes first
        times = step_times(config.done, trace, until)
        if len(times) > 1:
            out["steps"] = [None if x is None else round(x, 1) for x in times]
        rang = times[-1]
        if rang is not None:
            out.update(rang=round(rang, 1), trigger="rule")
        elif noticed is not None:
            out.update(rang=round(duration, 1), trigger="never")
    elif config.kind == KIND_START_PRESET:
        if preset_seconds:
            if noticed is None:
                if preset_seconds <= duration:
                    out.update(rang=round(preset_seconds, 1), trigger="countdown")
            elif preset_seconds < noticed or not config.cancel_on_off:
                out.update(rang=round(preset_seconds, 1), trigger="countdown")
            else:
                out.update(rang=round(duration, 1), trigger="cancelled")
    elif config.kind == KIND_WARN:
        warn = config.warn_after * 60
        if warn < (noticed if noticed is not None else duration + 1e-6):
            out.update(rang=round(warn, 1), trigger="countdown")
    return out


# ------------------------------------------------------------------ the store


class RunStore:
    """The stored runs of one area's power stations."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, runs_key(entry_id))
        self.runs: dict[str, list[dict[str, Any]]] = {}  # station id → runs, oldest first
        self.imported: set[str] = set()  # stations whose history was read
        # station id → {"start", "done", "steps", "curve"}: a mark on the run going on, steps met in it,
        # its curve so far (kept when the area unloads)
        self.pending: dict[str, dict[str, Any]] = {}

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
        pending = data.get("pending")
        if isinstance(pending, dict):
            self.pending = {
                str(k): v for k, v in pending.items()
                if isinstance(v, dict) and (isinstance(v.get("done"), int | float) or isinstance(v.get("steps"), dict) or isinstance(v.get("curve"), dict))
            }

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

    def replace(self, station_id: str, ids: set[str], run: dict[str, Any]) -> None:
        """Runs (``ids``) replaced by one – a run read again from the history."""
        runs = [r for r in self.list(station_id) if r["id"] not in ids]
        self.runs[station_id] = sorted([*runs, run], key=lambda r: r["start"])
        self._save()

    def set_tried(self, station_id: str, run_id: str, tries: int) -> None:
        """A run missing its start: how often completing it from the history was tried."""
        if (run := self.get(station_id, run_id)) is not None:
            run["refill_tried"] = tries
            self._save()

    def delete(self, station_id: str, run_id: str) -> bool:
        runs = self.list(station_id)
        kept = [r for r in runs if r["id"] != run_id]
        if len(kept) == len(runs):
            return False
        self.runs[station_id] = kept
        self._save()
        return True

    def set_mark(self, station_id: str, run_id: str, done: float | None, name: str = "done") -> dict[str, Any] | None:
        """Mark a point – when the run was done (seconds on its curve) – or remove the mark.
        Other marks of the run are kept."""
        run = self.get(station_id, run_id)
        if run is None or name == "ranges":
            return None
        marks = dict(run.get("marks") or {})
        if done is None:
            marks.pop(name, None)
        else:
            marks[name] = self._on_curve(run, done)
        run["marks"] = marks
        self._save()
        return run

    def set_range(self, station_id: str, run_id: str, name: str, span: tuple[float, float] | None) -> dict[str, Any] | None:
        """Mark a stretch of the curve by name (a phase, later), or remove it."""
        run = self.get(station_id, run_id)
        if run is None:
            return None
        marks = dict(run.get("marks") or {})
        ranges = dict(marks.get("ranges") or {})
        if span is None:
            ranges.pop(name, None)
        else:
            a, b = sorted(self._on_curve(run, x) for x in span)
            ranges[name] = [a, b]
        if ranges:
            marks["ranges"] = ranges
        else:
            marks.pop("ranges", None)
        run["marks"] = marks
        self._save()
        return run

    @staticmethod
    def _on_curve(run: dict[str, Any], seconds: float) -> float:
        """A moment on the run's curve: from where it is known to its last reading."""
        last = (run.get("t") or [0.0])[-1]
        return round(min(max(float(run.get("from") or 0.0), float(seconds)), max(last, float(run.get("duration") or 0.0))), 1)

    def set_skip(self, station_id: str, run_id: str, skip: bool) -> dict[str, Any] | None:
        """Leave a run out of learning (it stays stored), or take it back in."""
        run = self.get(station_id, run_id)
        if run is None:
            return None
        run["skip"] = bool(skip)
        self._save()
        return run

    def set_pending(self, station_id: str, start: str, done: float | None) -> None:
        """"Done now" during a run: kept here until the run is stored – also across a reload.
        An empty ``start`` forgets all of the station's (the run is stored)."""
        if not start:
            if self.pending.pop(station_id, None) is not None:
                self._save()
            return
        self._pending_set(station_id, start, "done", done)

    def set_steps_met(self, station_id: str, start: str, step: int, at: float | None, rule: dict[str, Any]) -> None:
        """Steps met in the run going on (how many, when the last) – picked up after a reload
        while the steps are the same (``rule``: the done rule's data)."""
        self._pending_set(station_id, start, "steps", {"met": step, "at": at, "rule": rule} if step else None)

    def set_curve(self, station_id: str, start: str, curve: dict[str, Any]) -> None:
        """The run going on so far when the area unloads – picked up when it loads again.
        ``curve``: ``t`` (seconds after ``run_start``), ``w``, ``run_start`` (the reading that began
        the run), ``sensor``, ``at`` (when it was kept)."""
        if not curve.get("t"):
            return
        t, w = compact(list(curve["t"]), list(curve["w"]), CURVE_KEEP)  # (the same step curve, rounded)
        self._pending_set(station_id, start, "curve", {**curve, "t": t, "w": w})

    def pending_curve(self, station_id: str, start: str) -> dict[str, Any] | None:
        entry = self.pending.get(station_id)
        curve = entry.get("curve") if entry and entry.get("start") == start else None
        if not isinstance(curve, dict):
            return None
        t, w = curve.get("t"), curve.get("w")
        if not isinstance(t, list) or not isinstance(w, list) or len(t) != len(w) or not t:
            return None
        if not all(isinstance(x, int | float) and not isinstance(x, bool) for x in (*t, *w)):
            return None
        if not all(isinstance(curve.get(k), str) for k in ("at", "run_start", "sensor")):
            return None
        return {**curve, "t": [float(x) for x in t], "w": [float(x) for x in w]}

    def drop_curve(self, station_id: str, start: str) -> None:
        self._pending_set(station_id, start, "curve", None)

    def _pending_set(self, station_id: str, start: str, key: str, value: Any) -> None:
        entry = self.pending.get(station_id)
        if value is None and (not entry or entry.get("start") != start or key not in entry):
            return  # nothing of this run to remove: other data stays
        entry = dict(entry) if entry and entry.get("start") == start else {"start": start}
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
        if len(entry) > 1:
            self.pending[station_id] = entry
        else:
            self.pending.pop(station_id, None)
        self._save()

    def pending_mark(self, station_id: str, start: str) -> float | None:
        entry = self.pending.get(station_id)
        done = entry.get("done") if entry and entry.get("start") == start else None
        return float(done) if isinstance(done, int | float) and not isinstance(done, bool) else None

    def steps_met(self, station_id: str, start: str, rule: dict[str, Any]) -> tuple[int, float | None]:
        """Steps met before a reload – none when the steps changed since."""
        entry = self.pending.get(station_id)
        steps = entry.get("steps") if entry and entry.get("start") == start else None
        if not isinstance(steps, dict) or steps.get("rule") != rule or not isinstance(steps.get("met"), int) or steps["met"] < 1:
            return 0, None
        at = steps.get("at")
        return steps["met"], float(at) if isinstance(at, int | float) and not isinstance(at, bool) else None

    def fill_sensor(self, station_id: str, sensor: str) -> None:
        """Runs stored before runs knew their sensor: they were recorded with the station's."""
        missing = [r for r in self.list(station_id) if not r.get("sensor")]
        for run in missing:
            run["sensor"] = sensor
        if missing:
            self._save()

    def mark_imported(self, station_id: str) -> None:
        self.imported.add(station_id)
        self._save()

    def prune(self, known: set[str]) -> None:
        """Forget the runs of deleted stations."""
        gone = [s for s in {*self.runs, *self.imported, *self.pending} if s not in known]
        for station_id in gone:
            self.runs.pop(station_id, None)
            self.imported.discard(station_id)
            self.pending.pop(station_id, None)
        if gone:
            self._save()

    def _data(self) -> dict[str, Any]:
        return {"runs": self.runs, "imported": sorted(self.imported), "pending": self.pending}

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
                source="history", simulated=rang is not None, sensor=config.sensor,
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
        return build_runs(_read_runs(hass, history, config.sensor, config, begin, end), config)

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


# ------------------------------------------------------------------ a run read again from the history

EDGE = 5.0  # s: how far the history's start or end of a run may differ from the stored one's and still be "the same"
REFILL_TRIES = 3  # a run missing its start: how often completing it is tried (the history may not be written yet)


class RereadError(Exception):
    """Why a run could not be read again: ``no_recorder``, ``other_sensor``, ``not_found``."""


def _shift_marks(marks: dict[str, Any], delta: float) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in marks.items():
        if key == "ranges" and isinstance(value, dict):
            out[key] = {n: [round(a + delta, 1), round(b + delta, 1)] for n, (a, b) in value.items()}
        elif isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = round(value + delta, 1)
    return out


def _span(run: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start = dt_util.parse_datetime(run.get("start") or "")
    if start is None:
        return None
    return start, start + timedelta(seconds=float(run.get("duration") or 0))


def merge_reread(
    found: list[tuple[datetime, float, Trace]], runs: list[dict[str, Any]], run_id: str, config: PowerConfig, *, join: bool
) -> tuple[dict[str, Any], set[str]] | None:
    """The run ``run_id`` as the history shows it (``found``: the runs detected there).

    The detected run must contain the stored one. Without ``join`` (completing a run that
    misses its start, done on its own) it must also end where the stored one ends and
    replaces only it – the station's "on and off" may have changed since. With ``join``
    (the user's click) it replaces every stored run of the same sensor lying within it –
    pieces of the same run, e.g. cut in a pause. Marks, the ring and "left out" are kept,
    moved to the real start. None when the history shows nothing more."""
    run = next((r for r in runs if r["id"] == run_id), None)
    span = _span(run) if run else None
    if run is None or span is None:
        return None
    old_start, old_end = span
    edge = timedelta(seconds=EDGE)

    def contains(f: tuple[datetime, float, Trace]) -> bool:
        start, duration, _trace = f
        end = start + timedelta(seconds=duration)
        return start <= old_start + edge and end >= old_end - edge and (join or abs(end - old_end) <= edge)

    found = [f for f in found if contains(f)]
    if not found:
        return None
    start, duration, trace = min(found, key=lambda f: f[1])  # (the tightest)
    end = start + timedelta(seconds=duration)
    earlier = (old_start - start).total_seconds()
    pieces = [run]
    others = [sp for r in runs if r is not run and (sp := _span(r)) is not None and sp[0] < end and sp[1] > start]
    if not join and others:
        return None  # on its own it only completes a run backwards – never over another stored run
    if join:
        sensor = run.get("sensor") or config.sensor
        for other in runs:
            sp = _span(other)
            if other is not run and sp and sp[0] >= start - edge and sp[1] <= end + edge and (other.get("sensor") or sensor) == sensor:
                pieces.append(other)
    if earlier < 1 and float(run.get("from") or 0) <= 0 and len(pieces) == 1 and duration <= float(run.get("duration") or 0) + 1:
        return None  # the stored run is all there is
    marks: dict[str, Any] = {}
    ranges: dict[str, Any] = {}
    for piece in sorted(pieces, key=lambda r: r is run):  # the clicked run's marks win
        sp = _span(piece)
        shifted = _shift_marks(piece.get("marks") or {}, (sp[0] - start).total_seconds() if sp else 0.0)
        ranges.update(shifted.pop("ranges", {}))
        marks.update(shifted)
    if ranges:
        marks["ranges"] = ranges
    # the ring: of the piece that rang by its rule (or countdown), else the last one's
    rang = trigger = None
    simulated = any(p.get("simulated") for p in pieces)
    if simulated and config.kind == KIND_UNTIL_OFF:  # worked out on a curve: again, on the whole one
        rang = first_fire(config.done, trace, duration + config.off_after)
        trigger = "rule" if rang is not None else "off"
        rang = rang if rang is not None else duration
    else:
        rung = [p for p in pieces if p.get("rang") is not None and _span(p)]
        best = next((p for p in sorted(rung, key=lambda p: p["start"]) if p.get("trigger") in ("rule", "countdown")), None)
        best = best or (max(rung, key=lambda p: p["start"]) if rung else None)
        if best is not None:
            rang = float(best["rang"]) + (_span(best)[0] - start).total_seconds()
            trigger = best.get("trigger")
        else:
            trigger = run.get("trigger")
    new = make_run(
        start, duration, trace, on_above=config.on_above, rang=rang, trigger=trigger,
        source=run.get("source") or "live", simulated=simulated, sensor=config.sensor, marks=marks,
    )
    new.update(id=run["id"], skip=bool(run.get("skip")), refilled=True)
    return new, {p["id"] for p in pieces}


def _read_runs(hass: HomeAssistant, history: Any, sensor: str, config: PowerConfig, begin: datetime, end: datetime) -> list[tuple[datetime, float, Trace]]:
    """The runs in the sensor's history, read day by day (a sensor may report every second)."""
    detector = RunDetector(config)
    day = begin
    last: datetime | None = None
    while day < end:
        nxt = min(day + timedelta(days=1), end)
        # the chunks overlap a little: a reading right at a boundary is in neither otherwise
        for state in history.state_changes_during_period(
            hass, day - timedelta(seconds=1) if last else day, nxt, sensor, no_attributes=True, include_start_time_state=last is None
        ).get(sensor, []):
            if last is None or state.last_changed > last:
                detector.feed(state.last_changed, _number(state))
                last = state.last_changed
        last = last or day
        day = nxt
    return detector.finish(end)


async def async_reread_run(
    hass: HomeAssistant, store: RunStore, station_id: str, run_id: str, config: PowerConfig, *, join: bool = True
) -> dict[str, Any] | None:
    """Read a stored run again from the recorder's history (see ``merge_reread``); the new run,
    or None when there is nothing more. Raises ``RereadError``."""
    run = store.get(station_id, run_id)
    span = _span(run) if run else None
    if run is None or span is None:
        raise RereadError("not_found")
    if run.get("sensor") and run["sensor"] != config.sensor:
        raise RereadError("other_sensor")
    if "recorder" not in hass.config.components:
        raise RereadError("no_recorder")
    from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415 - optional

    instance = get_instance(hass)
    if not await instance.async_db_ready:
        raise RereadError("no_recorder")
    start, old_end = span
    # before the run: where it really began; after it: a later piece (joining only)
    end = min(dt_util.utcnow(), (old_end + REREAD_BACK) if join else (old_end + timedelta(seconds=config.off_after + 60)))
    found = await instance.async_add_executor_job(_read_runs, hass, history, config.sensor, config, start - REREAD_BACK, end)
    # merged with the runs as they are now (a mark set meanwhile is kept)
    result = merge_reread(found, store.list(station_id), run_id, config, join=join)
    if result is None:
        return None
    new, replaced = result
    store.replace(station_id, replaced, new)
    return new


async def async_refill_runs(hass: HomeAssistant, store: RunStore, station_id: str, config: PowerConfig) -> int:
    """Stored runs missing their start (picked up after a restart or reload): completed from the
    history – only backwards, nothing else replaced; tried a few times each. Returns how many
    were completed."""
    done = 0
    for run in [r for r in store.list(station_id) if float(r.get("from") or 0) > 0 and int(r.get("refill_tried") or 0) < REFILL_TRIES]:
        try:
            new = await async_reread_run(hass, store, station_id, run["id"], config, join=False)
        except RereadError as err:
            if str(err) == "no_recorder":
                return done  # maybe later
            new = None
        except Exception:  # noqa: BLE001 - history is a nice-to-have; never break the area
            _LOGGER.warning("Could not complete a run of %s from the history", config.sensor, exc_info=True)
            new = None
        if new is not None:
            done += 1
            store.set_tried(station_id, new["id"], REFILL_TRIES)
        else:
            store.set_tried(station_id, run["id"], int(run.get("refill_tried") or 0) + 1)
    return done
