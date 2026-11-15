"""Stored runs of power stations: curves, the store, the websocket API and the history import."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.home_timer.power import DoneRule, Trace
from custom_components.home_timer.runs import (
    KEEP,
    MAX_POINTS,
    RunStore,
    compact,
    detect_runs,
    first_fire,
    interval,
    make_run,
    noise,
    runs_from_history,
)
from custom_components.home_timer.stations import PowerConfig

from .conftest import setup_area, tick
from .fixtures import moka
from .test_engine import events
from .test_power_station import SENSOR, add_power_station, watts

DROP = {"type": "drop", "percent": 10, "window": 60, "ignore": 120}
T0 = datetime(2026, 11, 15, 13, 5, tzinfo=UTC)


def moka_trace() -> Trace:
    trace = Trace()
    for t, w in moka.SAMPLES[:-1]:
        trace.add(t - moka.START, w)
    return trace


# ------------------------------------------------------------------ curves


def test_compact_drops_repeats_and_thins_long_runs() -> None:
    assert compact([0, 1, 2, 3, 4], [5, 5, 5, 7, 7]) == ([0, 3], [5, 7])
    times = [float(i) for i in range(10000)]
    values = [100.0 + (i % 50) for i in range(10000)]
    values[5000] = 900.0  # one spike
    t, w = compact(times, values)
    assert len(t) <= MAX_POINTS + 2
    assert 900.0 in w and t[0] == 0 and t[-1] == 9999
    assert t == sorted(t)


def test_interval_and_noise() -> None:
    assert interval([0, 2, 4, 6, 9]) == 2
    assert interval([0]) is None
    rng = random.Random(1)
    scatter = [200 + rng.gauss(0, 1.0) for _ in range(400)]
    assert noise(scatter, 10) == pytest.approx(1.0, rel=0.2)
    assert noise([0, 0, 0], 10) is None
    # a slow fall without scatter is not noise
    assert noise([200 - i for i in range(40)], 10) == 0


def test_first_fire_on_the_moka_run() -> None:
    trace = moka_trace()
    at = first_fire(DoneRule.from_data(DROP), trace, moka.MARK_OFF - moka.START)
    assert at is not None
    assert 0 <= at + moka.START - moka.MARK_DONE <= 70
    assert first_fire(DoneRule.from_data({"type": "off"}), trace, 900) is None


def test_detect_runs_like_the_watcher() -> None:
    config = PowerConfig(sensor=SENSOR, on_above=10, off_after=30)
    s = lambda sec, w: (T0 + timedelta(seconds=sec), w)  # noqa: E731
    samples = [s(0, 0), s(10, 500), s(20, None), s(40, 480), s(100, 0), s(140, 0),  # a run of 90 s
               s(200, 500), s(205, 0), s(260, 0),  # a blip
               s(300, 600)]  # still running at the end
    runs = detect_runs(samples, config, T0 + timedelta(seconds=320))
    assert [(r[0], r[1]) for r in runs] == [(T0 + timedelta(seconds=10), 90.0)]
    assert runs[0][2].times[0] == 0


def test_runs_from_history_simulate_the_rule() -> None:
    config = PowerConfig(sensor=SENSOR, done=DoneRule.from_data(DROP))
    states = [State(SENSOR, "0", last_changed=T0)]
    states += [State(SENSOR, str(w), last_changed=T0 + timedelta(seconds=t)) for t, w in moka.SAMPLES]
    states += [State(SENSOR, "unavailable", last_changed=T0 + timedelta(seconds=1100))]
    (run,) = runs_from_history(states, config, T0 + timedelta(hours=1))
    assert run["source"] == "history" and run["simulated"] is True and run["trigger"] == "rule"
    assert run["start"] == (T0 + timedelta(seconds=moka.START)).isoformat()
    assert run["duration"] == moka.MARK_OFF - moka.START
    assert 0 <= run["rang"] + moka.START - moka.MARK_DONE <= 70
    assert run["interval"] and run["noise"] is not None


# ------------------------------------------------------------------ the store


async def test_store_keeps_the_newest_and_marked_runs(hass: HomeAssistant) -> None:
    store = RunStore(hass, "e1")
    trace = Trace()
    trace.add(0, 100)
    runs = [make_run(T0 + timedelta(hours=i), 60, trace, on_above=10, rang=None, trigger=None) for i in range(KEEP + 5)]
    runs[0]["marks"] = {"done": 30}
    store.add("s1", runs)
    kept = store.list("s1")
    assert len(kept) == KEEP + 1
    assert kept[0]["id"] == runs[0]["id"]  # marked: kept although the oldest
    assert kept[-1]["id"] == runs[-1]["id"]
    assert store.delete("s1", runs[-1]["id"]) and not store.delete("s1", "nope")
    store.mark_imported("s1")
    store.prune({"other"})
    assert store.list("s1") == [] and store.imported == set()


# ------------------------------------------------------------------ live runs


async def test_a_run_is_stored_with_its_curve(hass: HomeAssistant, kitchen, freezer, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Moka pot", "until_off", done=DROP)
    engine = kitchen.runtime_data
    last = 0
    for t, w in moka.SAMPLES[:20]:
        await watts(hass, freezer, w, seconds_after=t - last)
        last = t
    # while it runs: the current run with its curve (a client only while time stands still)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "home_timer/runs/list", "entry_id": kitchen.entry_id, "station_id": station})
    res = (await client.receive_json())["result"]
    assert res["runs"] == [] and res["current"]["running"] is True
    await client.send_json_auto_id({"type": "home_timer/runs/get", "entry_id": kitchen.entry_id, "station_id": station, "run_id": "current"})
    current = (await client.receive_json())["result"]
    assert len(current["t"]) == len(current["w"]) > 10
    await client.close()
    for t, w in moka.SAMPLES[20:]:
        await watts(hass, freezer, w, seconds_after=t - last)
        last = t
    await tick(hass, freezer, 31)
    (run,) = engine.runs.list(station)
    assert run["source"] == "live" and run["trigger"] == "rule" and not run["simulated"]
    assert 0 <= run["rang"] + moka.START - moka.MARK_DONE <= 70
    assert run["duration"] == pytest.approx(moka.MARK_OFF - moka.START, abs=1)
    assert run["t"][0] == 0 and run["w"][0] == 172
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "home_timer/runs/list", "entry_id": kitchen.entry_id, "station_id": station})
    res = (await client.receive_json())["result"]
    assert res["current"] is None and len(res["runs"]) == 1 and "t" not in res["runs"][0]
    assert res["sensor"]["interval"] and res["sensor"]["noise"] is not None
    await client.send_json_auto_id({"type": "home_timer/runs/get", "entry_id": kitchen.entry_id, "station_id": station, "run_id": run["id"]})
    assert (await client.receive_json())["result"]["w"] == run["w"]
    await client.send_json_auto_id({"type": "home_timer/runs/delete", "entry_id": kitchen.entry_id, "station_id": station, "run_id": run["id"]})
    assert (await client.receive_json())["result"] == {"deleted": True}
    await client.send_json_auto_id({"type": "home_timer/runs/get", "entry_id": kitchen.entry_id, "station_id": station, "run_id": run["id"]})
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_runs_survive_a_reload(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off", done={"type": "off"})
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 0, seconds_after=120)
    await tick(hass, freezer, 31)
    assert len(kitchen.runtime_data.runs.list(station)) == 1
    assert kitchen.runtime_data.runs.list(station)[0]["trigger"] == "off"
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["rang"] == pytest.approx(120, abs=1)


async def test_blips_and_countdowns(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Rice cooker", "start_preset", presets=[("Rice", 60)], preset="p0")
    ev = events(hass)
    await watts(hass, freezer, 500)
    await watts(hass, freezer, 0, seconds_after=3)
    await tick(hass, freezer, 31)
    assert kitchen.runtime_data.runs.list(station) == []  # a blip
    await watts(hass, freezer, 500, seconds_after=10)
    await tick(hass, freezer, 65)
    assert ev["finished"]
    await watts(hass, freezer, 0, seconds_after=30)
    await tick(hass, freezer, 31)
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["trigger"] == "countdown" and run["rang"] == pytest.approx(60, abs=6)


async def test_deleting_the_area_deletes_its_runs(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen")
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, entry, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 31)
    store = entry.runtime_data.runs
    await store.async_save()
    assert await hass.config_entries.async_remove(entry.entry_id)
    again = RunStore(hass, entry.entry_id)
    await again.async_load()
    assert again.list(station) == []


def test_a_run_going_at_the_first_reading_is_left_out() -> None:
    config = PowerConfig(sensor=SENSOR, on_above=10, off_after=30)
    s = lambda sec, w: (T0 + timedelta(seconds=sec), w)  # noqa: E731
    runs = detect_runs([s(0, 500), s(60, 0), s(100, 0), s(200, 400), s(300, 0), s(340, 0)], config, T0 + timedelta(seconds=400))
    assert [r[1] for r in runs] == [100.0]  # the first one started before the history


def test_history_checks_the_rule_until_the_stop_is_noticed() -> None:
    """As live: a pause rule shorter than "stopped after" fires after the power went down."""
    config = PowerConfig(sensor=SENSOR, on_above=10, off_after=30,
                         done=DoneRule.from_data({"type": "pause", "below": 20, "seconds": 10, "ignore": 0}))
    states = [State(SENSOR, w, last_changed=T0 + timedelta(seconds=t)) for t, w in ((0, "0"), (10, "800"), (100, "0"), (200, "0"))]
    (run,) = runs_from_history(states, config, T0 + timedelta(seconds=300))
    assert run["duration"] == 90 and run["trigger"] == "rule"
    assert 100 <= run["rang"] <= 105  # 10 s below 20 W, on the 5 s tick


def test_first_fire_bisects_monotone_rules() -> None:
    trace = Trace()
    for i in range(7200):
        trace.add(float(i), 1000.0)
    assert first_fire(DoneRule.from_data({"type": "energy", "wh": 500}), trace, 7200) == 1800
    assert first_fire(DoneRule.from_data({"type": "time", "seconds": 61}), trace, 7200) == 61
    assert first_fire(DoneRule.from_data({"type": "energy", "wh": 5000}), trace, 7200) is None


async def test_runs_api_needs_a_loaded_area(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "home_timer/runs/delete", "entry_id": kitchen.entry_id, "station_id": station, "run_id": "current"})
    assert (await client.receive_json())["error"]["code"] == "not_found"
    await hass.config_entries.async_unload(kitchen.entry_id)
    await client.send_json_auto_id({"type": "home_timer/runs/list", "entry_id": kitchen.entry_id, "station_id": station})
    assert (await client.receive_json())["error"]["code"] == "not_loaded"


async def test_a_run_across_a_reload_keeps_its_ring(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off", done={"type": "time", "seconds": 60})
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 2001, seconds_after=70)  # the rule rang at about 60 s
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    await watts(hass, freezer, 1999, seconds_after=20)
    await watts(hass, freezer, 0, seconds_after=20)
    await tick(hass, freezer, 31)
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["from"] > 60 and run["trigger"] == "rule" and 60 <= run["rang"] <= 71  # time jumps 70 s at once


async def test_warn_rings_by_countdown_and_attributes_are_not_readings(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Iron", "warn", warn_after=1)
    await watts(hass, freezer, 1200)
    for _ in range(5):
        await tick(hass, freezer, 1)
        hass.states.async_set(SENSOR, "1200", {"voltage": 230})  # only an attribute changes
        await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    await watts(hass, freezer, 0, seconds_after=10)
    await tick(hass, freezer, 31)
    (run,) = kitchen.runtime_data.runs.list(station)
    assert run["trigger"] == "countdown" and run["rang"] == pytest.approx(60, abs=6)
    assert run["t"] == [0.0, 75.0] or len(run["t"]) == 2  # 1200 W, then 0: no repeated readings


async def test_deleting_a_station_deletes_its_runs(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 31)
    assert kitchen.runtime_data.runs.list(station)
    hass.config_entries.async_remove_subentry(kitchen, station)
    await hass.async_block_till_done()
    assert kitchen.runtime_data.runs.list(station) == []
