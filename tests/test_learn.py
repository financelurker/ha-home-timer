"""Learning: marks on stored runs, the "below its trend" rule, suggestions, "Done now"."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.home_timer.learn import MIN_MARKED, search
from custom_components.home_timer.power import DoneRule, Trace
from custom_components.home_timer.runs import first_fire, make_run
from custom_components.home_timer.stations import PowerConfig

from .conftest import tick
from .fixtures import moka
from .test_power_station import SENSOR, add_power_station, watts

T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
DONE = moka.MARK_DONE - moka.START


def moka_trace() -> Trace:
    trace = Trace()
    for t, w in moka.SAMPLES:
        trace.add(t - moka.START, w)
    return trace


def test_below_its_trend_on_the_moka_run() -> None:
    trace = moka_trace()
    for factor in (2, 3, 4):
        for baseline in (60, 120, 180):
            at = first_fire(DoneRule.from_data({"type": "trend", "factor": factor, "baseline": baseline, "ignore": 90}), trace, 1000)
            assert at is not None and 0 <= at - DONE <= 35, (factor, baseline, at)
    assert DoneRule.from_data({"type": "trend"}).describe().startswith("below its trend")


def test_below_its_trend_ignores_scatter_on_a_steady_fall() -> None:
    rng = random.Random(5)
    trace = Trace()
    for i in range(0, 900, 5):
        trace.add(i, 250 - i * 0.1 + rng.gauss(0, 1.5))
    rule = DoneRule.from_data({"type": "trend", "factor": 4, "baseline": 120, "ignore": 60})
    assert first_fire(rule, trace, 900) is None


def marked_runs(count: int, seed: int = 3) -> list[dict]:
    rng = random.Random(seed)
    pts = [(t - moka.START, w) for t, w in moka.SAMPLES[:-1]]

    def at(t: float) -> float:
        for (a, wa), (b, wb) in zip(pts, pts[1:], strict=False):
            if a <= t <= b:
                return wa + (wb - wa) * (t - a) / (b - a)
        return pts[-1][1]

    runs = []
    for i in range(count):
        water = rng.uniform(0.8, 1.15)
        off = 970 * water
        trace = Trace()
        t = 0.0
        while t < off:
            trace.add(round(t, 1), round(at(t if t < 30 else 30 + (t - 30) / water) + rng.gauss(0, 0.8), 1))
            t += rng.uniform(8, 12)
        trace.add(round(off, 1), 0.0)
        runs.append(make_run(T0 - timedelta(days=i), off, trace, on_above=10, rang=None, trigger=None,
                             marks={"done": round(30 + (DONE - 30) * water, 1)}))
    return runs


def test_search_finds_rules_that_fit_the_marks() -> None:
    runs = marked_runs(5)
    res = search(runs, PowerConfig(sensor=SENSOR), 60)
    assert res["marked"] == 5 and len(res["suggestions"]) == 3
    best = res["suggestions"][0]
    assert best["on_time"] == 5 and best["early"] == 0 and 0 <= best["max"] <= 60
    assert len({s["rule"]["type"] for s in res["suggestions"]}) == 3
    assert res["outliers"] == []
    # too few marks: nothing yet
    assert search(runs[: MIN_MARKED - 1], PowerConfig(sensor=SENSOR), 60)["suggestions"] == []
    # runs left out do not count
    for run in runs[2:]:
        run["skip"] = True
    assert search(runs, PowerConfig(sensor=SENSOR), 60)["marked"] == 2


def test_a_wrong_mark_is_an_outlier() -> None:
    runs = marked_runs(5)
    runs[1]["marks"]["done"] = 150.0  # far before the power changes
    res = search(runs, PowerConfig(sensor=SENSOR), 60)
    assert res["outliers"] == [runs[1]["id"]]
    assert res["suggestions"][0]["on_time"] == 4


async def test_marks_skip_suggest_and_done_now(hass: HomeAssistant, kitchen, freezer, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Moka pot", "until_off", done={"type": "off"})
    button = "button.kitchen_moka_pot_done_now"
    assert hass.states.get(button) is not None
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    # a run, marked "done now" from the dashboard while it runs
    last = 0
    for t, w in moka.SAMPLES:
        await watts(hass, freezer, w, seconds_after=t - last)
        last = t
        if t == moka.MARK_DONE + 5:
            await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await tick(hass, freezer, 31)
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["marks"]["done"] == pytest.approx(DONE + 5, abs=1)
    client = await hass_ws_client(hass)
    base = {"entry_id": kitchen.entry_id, "station_id": station}
    await client.send_json_auto_id({"type": "home_timer/runs/mark", **base, "run_id": run["id"], "done": 480})
    assert (await client.receive_json())["result"] == {"marks": {"done": 480.0}}
    await client.send_json_auto_id({"type": "home_timer/runs/mark", **base, "run_id": "current"})
    assert (await client.receive_json())["error"]["code"] == "not_running"
    await client.send_json_auto_id({"type": "home_timer/runs/skip", **base, "run_id": run["id"], "skip": True})
    assert (await client.receive_json())["result"] == {"skip": True}
    await client.send_json_auto_id({"type": "home_timer/runs/list", **base})
    listed = (await client.receive_json())["result"]
    assert listed["runs"][0]["marks"] == {"done": 480.0} and listed["runs"][0]["skip"] is True and listed["min_marked"] == MIN_MARKED
    await client.send_json_auto_id({"type": "home_timer/runs/mark", **base, "run_id": run["id"], "done": None})
    assert (await client.receive_json())["result"] == {"marks": {}}
    # suggestions need marked runs
    kitchen.runtime_data.runs.add(station, marked_runs(4))
    await client.send_json_auto_id({"type": "home_timer/runs/suggest", **base, "power": {"tolerance": 60}})
    res = (await client.receive_json())["result"]
    assert res["marked"] == 4 and res["suggestions"] and res["suggestions"][0]["on_time"] == 4
    await client.send_json_auto_id({"type": "home_timer/runs/suggest_status", **base})
    status = (await client.receive_json())["result"]
    assert status["busy"] is False and status["progress"] == 1.0 and status["result"] == res


async def test_a_search_shows_its_progress_and_can_be_cancelled(hass: HomeAssistant, kitchen, hass_ws_client, monkeypatch) -> None:
    import threading

    from custom_components.home_timer.learn import Cancelled
    from custom_components.home_timer.panel import ws

    started = threading.Event()

    def slow_search(runs, config, tolerance, report):
        for i in range(1000):
            if not report(i / 1000, "rules" if i < 500 else "steps"):
                raise Cancelled
            if i == 3:
                started.set()
            threading.Event().wait(0.01)
        return {"marked": 0, "suggestions": [], "outliers": []}

    monkeypatch.setattr(ws, "search", slow_search)
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Moka pot", "until_off")
    client = await hass_ws_client(hass)
    base = {"entry_id": kitchen.entry_id, "station_id": station}
    await client.send_json({"id": 1, "type": "home_timer/runs/suggest_status", **base})
    assert (await client.receive_json())["result"] == {"busy": False}
    await client.send_json({"id": 50, "type": "home_timer/runs/suggest", **base, "sig": "abc"})
    search_id = 50
    await hass.async_add_executor_job(started.wait, 5)
    await client.send_json({"id": 102, "type": "home_timer/runs/suggest_status", **base})
    status = (await client.receive_json())["result"]
    assert status["busy"] is True and 0 < status["progress"] < 1 and status["stage"] == "rules" and status["sig"] == "abc"
    await client.send_json({"id": 103, "type": "home_timer/runs/suggest", **base})
    assert (await client.receive_json())["error"]["code"] == "busy"
    await client.send_json({"id": 104, "type": "home_timer/runs/suggest_cancel", **base})
    assert (await client.receive_json())["result"] == {"cancelled": True}
    reply = await client.receive_json()
    assert reply["id"] == search_id and reply["error"]["code"] == "cancelled"
    await client.send_json({"id": 105, "type": "home_timer/runs/suggest_status", **base})
    status = (await client.receive_json())["result"]
    assert status["busy"] is False and status["cancelled"] is True
    await client.send_json({"id": 106, "type": "home_timer/runs/suggest_cancel", **base})
    assert (await client.receive_json())["result"] == {"cancelled": False}


def test_search_reports_progress_and_stops() -> None:
    from custom_components.home_timer.learn import Cancelled

    runs = marked_runs(4)
    seen: list[tuple[float, str]] = []
    search(runs, PowerConfig(sensor=SENSOR), 60, report=lambda share, stage: seen.append((share, stage)) or True)
    assert seen[0][1] == "rules" and seen[-1][1] == "steps" and 0.95 < seen[-1][0] <= 1
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:], strict=False))
    calls: list[float] = []
    with pytest.raises(Cancelled):
        search(runs, PowerConfig(sensor=SENSOR), 60, report=lambda share, stage: calls.append(share) or len(calls) < 5)
    assert len(calls) == 5


async def test_tolerance_is_a_station_setting(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Moka pot", "until_off")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "home_timer/config/get"})
    area = (await client.receive_json())["result"]["areas"][0]
    st = next(s for s in area["stations"] if s["id"] == station)
    assert st["power"]["tolerance"] == 60
    power = {**st["power"], "tolerance": 3}
    await client.send_json_auto_id({"type": "home_timer/station/save", "entry_id": kitchen.entry_id, "station_id": station,
                                    "version": st["version"], "station": {"name": "Moka pot", "mode": "power", "power": power}})
    assert (await client.receive_json())["result"]["errors"] == {"power.tolerance": "out_of_range"}


def test_above_its_trend_and_plateau() -> None:
    rng = random.Random(1)
    rice = Trace()
    for i in range(0, 1500, 5):
        rice.add(i, 650 + rng.gauss(0, 3) if i < 1200 else 45 + rng.gauss(0, 0.8))
    plateau = DoneRule.from_data({"type": "plateau", "factor": 3, "seconds": 60, "ignore": 120})
    assert 1200 <= first_fire(plateau, rice, 1500) <= 1280
    assert first_fire(plateau, rice, 1150) is None  # flat at 650 W from the start: it never changed
    pump = Trace()
    for i in range(0, 1100, 5):
        pump.add(i, 30 + rng.gauss(0, 0.7) if i < 900 else 400)
    rise = DoneRule.from_data({"type": "rise", "factor": 3, "baseline": 120, "ignore": 60})
    assert 900 <= first_fire(rise, pump, 1100) <= 925
    assert first_fire(DoneRule.from_data({"type": "trend"}), pump, 1100) is None  # it does not fall


def test_new_rules_work_in_kilowatts() -> None:
    trace = Trace()
    for t, w in moka.SAMPLES:
        trace.add(t - moka.START, w / 1000)
    at = first_fire(DoneRule.from_data({"type": "trend", "factor": 3, "baseline": 120, "ignore": 90}), trace, 1000)
    assert at is not None and 0 <= at - DONE <= 35
    rng = random.Random(4)
    rice = Trace()
    for i in range(0, 1500, 5):
        rice.add(i, (650 + rng.gauss(0, 3) if i < 1200 else 45 + rng.gauss(0, 0.8)) / 1000)
    assert first_fire(DoneRule.from_data({"type": "plateau", "factor": 3, "seconds": 60, "ignore": 120}), rice, 1500) is not None


def test_search_is_quick_on_long_runs_and_never_early_comes_first() -> None:
    import time

    rng = random.Random(2)
    runs = []
    for i in range(3):
        trace = Trace()
        for k in range(3600):
            trace.add(k, 800 - k * 0.05 + rng.gauss(0, 1) if k < 3000 else 500 + rng.gauss(0, 1))
        trace.add(3600, 0)
        runs.append(make_run(T0 - timedelta(hours=i), 3600, trace, on_above=10, rang=None, trigger=None, marks={"done": 3003}))
    began = time.monotonic()
    res = search(runs, PowerConfig(sensor=SENSOR), 60)
    assert time.monotonic() - began < 20
    assert res["suggestions"] and all(s["early"] == 0 for s in res["suggestions"])


async def test_mark_api_edges_and_the_button_only_where_it_fits(hass: HomeAssistant, kitchen, freezer, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    warn = await add_power_station(hass, kitchen, "Iron", "warn")
    assert hass.states.get("button.kitchen_iron_done_now") is None
    station = await add_power_station(hass, kitchen, "Kettle", "until_off")
    client = await hass_ws_client(hass)
    base = {"entry_id": kitchen.entry_id, "station_id": station}
    for msg in ({"type": "home_timer/runs/mark", **base, "run_id": "nope", "done": 3}, {"type": "home_timer/runs/skip", **base, "run_id": "nope", "skip": True}):
        await client.send_json_auto_id(msg)
        assert (await client.receive_json())["error"]["code"] == "not_found"
    await client.send_json_auto_id({"type": "home_timer/runs/mark", "entry_id": kitchen.entry_id, "station_id": warn, "run_id": "current"})
    assert (await client.receive_json())["error"]["code"] == "not_running"
    # done now during a run: in the list, kept across a reload, stored with the run
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 1990, seconds_after=60)
    await client.send_json_auto_id({"type": "home_timer/runs/mark", **base, "run_id": "current", "done": 9999})
    assert (await client.receive_json())["result"]["done"] == pytest.approx(60, abs=1)  # not later than now
    await client.close()
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    await watts(hass, freezer, 1980, seconds_after=30)
    await watts(hass, freezer, 0, seconds_after=30)
    await tick(hass, freezer, 31)
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["marks"] == {"done": pytest.approx(60, abs=1)}
    assert kitchen.runtime_data.runs.pending == {}
    # a stored mark stays on the curve
    store = kitchen.runtime_data.runs
    assert store.set_mark(station, run["id"], 99999)["marks"]["done"] <= max(run["t"][-1], run["duration"])
