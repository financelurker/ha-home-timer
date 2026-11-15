"""Learning a power station's done rule from runs the user marked.

A mark is a fact – "in this run it was done here" – not a rule. The search tries
every rule with a grid of values on all marked runs (those not left out) and asks of
each: does it ring after each run's own mark, and at most ``tolerance`` seconds
after it? Never before is what matters most: a timer that rings before the coffee
is ready is worse than one that rings a little late.

Ranking: fewest too early first, then most runs on time, then the shortest median
delay, then the shortest longest delay, then the simpler rule. The best of each rule type
is offered (at most ``TOP``). A run that the best suggestion misses while it fits
most others is an outlier – its mark may be wrong.

Pure (no Home Assistant); the panel runs it in an executor.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from statistics import median
from typing import Any

from .power import (
    RULE_CYCLING,
    RULE_DROP,
    RULE_ENERGY,
    RULE_KNEE,
    RULE_PAUSE,
    RULE_PLATEAU,
    RULE_RISE,
    RULE_TIME,
    RULE_TREND,
    TREND_RECENT,
    DoneRule,
    Trace,
)
from .runs import TICK, _detect
from .stations import PowerConfig

MIN_MARKED = 3  # marked runs before the panel offers suggestions
MAX_RUNS = 12  # the newest marked runs the search uses (it stays quick)
MAX_CHECKS = 600  # moments checked per run
TOP = 3
EARLY = -0.5  # s: a ring this much before the mark is too early (as the panel says)
# simpler first: on a tie the earlier one wins
ORDER = [RULE_TREND, RULE_DROP, RULE_KNEE, RULE_RISE, RULE_PLATEAU, RULE_PAUSE, RULE_CYCLING, RULE_TIME, RULE_ENERGY]
IGNORES = (0, 30, 60, 90, 120, 180)


def _lookback(rule: str, params: dict[str, float]) -> float:
    """How far back a rule looks at a check – it may only fire once that is past "ignore"."""
    if rule in (RULE_DROP, RULE_CYCLING):
        return params["window"]
    if rule == RULE_KNEE:
        return params["window"] + params["baseline"]
    if rule in (RULE_TREND, RULE_RISE):
        return params["baseline"] + TREND_RECENT
    if rule == RULE_PLATEAU:
        return 2 * params["seconds"]
    return 0.0


def _grid(rule: str, runs: list[dict[str, Any]]) -> Iterator[dict[str, float]]:
    """Values to try, without "ignore" (the search derives each "ignore" from one pass)."""
    if rule in (RULE_TREND, RULE_RISE):
        for factor in (2, 2.5, 3, 4, 5, 6, 8):
            for baseline in (60, 90, 120, 180, 240):
                yield {"factor": factor, "baseline": baseline}
    elif rule == RULE_PLATEAU:
        for factor in (2, 3, 4, 6):
            for seconds in (20, 30, 60, 90, 120, 180):
                yield {"factor": factor, "seconds": seconds}
    elif rule == RULE_DROP:
        for percent in (3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 30):
            for window in (20, 30, 40, 60, 90, 120):
                yield {"percent": percent, "window": window}
    elif rule == RULE_KNEE:
        for factor in (1.5, 2, 2.5, 3, 4, 6):
            for window in (20, 30, 40, 60, 90):
                for baseline in (60, 120, 180, 240):
                    yield {"factor": factor, "window": window, "baseline": baseline}
    elif rule == RULE_PAUSE:
        for below in (2, 5, 10, 20, 50, 100):
            for seconds in (5, 10, 20, 30, 60):
                yield {"below": below, "seconds": seconds}
    elif rule == RULE_CYCLING:
        top = max((max(r["trace"].watts) for r in runs), default=0)
        for level in sorted({round(top * f) for f in (0.3, 0.5, 0.7)}):
            for window in (60, 120, 300):
                for share in (20, 40, 60):
                    yield {"level": level, "window": window, "share": share}


def _flat(rule: str, runs: list[dict[str, Any]]) -> Iterator[dict[str, float]]:
    """Time and energy: just after each run's mark (and the latest of them)."""
    if rule == RULE_TIME:
        for value in sorted({min(86400, math.ceil(r["mark"]) + 1) for r in runs}):
            yield {"seconds": value}
    elif rule == RULE_ENERGY:
        for value in sorted({math.ceil(r["trace"].energy_wh(r["mark"]) * 10 + 1) / 10 for r in runs}):
            yield {"wh": value}


def _prepare(run: dict[str, Any], config: PowerConfig) -> dict[str, Any] | None:
    """A marked stored run as the search needs it – found again with the draft's "on and
    off" as the preview finds it: its trace from its start, until when it is checked, its mark."""
    mark = (run.get("marks") or {}).get("done")
    times, watts = run.get("t") or [], run.get("w") or []
    if mark is None or not times:
        return None
    found = _detect(times, watts, config, True, float(run.get("duration") or 0.0))
    if found is None or found["open"]:
        return None
    start = found["start"]
    duration = found["end"] - start
    trace = Trace()
    for t, w in zip(times[found["first"] :], watts[found["first"] :], strict=True):
        trace.add(t - start, w)
    until = duration + config.off_after - 1e-6
    checks = [t for t in sorted({*trace.times, *(trace.start + TICK * i for i in range(int((until - trace.start) / TICK) + 1))}) if t <= until]
    if len(checks) > MAX_CHECKS:  # a long run: checked on an even grid (a few seconds apart) – the search stays quick
        step = (until - trace.start) / MAX_CHECKS
        checks = [trace.start + step * k for k in range(MAX_CHECKS + 1)]
    return {"id": run["id"], "trace": trace, "mark": float(mark) - start, "duration": duration, "checks": checks}


def _fire_times(done: DoneRule, run: dict[str, Any]) -> list[float]:
    return [t for t in run["checks"] if done.fires(run["trace"], t)]


def search(runs: list[dict[str, Any]], config: PowerConfig, tolerance: float) -> dict[str, Any]:
    """Suggestions for the done rule from the marked runs (not left out)."""
    usable = [p for r in runs if not r.get("skip") and (p := _prepare(r, config)) is not None]
    marked = usable[-MAX_RUNS:]
    if len(marked) < MIN_MARKED:
        return {"marked": len(marked), "suggestions": [], "outliers": []}
    best: dict[str, dict[str, Any]] = {}

    def judge(rule: str, params: dict[str, float], rings: list[float | None]) -> None:
        # a rule that does not fire rings when the run is noticed as stopped (as the preview says)
        delays = [(ring if ring is not None else run["duration"]) - run["mark"] for run, ring in zip(marked, rings, strict=True)]
        early = sum(1 for d in delays if d < EARLY)
        on_time = sum(1 for d in delays if EARLY <= d <= tolerance)
        fit = [max(0.0, d) for d in delays if EARLY <= d <= tolerance]
        score = (early, -on_time, median(fit) if fit else 1e9, max(fit) if fit else 1e9, ORDER.index(rule))
        if rule not in best or score < best[rule]["score"]:
            best[rule] = {"score": score, "rule": DoneRule.from_data({"type": rule, **params}).to_data(), "delays": delays,
                          "on_time": on_time, "early": early}

    for rule in ORDER:
        if rule in (RULE_TIME, RULE_ENERGY):
            for params in _flat(rule, marked):
                done = DoneRule.from_data({"type": rule, **params})
                judge(rule, params, [next(iter(_fire_times(done, run)), None) for run in marked])
            continue
        for params in _grid(rule, marked):
            done = DoneRule.from_data({"type": rule, **params, "ignore": 0})
            fired = [_fire_times(done, run) for run in marked]
            back = _lookback(rule, params)
            for ignore in IGNORES if rule != RULE_PAUSE else (0, 60, 120):
                if rule == RULE_PAUSE and ignore:
                    with_ignore = DoneRule.from_data({"type": rule, **params, "ignore": ignore})
                    rings = [next(iter(_fire_times(with_ignore, run)), None) for run in marked]
                else:
                    # the rule fires at t with this "ignore" when it fires without and looks back no further than it
                    rings = [next((t for t in times if t - back >= ignore), None) for times in fired]
                judge(rule, {**params, "ignore": ignore}, rings)
    ranked = sorted(best.values(), key=lambda b: b["score"])
    ranked = [b for b in ranked if b["on_time"] > 0][:TOP]
    suggestions = []
    for b in ranked:
        fit = [max(0.0, d) for d in b["delays"] if EARLY <= d <= tolerance]
        suggestions.append(
            {
                "rule": b["rule"],
                "on_time": b["on_time"],
                "early": b["early"],
                "late": len(marked) - b["on_time"] - b["early"],
                "runs": len(marked),
                "median": round(median(fit), 1) if fit else None,
                "max": round(max(fit), 1) if fit else None,
                "delays": {run["id"]: round(d, 1) for run, d in zip(marked, b["delays"], strict=True)},
            }
        )
    outliers: list[str] = []
    if suggestions and suggestions[0]["on_time"] >= max(MIN_MARKED - 1, round(len(marked) * 2 / 3)):
        outliers = [rid for rid, d in suggestions[0]["delays"].items() if not EARLY <= d <= tolerance]
    return {"marked": len(marked), "usable": len(usable), "suggestions": suggestions, "outliers": outliers}
