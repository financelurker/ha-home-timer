"""A run read again from the recorder's history: its real start and its whole curve."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.home_timer.power import Trace
from custom_components.home_timer.runs import make_run, reread_run
from custom_components.home_timer.stations import PowerConfig

from .conftest import setup_area, tick
from .test_power_station import SENSOR, add_power_station, watts

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
CONFIG = PowerConfig(sensor=SENSOR, on_above=10, off_after=600)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder has to come before Home Assistant is set up."""
    yield


def dishwasher() -> list[tuple[datetime, float]]:
    """0–20 min heating, a pause (20–25 min, 2 W), 25–50 min washing, then off."""
    out = [(T0 - timedelta(minutes=5), 0.0)]
    for s in range(0, 50 * 60, 30):
        w = 2000.0 if s < 1200 else 2.0 if s < 1500 else 150.0 + (s % 120)
        out.append((T0 + timedelta(seconds=s), w))
    out.append((T0 + timedelta(minutes=50), 0.0))
    return out


def stored(start_s: float, end_s: float, samples, *, picked_up_at: float | None = None, **extra) -> dict:
    """A stored run as the watcher made it: from ``start_s``, its curve from ``picked_up_at`` on."""
    start = T0 + timedelta(seconds=start_s)
    trace = Trace()
    for when, w in samples:
        t = (when - start).total_seconds()
        if (picked_up_at or 0) <= t <= end_s - start_s:
            trace.add(t, w)
    return {**make_run(start, end_s - start_s, trace, on_above=10, rang=extra.pop("rang", None), trigger="rule", sensor=SENSOR), **extra}


def test_a_run_picked_up_later_gets_its_start_back() -> None:
    samples = dishwasher()
    run = stored(0, 3000, samples, picked_up_at=1800, rang=2900, marks={"done": 2880.0})
    assert run["from"] >= 1800
    new, replaced = reread_run(samples, [run], run["id"], CONFIG, T0 + timedelta(hours=2))
    assert new["id"] == run["id"] and replaced == {run["id"]} and new["refilled"]
    assert new["from"] == 0 and new["t"][0] == 0 and new["duration"] == pytest.approx(3000, abs=1)
    assert new["marks"] == {"done": 2880.0} and new["rang"] == 2900  # the same start: nothing moves


def test_a_run_cut_in_a_pause_is_joined_with_its_beginning() -> None:
    # the old version ended the run in the pause at a reload: the rest became a run of its own
    samples = dishwasher()
    first = stored(0, 1200, samples)  # (if it was stored at all)
    rest = stored(1500, 3000, samples, rang=1400, marks={"done": 1380.0, "ranges": {"wash": [0, 1500]}}, skip=True)
    new, replaced = reread_run(samples, [first, rest], rest["id"], CONFIG, T0 + timedelta(hours=2))
    assert replaced == {first["id"], rest["id"]} and new["id"] == rest["id"]
    assert new["start"] == T0.isoformat() and new["duration"] == pytest.approx(3000, abs=1)
    assert new["marks"] == {"done": 2880.0, "ranges": {"wash": [1500.0, 3000.0]}}  # moved to the real start
    assert new["rang"] == 2900 and new["skip"] is True
    # the whole run read again: nothing more
    assert reread_run(samples, [new], new["id"], CONFIG, T0 + timedelta(hours=2)) is None


async def test_runs_missing_their_start_are_completed_and_can_be_read_again(
    recorder_mock, hass: HomeAssistant, freezer, hass_ws_client
) -> None:
    entry = await setup_area(hass, "Kitchen")
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, entry, "Dishwasher", "until_off", off_after=60)
    await hass.async_block_till_done(wait_background_tasks=True)
    await watts(hass, freezer, 2000, seconds_after=5)
    await watts(hass, freezer, 2100, seconds_after=60)
    # a reload that loses the curve (as the version before kept curves did)
    with patch("custom_components.home_timer.watcher.KEEP_GAP", -1):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    await watts(hass, freezer, 2200, seconds_after=60)
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 61)
    await async_wait_recording_done(hass)
    await tick(hass, freezer, 31)  # completed a little later (the recorder writes in batches)
    await hass.async_block_till_done(wait_background_tasks=True)
    (run,) = entry.runtime_data.runs.list(station)
    assert run["from"] == 0 and run["refilled"] and run["duration"] == pytest.approx(180, abs=2)  # completed at once
    # a run stored before (missing its start, never tried) is completed when the area loads
    store = entry.runtime_data.runs
    cut = [i for i, t in enumerate(run["t"]) if t >= 60]
    run.update(t=[run["t"][i] for i in cut], w=[run["w"][i] for i in cut], **{"from": run["t"][cut[0]]})
    run.pop("refilled"), run.pop("refill_tried", None)
    await store.async_save()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    (run,) = entry.runtime_data.runs.list(station)
    assert run["from"] == 0 and run["refilled"]
    client = await hass_ws_client(hass)
    base = {"type": "home_timer/runs/reread", "entry_id": entry.entry_id, "station_id": station}
    await client.send_json_auto_id({**base, "run_id": run["id"]})
    assert (await client.receive_json())["result"] == {"run": None}  # nothing more
    await client.send_json_auto_id({**base, "run_id": "nope"})
    assert (await client.receive_json())["error"]["code"] == "not_found"
