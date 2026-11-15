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
           the ``baseline`` s before
time       ``seconds`` have passed                                      very regular runs
energy     ``wh`` Wh have been used                                     very regular runs
=========  ===========================================================  ====================

All rules except ``off`` ignore the first ``ignore`` seconds of a run (switch-on
spikes). A trace may start late (a run picked up after a restart): rules that look
back never look before its first sample, and ``energy`` only counts from there.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

RULE_OFF = "off"
RULE_PAUSE = "pause"
RULE_CYCLING = "cycling"
RULE_DROP = "drop"
RULE_KNEE = "knee"
RULE_TIME = "time"
RULE_ENERGY = "energy"

DEFAULT_IGNORE = 120
RULE_DEFAULTS: dict[str, dict[str, float]] = {
    RULE_OFF: {},
    RULE_PAUSE: {"below": 10, "seconds": 10, "ignore": DEFAULT_IGNORE},
    RULE_CYCLING: {"level": 50, "window": 60, "share": 60, "ignore": DEFAULT_IGNORE},
    RULE_DROP: {"percent": 10, "window": 60, "ignore": DEFAULT_IGNORE},
    RULE_KNEE: {"factor": 3, "window": 60, "baseline": 180, "ignore": DEFAULT_IGNORE},
    RULE_TIME: {"seconds": 300},
    RULE_ENERGY: {"wh": 30},
}
RULE_TYPES = tuple(RULE_DEFAULTS)
MIN_SLOPE = 0.5 / 60  # W per second: below this a baseline counts as flat
MIN_SLOPE_SHARE = 0.005 / 60  # … or below 0.5 % of the power level per minute


@dataclass
class Trace:
    """Power samples of one run; ``t`` in seconds since the run started."""

    times: list[float] = field(default_factory=list)
    watts: list[float] = field(default_factory=list)

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

    def mean(self, t0: float, t1: float) -> float:
        """Time-weighted mean between t0 and t1."""
        if t1 <= t0:
            return self.value(t1)
        return sum((b - a) * w for a, b, w in self._segments(t0, t1)) / (t1 - t0)

    def energy_wh(self, t1: float) -> float:
        return sum((b - a) * w for a, b, w in self._segments(self.start, t1)) / 3600

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

    def describe(self) -> str:
        """The rule in words (English; shown as an attribute)."""
        p = self.p
        text = {
            RULE_OFF: "when it switches off",
            RULE_PAUSE: f"below {p('below'):g} W for {p('seconds'):g} s",
            RULE_CYCLING: f"at ≥ {p('level'):g} W less than {p('share'):g} % of {p('window'):g} s",
            RULE_DROP: f"falls by ≥ {p('percent'):g} % within {p('window'):g} s",
            RULE_KNEE: f"falls {p('factor'):g}× faster over {p('window'):g} s than the {p('baseline'):g} s before",
            RULE_TIME: f"after {p('seconds'):g} s",
            RULE_ENERGY: f"after {p('wh'):g} Wh",
        }[self.type]
        if "ignore" in self.params and self.p("ignore"):
            text += f" (first {p('ignore'):g} s ignored)"
        return text

    def fires(self, trace: Trace, now: float, *, stopped: bool = False) -> bool:
        """Whether the run counts as done at ``now`` (seconds since its start)."""
        if self.type == RULE_OFF:
            return stopped
        if self.type == RULE_TIME:
            return now >= self.p("seconds")
        if self.type == RULE_ENERGY:
            return trace.energy_wh(now) >= self.p("wh")
        # never before "ignore", never looking back before the first known sample
        earliest = max(self.p("ignore"), trace.start)
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
        if self.type == RULE_KNEE:
            window, baseline = self.p("window"), self.p("baseline")
            if now - window - baseline < earliest:
                return False
            # slopes from the means of each half (robust against single noisy readings)
            w0, wm = now - window, now - window / 2
            b0, bm = w0 - baseline, w0 - baseline / 2
            recent = (trace.mean(w0, wm) - trace.mean(wm, now)) / (window / 2)
            before = (trace.mean(b0, bm) - trace.mean(bm, w0)) / (baseline / 2)
            floor = max(MIN_SLOPE, MIN_SLOPE_SHARE * trace.mean(w0, now))
            return recent > 0 and recent >= self.p("factor") * max(before, floor)
        return False
