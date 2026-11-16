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

Two steps ("A, then B"): from the single rules the search keeps a few that are met
before the mark in each run (A) and a few that are met near the mark (B, early or
not). B is looked for after A and looks at nothing before it ("ignore" counts from
A); the pairs are first judged from B's moments alone, the best few then exactly. A
pair is offered (first) only when it beats the best single rule. Longer sequences
are set up by hand.

The target is the mark the rules should meet: ``done`` for now (phase starts and ends
later, B14).

Pure (no Home Assistant); the panel runs it in an executor.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from statistics import median
from typing import Any

from .power import (
    RULE_CYCLING,
    RULE_DROP,
    RULE_ENERGY,
    RULE_FLATTEN,
    RULE_KNEE,
    RULE_PAUSE,
    RULE_PLATEAU,
    RULE_RISE,
    RULE_TIME,
    RULE_STEEPER_UP,
    RULE_TREND,
    RULE_UPTURN,
    TREND_RECENT,
    RULE_DEFAULTS,
    DoneRule,
    DoneSteps,
    Trace,
    bend,
)
from .runs import TICK, _detect, _first
from .stations import PowerConfig

MIN_MARKED = 3  # marked runs before the panel offers suggestions
MAX_RUNS = 12  # the newest marked runs the search uses (it stays quick)
MAX_CHECKS = 600  # moments checked per run
TOP = 3
EARLY = -0.5  # s: a ring this much before the mark is too early (as the panel says)
# simpler first: on a tie the earlier one wins
ORDER = [
    RULE_TREND, RULE_DROP, RULE_KNEE, RULE_FLATTEN, RULE_UPTURN, RULE_STEEPER_UP,
    RULE_RISE, RULE_PLATEAU, RULE_PAUSE, RULE_CYCLING, RULE_TIME, RULE_ENERGY,
]  # fmt: skip
BENDS = (RULE_KNEE, RULE_FLATTEN, RULE_UPTURN, RULE_STEEPER_UP)
BEND_FACTORS = {
    RULE_KNEE: (1.5, 2, 2.5, 3, 4, 6),
    RULE_FLATTEN: (1.5, 2, 3, 4, 6),
    RULE_UPTURN: (1.5, 2, 3, 5, 8),
    RULE_STEEPER_UP: (1.5, 2, 2.5, 3, 4, 6),
}
IGNORES = (0, 30, 60, 90, 120, 180)
STEP_GAP = 2 * TICK  # s: a last step met sooner after the first one is no step of its own
PER_TYPE = 5
PAIR_IGNORES = (0, 30, 60, 120)  # the last step's "ignore" (from the first step) tried in pairs
EXACT = 12  # pairs judged exactly  # steps: candidates kept per rule type for each place in a pair


def _lookback(rule: str, params: dict[str, float]) -> float:
    """How far back a rule looks at a check – it may only fire once that is past "ignore"."""
    if rule in (RULE_DROP, RULE_CYCLING):
        return params["window"]
    if rule in BENDS:
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


def _prepare(run: dict[str, Any], config: PowerConfig, target: str = "done") -> dict[str, Any] | None:
    """A marked stored run as the search needs it – found again with the draft's "on and
    off" as the preview finds it: its trace from its start, until when it is checked, its mark."""
    mark = (run.get("marks") or {}).get(target)
    if not isinstance(mark, int | float) or isinstance(mark, bool):
        mark = None
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


def _bend_fires(marked: list[dict[str, Any]]) -> Iterator[tuple[str, dict[str, float], list[list[float]]]]:
    """The four bend directions at once: the slopes per window and baseline are worked out
    once per check, each direction and factor is then a comparison (as ``DoneRule.fires``)."""
    for window in (20, 30, 40, 60, 90):
        for baseline in (60, 120, 180, 240):
            slopes = []
            for run in marked:
                trace = run["trace"]
                slopes.append([(t, *bend(trace, t, window, baseline)) for t in run["checks"] if t - window - baseline >= max(0.0, trace.start)])
            for rule in BENDS:
                for factor in BEND_FACTORS[rule]:
                    probe = DoneRule.from_data({"type": rule, "factor": factor})
                    fired = [[t for t, recent, before, floor in per if probe.bends(recent, before, floor)] for per in slopes]
                    yield rule, {"factor": factor, "window": window, "baseline": baseline}, fired


def _score(rings: list[float | None], marked: list[dict[str, Any]], tolerance: float) -> tuple[tuple[float, ...], list[float]]:
    # a rule that does not fire rings when the run is noticed as stopped (as the preview says)
    delays = [(ring if ring is not None else run["duration"]) - run["mark"] for run, ring in zip(marked, rings, strict=True)]
    early = sum(1 for d in delays if d < EARLY)
    on_time = sum(1 for d in delays if EARLY <= d <= tolerance)
    fit = [max(0.0, d) for d in delays if EARLY <= d <= tolerance]
    return (early, -on_time, median(fit) if fit else 1e9, max(fit) if fit else 1e9), delays


def _first_after(times: list[float], after: float) -> float | None:
    i = bisect_right(times, after)
    return times[i] if i < len(times) else None


def search(runs: list[dict[str, Any]], config: PowerConfig, tolerance: float, target: str = "done") -> dict[str, Any]:
    """Suggestions for the done rule (or steps) from the marked runs (not left out)."""
    usable = [p for r in runs if not r.get("skip") and (p := _prepare(r, config, target)) is not None]
    marked = usable[-MAX_RUNS:]
    if len(marked) < MIN_MARKED:
        return {"marked": len(marked), "suggestions": [], "outliers": []}
    best: dict[str, dict[str, Any]] = {}
    firsts: dict[str, list[tuple[tuple[float, ...], dict[str, Any], list[float]]]] = {}  # A: met before the mark
    nears: dict[str, list[tuple[tuple[float, ...], str, dict[str, float], float, list[list[float]]]]] = {}  # B: met near the mark

    def keep(pool: dict[str, list[Any]], rule: str, item: tuple[Any, ...], moments: tuple[Any, ...]) -> None:
        # the same moments in each run (e.g. only "ignore" differs): the first – simplest – one is kept
        seen = pool.setdefault(f"{rule}:seen", set())
        if moments in seen:
            return
        seen.add(moments)
        items = pool.setdefault(rule, [])
        items.append(item)
        if len(items) > 4 * PER_TYPE:
            items.sort(key=lambda x: x[0])
            del items[PER_TYPE:]

    def judge(rule: str, params: dict[str, float], fired: list[list[float]]) -> None:
        rings = [times[0] if times else None for times in fired]
        score, delays = _score(rings, marked, tolerance)
        data = DoneRule.from_data({"type": rule, **params}).to_data()
        score = (*score, ORDER.index(rule))
        if rule not in best or score < best[rule]["score"]:
            best[rule] = {"score": score, "rule": data, "delays": delays, "on_time": -score[1], "early": score[0]}
        # as a first step: met in each run before the mark – the closer before it the better
        gaps = [run["mark"] - ring if ring is not None else -1e9 for run, ring in zip(marked, rings, strict=True)]
        if all(g >= -EARLY for g in gaps):
            keep(firsts, rule, ((median(gaps), max(gaps), ORDER.index(rule)), data, rings), tuple(rings))

    def near(rule: str, params: dict[str, float], back: float, fired: list[list[float]]) -> None:
        # as a last step (moments without "ignore"): how it would ring if looked for only shortly before the mark
        rings = [_first_after(times, run["mark"] + EARLY - 1e-9) for run, times in zip(marked, fired, strict=True)]
        score, _ = _score(rings, marked, tolerance)
        if score[1] < 0:
            keep(nears, rule, ((*score[1:], ORDER.index(rule)), rule, params, back, fired), (back, *map(tuple, fired)))

    for rule in ORDER:
        if rule in BENDS:
            continue
        if rule in (RULE_TIME, RULE_ENERGY):
            for params in _flat(rule, marked):
                done = DoneRule.from_data({"type": rule, **params})
                fired = [_fire_times(done, run) for run in marked]
                judge(rule, params, fired)
                near(rule, params, 0.0, fired)
            continue
        for params in _grid(rule, marked):
            done = DoneRule.from_data({"type": rule, **params, "ignore": 0})
            fired = [_fire_times(done, run) for run in marked]
            back = _lookback(rule, params)
            near(rule, params, back if rule != RULE_PAUSE else params["seconds"], fired)
            for ignore in IGNORES if rule != RULE_PAUSE else (0, 60, 120):
                if rule == RULE_PAUSE and ignore:
                    with_ignore = DoneRule.from_data({"type": rule, **params, "ignore": ignore})
                    judge(rule, {**params, "ignore": ignore}, [_fire_times(with_ignore, run) for run in marked])
                else:
                    # the rule fires at t with this "ignore" when it fires without and looks back no further than it
                    judge(rule, {**params, "ignore": ignore}, [[t for t in times if t - back >= ignore] for times in fired])
    for rule, params, fired in _bend_fires(marked):
        back = _lookback(rule, params)
        near(rule, params, back, fired)
        for ignore in IGNORES:
            judge(rule, {**params, "ignore": ignore}, [[t for t in times if t - back >= ignore] for times in fired])

    ranked = sorted(best.values(), key=lambda b: b["score"])
    ranked = [b for b in ranked if b["on_time"] > 0][:TOP]
    pair = _best_pair(firsts, nears, marked, tolerance)
    if pair and (not ranked or pair["score"] < ranked[0]["score"][:4]):
        ranked = [pair, *ranked][:TOP]
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


def _best_pair(firsts: dict[str, list[Any]], nears: dict[str, list[Any]], marked: list[dict[str, Any]], tolerance: float) -> dict[str, Any] | None:
    """The best "A, then B" from the kept candidates: judged from B's moments, the best few exactly."""
    pick = lambda pool: [x for key, items in pool.items() if not key.endswith(":seen") for x in sorted(items, key=lambda x: x[0])[:PER_TYPE]]  # noqa: E731
    rough: dict[tuple[Any, ...], tuple[tuple[float, ...], dict[str, Any], dict[str, Any], list[float]]] = {}
    for _sa, a, a_rings in pick(firsts):
        for _sb, rule, params, back, fired in pick(nears):
            for ignore in PAIR_IGNORES if "ignore" in RULE_DEFAULTS[rule] else (None,):
                b = DoneRule.from_data({"type": rule, **params, **({} if ignore is None else {"ignore": ignore})}).to_data()
                if a == b:
                    continue
                # B fires at t after A when its look back starts after A (+ ignore)
                rings = [None if at is None else _first_at(times, max(at + 1e-9, at + (ignore or 0) + back)) for times, at in zip(fired, a_rings, strict=True)]
                if _no_step(rings, a_rings):
                    continue
                score, _ = _score(rings, marked, tolerance)
                score = (*score, ORDER.index(a["type"]) + ORDER.index(rule))  # on a tie: the simpler pair
                moments = (tuple(a_rings), tuple(rings))  # the same pair of moments: judged once (the simplest)
                if moments not in rough or score < rough[moments][0]:
                    rough[moments] = (score, a, b, a_rings)
    best: dict[str, Any] | None = None
    for _score_rough, a, b, a_rings in sorted(rough.values(), key=lambda x: x[0])[:EXACT]:
        rule_b = DoneRule.from_data(b)
        rings = [None if at is None else _first(rule_b, run["trace"], run["checks"], at) for run, at in zip(marked, a_rings, strict=True)]
        if _no_step(rings, a_rings):
            continue
        score, delays = _score(rings, marked, tolerance)
        if best is None or score < best["score"]:
            best = {"score": score, "rule": DoneSteps.from_data({"steps": [a, b]}).to_data(), "delays": delays,
                    "on_time": -score[1], "early": score[0]}
    return best if best and best["on_time"] > 0 else None


def _no_step(rings: list[float | None], a_rings: list[float | None]) -> bool:
    """B met right after A in most runs: no step of its own, just A a little later."""
    soon = sum(1 for ring, at in zip(rings, a_rings, strict=True) if ring is not None and at is not None and ring - at < STEP_GAP)
    return soon * 2 > len(rings)


def _first_at(times: list[float], at: float) -> float | None:
    i = bisect_left(times, at)
    return times[i] if i < len(times) else None
