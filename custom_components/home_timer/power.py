"""Reading an appliance's power: when it runs, and when it is done.

Everything here is pure (no Home Assistant): a ``Trace`` holds the samples of one
run as a step function (a value holds until the next one – power sensors report on
change), and a ``DoneRule`` says when the run counts as done. The rule types cover
different appliances:

=========  ===========================================================  ====================
type       fires when …                                                 e.g.
=========  ===========================================================  ====================
off        the appliance has stopped (power below "runs above")         kettle, toaster
pause      power stays below ``below`` W for ``seconds``                 pauses when done
cycling    the share of time at ≥ ``level`` W within ``window`` s       thermostat appliances
           falls below ``share`` %
drop       power falls by ≥ ``percent`` % within ``window`` s            moka pot
knee       power falls ``factor`` × faster over ``window`` s than over  moka pot
           the ``baseline`` s before ("the trend bends steeper downwards")
flatten    … falls ``factor`` × slower than before, or no longer         levels off
upturn     … starts to rise after it fell or stayed                     switches on again
steeper_up … rises ``factor`` × faster than before                       a higher stage
trend      power is below the line through the ``baseline`` s before    moka pot
           by ≥ ``factor`` × the scatter of the readings around it
rise       power is above the line through the ``baseline`` s before    something switches
           by ≥ ``factor`` × the scatter of the readings around it      on at the end
plateau    power stays within ± ``factor`` × the scatter for ``seconds``   settles at a level
           s, after it changed before                                   (keep warm)
time       ``seconds`` have passed                                      very regular runs
energy     ``wh`` Wh have been used                                     very regular runs
=========  ===========================================================  ====================

All rules except ``off`` ignore the first ``ignore`` seconds of a run (switch-on
spikes). A trace may start late (a run picked up after a restart): rules that look
back never look before its first sample, and ``energy`` only counts from there.

``DoneSteps``: rules met one after another; each later step looks only after the
one before was met.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

RULE_OFF = "off"
RULE_PAUSE = "pause"
RULE_CYCLING = "cycling"
RULE_DROP = "drop"
RULE_KNEE = "knee"  # the trend bends steeper downwards
RULE_FLATTEN = "flatten"  # the trend flattens out
RULE_UPTURN = "upturn"  # the trend turns upwards
RULE_STEEPER_UP = "steeper_up"  # the trend bends steeper upwards
RULE_TREND = "trend"
RULE_RISE = "rise"
RULE_PLATEAU = "plateau"
RULE_TIME = "time"
RULE_ENERGY = "energy"

DEFAULT_IGNORE = 120
RULE_DEFAULTS: dict[str, dict[str, float]] = {
    RULE_OFF: {},
    RULE_PAUSE: {"below": 10, "seconds": 10, "ignore": DEFAULT_IGNORE},
    RULE_CYCLING: {"level": 50, "window": 60, "share": 60, "ignore": DEFAULT_IGNORE},
    RULE_DROP: {"percent": 10, "window": 60, "ignore": DEFAULT_IGNORE},
    RULE_KNEE: {"factor": 3, "window": 60, "baseline": 180, "ignore": DEFAULT_IGNORE},
    RULE_FLATTEN: {"factor": 2, "window": 60, "baseline": 180, "ignore": DEFAULT_IGNORE},
    RULE_UPTURN: {"factor": 3, "window": 60, "baseline": 180, "ignore": DEFAULT_IGNORE},
    RULE_STEEPER_UP: {"factor": 3, "window": 60, "baseline": 180, "ignore": DEFAULT_IGNORE},
    RULE_TREND: {"factor": 3, "baseline": 120, "ignore": 90},
    RULE_RISE: {"factor": 3, "baseline": 120, "ignore": 90},
    RULE_PLATEAU: {"factor": 3, "seconds": 60, "ignore": 120},
    RULE_TIME: {"seconds": 300},
    RULE_ENERGY: {"wh": 30},
}
RULE_TYPES = tuple(RULE_DEFAULTS)

# the values of each rule type: (key, min, max, step, unit) – for the panel's fields and checks
RULE_FIELDS: dict[str, list[tuple[str, float, float, float, str]]] = {
    RULE_OFF: [],
    RULE_PAUSE: [("below", 0, 5000, 1, "W"), ("seconds", 1, 3600, 1, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_CYCLING: [
        ("level", 0, 5000, 1, "W"),
        ("window", 10, 3600, 5, "s"),
        ("share", 1, 100, 1, "%"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_DROP: [("percent", 1, 100, 1, "%"), ("window", 10, 3600, 5, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_KNEE: [
        ("factor", 1.5, 20, 0.5, "×"),
        ("window", 10, 3600, 5, "s"),
        ("baseline", 30, 7200, 10, "s"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_FLATTEN: [
        ("factor", 1.5, 20, 0.5, "×"),
        ("window", 10, 3600, 5, "s"),
        ("baseline", 30, 7200, 10, "s"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_UPTURN: [
        ("factor", 1.5, 20, 0.5, "×"),
        ("window", 10, 3600, 5, "s"),
        ("baseline", 30, 7200, 10, "s"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_STEEPER_UP: [
        ("factor", 1.5, 20, 0.5, "×"),
        ("window", 10, 3600, 5, "s"),
        ("baseline", 30, 7200, 10, "s"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_TREND: [("factor", 1, 20, 0.5, "×"), ("baseline", 30, 3600, 10, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_RISE: [("factor", 1, 20, 0.5, "×"), ("baseline", 30, 3600, 10, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_PLATEAU: [("factor", 1, 20, 0.5, "×"), ("seconds", 10, 3600, 5, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_TIME: [("seconds", 1, 86400, 1, "s")],
    RULE_ENERGY: [("wh", 0.1, 100000, 0.1, "Wh")],
}
BENDS = (RULE_KNEE, RULE_FLATTEN, RULE_UPTURN, RULE_STEEPER_UP)  # the trend changes: four directions
TREND_RECENT = 20.0  # seconds: the readings compared with the trend line
TREND_MIN_SCATTER = 0.003  # share of the level: the scatter assumed at least (clean readings would make any wiggle "clear")
MIN_SLOPE = 0.5 / 60  # W per second: below this a baseline counts as flat
MIN_SLOPE_SHARE = 0.005 / 60  # … or below 0.5 % of the power level per minute


@dataclass
class Trace:
    """Power samples of one run; ``t`` in seconds since the run started."""

    times: list[float] = field(default_factory=list)
    watts: list[float] = field(default_factory=list)
    _cum: list[float] = field(default_factory=list, repr=False, compare=False)  # energy (W·s) up to each sample

    def add(self, t: float, w: float) -> None:
        if self.times and t < self.times[-1]:
            t = self.times[-1]
        if self.times and t == self.times[-1]:
            self.watts[-1] = w
            return
        self.times.append(t)
        self.watts.append(w)

    @property
    def start(self) -> float:
        """From when the samples are known (0, or later for a run picked up after a restart)."""
        return self.times[0] if self.times else 0.0

    def value(self, t: float) -> float:
        i = bisect_right(self.times, t) - 1
        return self.watts[i] if i >= 0 else 0.0

    def _segments(self, t0: float, t1: float) -> list[tuple[float, float, float]]:
        """(start, end, watts) pieces of the step function between t0 and t1."""
        if t1 <= t0:
            return []
        pieces = []
        start = t0
        value = self.value(t0)
        i = bisect_right(self.times, t0)
        while i < len(self.times) and self.times[i] < t1:
            pieces.append((start, self.times[i], value))
            start, value = self.times[i], self.watts[i]
            i += 1
        pieces.append((start, t1, value))
        return pieces

    def maximum(self, t0: float, t1: float) -> float:
        return max(w for _, _, w in self._segments(t0, t1)) if t1 > t0 else self.value(t1)

    def share_at_least(self, level: float, t0: float, t1: float) -> float:
        total = t1 - t0
        if total <= 0:
            return 1.0
        return sum(b - a for a, b, w in self._segments(t0, t1) if w >= level) / total

    def _integral(self, t: float) -> float:
        """Energy (W·s) from the first sample up to ``t`` – O(log n) with running sums."""
        n = len(self.times)
        if len(self._cum) != n:  # samples were added: extend the running sums
            cum = self._cum[: max(0, len(self._cum) - 1)] or [0.0]
            for i in range(len(cum), n):
                cum.append(cum[-1] + (self.times[i] - self.times[i - 1]) * self.watts[i - 1])
            self._cum = cum[:n]
        if not n or t <= self.times[0]:
            return 0.0
        i = bisect_right(self.times, t) - 1
        return self._cum[i] + (t - self.times[i]) * self.watts[i]

    def mean(self, t0: float, t1: float) -> float:
        """Time-weighted mean between t0 and t1."""
        if t1 <= t0:
            return self.value(t1)
        if t0 < self.start:
            return sum((b - a) * w for a, b, w in self._segments(t0, t1)) / (t1 - t0)
        return (self._integral(t1) - self._integral(t0)) / (t1 - t0)

    def energy_wh(self, t1: float) -> float:
        return self._integral(t1) / 3600

    def has_reading(self, t0: float, t1: float) -> bool:
        """A reading arrived in (t0, t1]."""
        i = bisect_right(self.times, t0)
        return i < len(self.times) and self.times[i] <= t1

    def readings(self, t0: float, t1: float, count: int = 24) -> list[tuple[float, float]]:
        """The step curve sampled on a fixed grid over [t0, t1] – weighted by time, whatever the
        sensor's report rate (a value held long counts long; thinned curves count like raw ones)."""
        step = (t1 - t0) / count
        return [(t0 + k * step, self.value(t0 + k * step)) for k in range(count + 1)]

    def below_since(self, level: float, t: float) -> float | None:
        """Since when (seconds) the power has been below ``level`` at ``t``, or None."""
        if self.value(t) >= level:
            return None
        i = bisect_right(self.times, t) - 1
        while i > 0 and self.watts[i - 1] < level:
            i -= 1
        return self.times[i] if i >= 0 else 0.0


@dataclass(frozen=True)
class DoneRule:
    type: str = RULE_OFF
    params: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_data(cls, data: dict[str, Any] | None) -> DoneRule:
        data = data or {}
        rule_type = data.get("type", RULE_OFF)
        if rule_type not in RULE_DEFAULTS:
            rule_type = RULE_OFF
        params = dict(RULE_DEFAULTS[rule_type])
        for key in params:
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                params[key] = float(value)
        return cls(rule_type, params)

    def to_data(self) -> dict[str, Any]:
        return {"type": self.type, **self.params}

    def p(self, key: str) -> float:
        return self.params.get(key, RULE_DEFAULTS[self.type].get(key, 0.0))

    def bends(self, recent: float, before: float, floor: float) -> bool:
        """The trend changes (``bend``'s slopes, > 0 falling): whether this direction is met."""
        factor = self.p("factor")
        if self.type == RULE_KNEE:  # falls a factor faster than before
            return recent > 0 and recent >= factor * max(before, floor)
        if self.type == RULE_FLATTEN:  # it fell, now a factor slower (or no longer)
            return before > floor and recent <= before / factor
        if self.type == RULE_UPTURN:  # it fell or stayed, now it rises clearly
            return before > -floor and -recent >= factor * floor
        return -recent > 0 and -recent >= factor * max(-before, floor)  # RULE_STEEPER_UP

    def describe(self) -> str:
        """The rule in words (English; shown as an attribute)."""
        p = self.p
        text = {
            RULE_OFF: "when it switches off",
            RULE_PAUSE: f"below {p('below'):g} W for {p('seconds'):g} s",
            RULE_CYCLING: f"at ≥ {p('level'):g} W less than {p('share'):g} % of {p('window'):g} s",
            RULE_DROP: f"falls by ≥ {p('percent'):g} % within {p('window'):g} s",
            RULE_KNEE: f"falls {p('factor'):g}× faster over {p('window'):g} s than the {p('baseline'):g} s before",
            RULE_FLATTEN: f"falls {p('factor'):g}× slower over {p('window'):g} s than the {p('baseline'):g} s before",
            RULE_UPTURN: f"turns upwards over {p('window'):g} s ({p('factor'):g}× the flat limit) after the {p('baseline'):g} s before",
            RULE_STEEPER_UP: f"rises {p('factor'):g}× faster over {p('window'):g} s than the {p('baseline'):g} s before",
            RULE_TREND: f"below its trend over {p('baseline'):g} s by {p('factor'):g}× the scatter",
            RULE_RISE: f"above its trend over {p('baseline'):g} s by {p('factor'):g}× the scatter",
            RULE_PLATEAU: f"settles within ±{p('factor'):g}× the scatter for {p('seconds'):g} s",
            RULE_TIME: f"after {p('seconds'):g} s",
            RULE_ENERGY: f"after {p('wh'):g} Wh",
        }[self.type]
        if "ignore" in self.params and self.p("ignore"):
            text += f" (first {p('ignore'):g} s ignored)"
        return text

    def fires(self, trace: Trace, now: float, *, stopped: bool = False, begin: float = 0.0) -> bool:
        """Whether the run counts as done at ``now`` (seconds since its start).

        ``begin``: for a later step, when the step before was met – the rule looks at
        nothing before it, and "ignore" counts from it. Time and energy count from the
        run's start."""
        if self.type == RULE_OFF:
            return stopped
        if self.type == RULE_TIME:
            return now >= self.p("seconds")
        if self.type == RULE_ENERGY:
            return trace.energy_wh(now) >= self.p("wh")
        # never before "ignore", never looking back before the first known sample
        earliest = max(begin + self.p("ignore"), trace.start)
        if self.type == RULE_PAUSE:
            since = trace.below_since(self.p("below"), now)
            return since is not None and since >= earliest and now - since >= self.p("seconds")
        if self.type == RULE_CYCLING:
            window = self.p("window")
            if now - window < earliest:
                return False
            return trace.share_at_least(self.p("level"), now - window, now) * 100 < self.p("share")
        if self.type == RULE_DROP:
            window = self.p("window")
            if now - window < earliest:
                return False
            peak = trace.maximum(now - window, now)
            return peak > 0 and (peak - trace.value(now)) / peak * 100 >= self.p("percent")
        if self.type in BENDS:
            window, baseline = self.p("window"), self.p("baseline")
            if now - window - baseline < earliest:
                return False
            return self.bends(*bend(trace, now, window, baseline))
        if self.type == RULE_TREND:
            return _off_trend(trace, now, self.p("baseline"), self.p("factor"), earliest) < 0
        if self.type == RULE_RISE:
            return _off_trend(trace, now, self.p("baseline"), self.p("factor"), earliest) > 0
        if self.type == RULE_PLATEAU:
            return _plateau(trace, now, self.p("seconds"), self.p("factor"), earliest)
        return False


def _line(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Least squares through the readings: (mean t, mean w, slope, scatter around the line)."""
    n = len(pts)
    mt = sum(t for t, _ in pts) / n
    mw = sum(w for _, w in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    slope = sum((t - mt) * (w - mw) for t, w in pts) / var if var else 0.0
    residuals = sorted(abs(w - (mw + slope * (t - mt))) for t, w in pts)
    scatter = max(TREND_MIN_SCATTER * abs(mw), 1e-9, 1.4826 * residuals[len(residuals) // 2])
    return mt, mw, slope, scatter


def _off_trend(trace: Trace, now: float, baseline: float, factor: float, earliest: float) -> int:
    """-1: the readings of the last ``TREND_RECENT`` s are clearly below the line through
    the ``baseline`` s before them, +1: clearly above, 0: neither. "Clearly": by more
    than ``factor`` × the readings' scatter around that line (at least
    ``TREND_MIN_SCATTER`` of the level – in W or kW alike)."""
    end = now - TREND_RECENT
    start = end - baseline
    if start < earliest:
        return 0
    if not trace.has_reading(end, now):
        return 0  # nothing new since the line: no news
    mt, mw, slope, scatter = _line(trace.readings(start, end))
    diff = trace.mean(end, now) - (mw + slope * (now - TREND_RECENT / 2 - mt))
    return -1 if diff < -factor * scatter else 1 if diff > factor * scatter else 0


def _plateau(trace: Trace, now: float, seconds: float, factor: float, earliest: float) -> bool:
    """The power has stayed flat for ``seconds`` – within ± ``factor`` × its scatter around
    a level line – after it was not flat before (it changed, then settled)."""
    start = now - seconds
    if start - seconds < earliest:
        return False
    recent = trace.readings(start, now)
    mt, mw, slope, scatter = _line(recent)
    if abs(slope) * seconds > factor * scatter:
        return False  # still moving
    if any(abs(w - mw) > factor * scatter for _, w in recent):
        return False
    before = trace.readings(start - seconds, start)
    return any(abs(w - mw) > 2 * factor * scatter for _, w in before)  # it changed before settling


def bend(trace: Trace, now: float, window: float, baseline: float) -> tuple[float, float, float]:
    """How fast the power falls (> 0) or rises (< 0) over the last ``window`` and over the
    ``baseline`` before it, from the means of each half (robust against single noisy
    readings), and the slope below which it counts as flat."""
    w0, wm = now - window, now - window / 2
    b0, bm = w0 - baseline, w0 - baseline / 2
    recent = (trace.mean(w0, wm) - trace.mean(wm, now)) / (window / 2)
    before = (trace.mean(b0, bm) - trace.mean(bm, w0)) / (baseline / 2)
    return recent, before, max(MIN_SLOPE, MIN_SLOPE_SHARE * trace.mean(w0, now))


# ------------------------------------------------------------------ steps

MAX_STEPS = 8
STEP_KEYS = ("phase", "repeat", "optional")  # reserved for phases (B14): kept as stored, not used yet


@dataclass(frozen=True)
class DoneSteps:
    """"It is done after these steps": rules met one after another.

    Each step is looked for once the step before was met (at a later check); done
    when the last one is met. One step is the plain done rule. Stored as the rule
    itself for one step, else ``{"steps": [rule, …]}`` – each step may carry keys
    for phases later (``STEP_KEYS``), which are kept untouched.
    """

    steps: tuple[DoneRule, ...] = (DoneRule(),)
    extra: tuple[dict[str, Any], ...] = ({},)

    @classmethod
    def from_data(cls, data: dict[str, Any] | None) -> DoneSteps:
        raw = data.get("steps") if isinstance(data, dict) else None
        if isinstance(raw, list) and raw:
            items = [x for x in raw if isinstance(x, dict)]
            usable = [x for x in items if x.get("type") in RULE_DEFAULTS and (x["type"] != RULE_OFF or len(items) == 1)]
            if len(usable) < len(items):  # unknown types (a newer version?) or "off" within steps: left out
                _LOGGER.warning("Done steps left out (unknown, or 'off' within steps): %s", [x for x in items if x not in usable])
            items = usable[:MAX_STEPS] or [{}]
            return cls(
                tuple(DoneRule.from_data(x) for x in items),
                tuple({k: x[k] for k in STEP_KEYS if k in x} for x in items),
            )
        return cls((DoneRule.from_data(data),), ({},))

    def to_data(self) -> dict[str, Any]:
        if len(self.steps) == 1 and not self.extra[0]:
            return self.steps[0].to_data()
        return {"steps": [{**rule.to_data(), **extra} for rule, extra in zip(self.steps, self.extra, strict=True)]}

    @property
    def type(self) -> str:
        """The rule's type for one step, "steps" for more."""
        return self.steps[0].type if len(self.steps) == 1 else "steps"

    def describe(self) -> str:
        return ", then ".join(rule.describe() for rule in self.steps)
