"""Steps: rules met one after another ("it is done after these steps"), and the trend changing direction."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant

from custom_components.home_timer.learn import search
from custom_components.home_timer.panel.edit import check_done, draft_power
from custom_components.home_timer.power import DoneRule, DoneSteps, Trace
from custom_components.home_timer.runs import first_fire, make_run, simulate, step_times
from custom_components.home_timer.stations import PowerConfig

from .test_engine import events
from .test_learn import T0
from .test_power_station import SENSOR, add_power_station, watts


def trace_of(points: list[tuple[float, float]], step: float = 5) -> Trace:
    """A trace from (t, W) corners, read every ``step`` s, linear in between."""
    trace = Trace()
    for (t0, w0), (t1, w1) in zip(points, points[1:], strict=False):
        t = t0
        while t < t1:
            trace.add(t, round(w0 + (w1 - w0) * (t - t0) / (t1 - t0), 2))
            t += step
    trace.add(*points[-1])
    return trace


def bend_rule(kind: str, factor: float = 3) -> DoneRule:
    return DoneRule.from_data({"type": kind, "factor": factor, "window": 60, "baseline": 180, "ignore": 0})


def test_the_trend_changes_in_four_directions() -> None:
    falls_then_steeper = trace_of([(0, 500), (600, 440), (900, 140)])  # 0.1 W/s, then 1 W/s down
    falls_then_flat = trace_of([(0, 500), (600, 200), (900, 195)])  # 0.5 W/s down, then nearly flat
    flat_then_up = trace_of([(0, 300), (600, 300), (900, 450)])  # flat, then 0.5 W/s up
    up_then_steeper = trace_of([(0, 100), (600, 160), (900, 460)])  # 0.1 W/s, then 1 W/s up
    cases = {
        "knee": falls_then_steeper,
        "flatten": falls_then_flat,
        "upturn": flat_then_up,
        "steeper_up": up_then_steeper,
    }
    for kind, trace in cases.items():
        at = first_fire(bend_rule(kind), trace, 900)
        assert at is not None and 600 < at <= 660, (kind, at)
        # … and the other directions do not see a change there
        for other, other_trace in cases.items():
            if other != kind and (kind, other) not in {("flatten", "upturn"), ("steeper_up", "upturn")}:
                fired = first_fire(bend_rule(kind), other_trace, 900)
                assert fired is None, (kind, other, fired)


def test_steps_data_keeps_the_plain_rule_and_room_for_phases() -> None:
    plain = DoneSteps.from_data({"type": "drop", "percent": 10, "window": 60, "ignore": 120})
    assert plain.type == "drop" and plain.to_data() == {"type": "drop", "percent": 10.0, "window": 60.0, "ignore": 120.0}
    two = DoneSteps.from_data({"steps": [{"type": "time", "seconds": 60}, {"type": "pause", "below": 5, "seconds": 10, "ignore": 0}]})
    assert two.type == "steps" and len(two.steps) == 2
    assert two.describe() == "after 60 s, then " + two.steps[1].describe()
    assert DoneSteps.from_data(two.to_data()) == two
    # keys for phases (later) are kept untouched
    kept = DoneSteps.from_data({"steps": [{"type": "time", "seconds": 60, "phase": "Wash", "repeat": [1, 3], "optional": True}]})
    assert kept.to_data() == {"steps": [{"type": "time", "seconds": 60.0, "phase": "Wash", "repeat": [1, 3], "optional": True}]}
    # nothing usable: switches off
    assert DoneSteps.from_data({"steps": []}).type == "off" and DoneSteps.from_data(None).type == "off"


def test_each_step_looks_only_after_the_one_before() -> None:
    # 0–300 s: 400 W with a dip at 100 s, a pause (0 W) at 300 s, the power halves at 600 s
    trace = trace_of([(0, 400), (100, 400), (101, 200), (120, 200), (121, 400), (300, 400), (301, 0), (315, 0), (316, 400), (600, 400), (601, 200), (800, 200)])
    halves = {"type": "drop", "percent": 30, "window": 30, "ignore": 0}
    assert first_fire(DoneRule.from_data(halves), trace, 800) < 110  # alone: the dip
    steps = DoneSteps.from_data({"steps": [{"type": "pause", "below": 5, "seconds": 5, "ignore": 0}, halves]})
    times = step_times(steps, trace, 800)
    assert 300 < times[0] < 315 and 600 < times[1] <= 610
    assert first_fire(steps, trace, 800) == times[1]
    # a step that is not met: the rest are not either
    never = DoneSteps.from_data({"steps": [{"type": "time", "seconds": 5000}, halves]})
    assert step_times(never, trace, 800) == [None, None]
    # the preview shows where each step was met
    run = make_run(T0, 800, trace, on_above=10, rang=None, trigger=None)
    sim = simulate(run, PowerConfig(sensor=SENSOR, kind="until_off", done=steps), None)
    assert sim["trigger"] == "rule" and len(sim["steps"]) == 2 and sim["rang"] == sim["steps"][1]
    assert "steps" not in simulate(run, PowerConfig(sensor=SENSOR, kind="until_off", done=DoneSteps.from_data(halves)), None)


def test_checking_steps() -> None:
    rule, errors = check_done({"steps": [{"type": "drop", "percent": 500}, {"type": "trend"}]})
    assert errors == {"power.done.steps.0.percent": "out_of_range"} and len(rule["steps"]) == 2
    # "it switches off" is no step of a sequence
    assert check_done({"steps": [{"type": "off"}, {"type": "trend"}]})[1] == {"power.done.steps.0.type": "invalid"}
    # one step is stored as the plain rule, with the plain error keys
    rule, errors = check_done({"steps": [{"type": "drop", "percent": 500}]})
    assert rule["type"] == "drop" and errors == {"power.done.percent": "out_of_range"}
    assert check_done({"steps": []})[1] == {"power.done.steps": "invalid"}
    assert check_done({"steps": [{"type": "time"}] * 9})[1] == {"power.done.steps": "invalid"}
    # drafts: each value that cannot be used falls back to the saved one or the default
    saved = {"kind": "until_off", "done": {"steps": [{"type": "time", "seconds": 120}, {"type": "trend", "factor": 4, "baseline": 90, "ignore": 0}]}}
    draft = draft_power({"done": {"steps": [{"type": "time", "seconds": "x"}, {"type": "trend", "factor": 99}, {"type": "nope"}]}}, saved)
    steps = draft["done"]["steps"]
    assert steps[0]["seconds"] == 120 and steps[1]["factor"] == 4 and steps[2]["type"] == "off"


def runs_needing_two_steps(count: int = 6) -> list[dict]:
    """A pause (0 W, 15 s) mid-run, then done when the power halves; every other run dips
    to half the power before the pause – the halving alone would ring too early there."""
    rng = random.Random(3)
    out = []
    for i in range(count):
        base = rng.uniform(400, 900)
        pause = rng.uniform(600, 900)
        dip = rng.uniform(250, pause - 100) if i % 2 == 0 else None
        done = pause + rng.uniform(150, 400)
        trace = Trace()
        t = 0.0
        while t < done + 200:
            w = base if t < done else base * 0.5
            if pause <= t < pause + 15:
                w = 0.0
            if dip is not None and dip <= t < dip + 30:
                w = base * 0.5
            trace.add(round(t, 1), round(max(0.0, w + rng.gauss(0, 2)), 1) if w else 0.0)
            t += rng.uniform(4, 6)
        trace.add(round(done + 200, 1), 0.0)
        out.append(make_run(T0 - timedelta(days=i), done + 200, trace, on_above=10, rang=None, trigger=None,
                            marks={"done": round(done, 1)}))
    return out


def test_search_offers_two_steps_when_they_beat_one() -> None:
    runs = runs_needing_two_steps()
    res = search(runs, PowerConfig(sensor=SENSOR), 30)
    best = res["suggestions"][0]
    assert "steps" in best["rule"] and len(best["rule"]["steps"]) == 2
    assert best["on_time"] == 6 and best["early"] == 0
    singles = [s for s in res["suggestions"][1:] if "steps" not in s["rule"]]
    assert singles and all(s["on_time"] < 6 or s["early"] for s in singles)
    # what the search judged is what the preview says
    config = PowerConfig(sensor=SENSOR, kind="until_off", done=DoneSteps.from_data(best["rule"]))
    for run in runs:
        sim = simulate(run, config, None)
        assert sim["rang"] - (run["marks"]["done"] - sim["start"]) == pytest.approx(best["delays"][run["id"]], abs=0.2)


async def test_a_station_rings_after_its_last_step(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    done = {"steps": [{"type": "pause", "below": 5, "seconds": 5, "ignore": 0}, {"type": "drop", "percent": 30, "window": 30, "ignore": 0}]}
    await add_power_station(hass, kitchen, "Washer", "until_off", done=done)
    ev = events(hass)
    await watts(hass, freezer, 400)
    sensor = "sensor.kitchen_washer"
    assert hass.states.get(sensor).attributes["steps_met"] == 0
    await watts(hass, freezer, 200, seconds_after=60)  # a dip: the halving alone would ring here
    await watts(hass, freezer, 400, seconds_after=20)
    assert not ev["finished"]
    await watts(hass, freezer, 0, seconds_after=60)  # a pause: step 1
    await watts(hass, freezer, 400, seconds_after=12)
    assert hass.states.get(sensor).attributes["steps_met"] == 1 and not ev["finished"]
    await watts(hass, freezer, 405, seconds_after=60)
    assert not ev["finished"]
    await watts(hass, freezer, 200, seconds_after=60)  # the power halves: step 2 – done
    await watts(hass, freezer, 201, seconds_after=5)
    assert ev["finished"] and ev["finished"][0].data["trigger"] == "rule"
    assert hass.states.get(sensor).attributes["steps_met"] == 2


async def test_steps_met_survive_a_reload(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    done = {"steps": [{"type": "pause", "below": 5, "seconds": 5, "ignore": 0}, {"type": "drop", "percent": 30, "window": 30, "ignore": 0}]}
    await add_power_station(hass, kitchen, "Washer", "until_off", done=done)
    ev = events(hass)
    await watts(hass, freezer, 400)
    await watts(hass, freezer, 0, seconds_after=60)  # the pause: step 1
    await watts(hass, freezer, 400, seconds_after=12)
    assert hass.states.get("sensor.kitchen_washer").attributes["steps_met"] == 1
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    await watts(hass, freezer, 401, seconds_after=5)
    assert hass.states.get("sensor.kitchen_washer").attributes["steps_met"] == 1  # kept
    await watts(hass, freezer, 400, seconds_after=60)
    await watts(hass, freezer, 200, seconds_after=60)  # the power halves: step 2 – done
    await watts(hass, freezer, 201, seconds_after=5)
    assert ev["finished"] and ev["finished"][0].data["trigger"] == "rule"


async def test_steps_met_are_forgotten_when_the_steps_change(hass: HomeAssistant) -> None:
    from custom_components.home_timer.runs import RunStore

    store = RunStore(hass, "e1")
    rule = {"steps": [{"type": "time", "seconds": 60}, {"type": "time", "seconds": 120}]}
    store.set_steps_met("s1", "start", 1, 61.0, rule)
    store.set_pending("s1", "start", 30.0)
    assert store.steps_met("s1", "start", rule) == (1, 61.0) and store.pending_mark("s1", "start") == 30.0
    assert store.steps_met("s1", "start", {"type": "off"}) == (0, None)  # other steps
    assert store.steps_met("s1", "other start", rule) == (0, None)  # another run
    store.set_pending("s1", "start", None)
    assert store.steps_met("s1", "start", rule) == (1, 61.0)  # the mark removed, the steps kept
    store.set_pending("s1", "", None)  # the run is stored
    assert store.pending == {}


def test_unknown_steps_are_left_out() -> None:
    steps = DoneSteps.from_data({"steps": [{"type": "pause"}, {"type": "future_rule"}, {}, {"type": "off"}]})
    assert [r.type for r in steps.steps] == ["pause"]
    assert DoneSteps.from_data({"steps": [{"type": "off"}]}).type == "off"


def test_a_last_step_that_only_waits_is_no_step() -> None:
    # B ringing as soon as its look-back after A is past sees nothing of its own: A with a delay
    from .test_learn import marked_runs

    res = search(marked_runs(8, seed=5), PowerConfig(sensor=SENSOR), 60)
    assert all("steps" not in s["rule"] for s in res["suggestions"])


def test_searches_are_forgotten() -> None:
    from custom_components.home_timer.learn import SEARCHES, forget_searches

    SEARCHES.clear()
    states = {key: {"busy": True, "cancel": False} for key in ("e1:a", "e1:b", "e2:a")}
    SEARCHES.update(states)
    forget_searches("e1", {"a"})  # station b was deleted
    assert set(SEARCHES) == {"e1:a", "e2:a"} and states["e1:b"]["cancel"] is True
    forget_searches("e2")  # the area was deleted
    assert set(SEARCHES) == {"e1:a"}
    forget_searches()  # Home Assistant stops
    assert SEARCHES == {} and states["e1:a"]["cancel"] is True
