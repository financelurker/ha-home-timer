"""Stored runs: earlier runs are read once from the recorder's history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import setup_area
from .fixtures import moka
from .test_power_station import SENSOR, add_power_station

DROP = {"type": "drop", "percent": 10, "window": 60, "ignore": 120}
T0 = datetime(2026, 11, 15, 13, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder has to come before Home Assistant is set up."""
    yield



async def test_earlier_runs_are_read_from_the_history(recorder_mock, hass: HomeAssistant, freezer) -> None:
    freezer.move_to(T0)
    entry = await setup_area(hass, "Kitchen")
    hass.states.async_set(SENSOR, "0")
    last = 0
    for t, w in moka.SAMPLES:
        freezer.tick(timedelta(seconds=t - last))
        last = t
        hass.states.async_set(SENSOR, str(w))
        await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=5))
    await async_wait_recording_done(hass)
    station = await add_power_station(hass, entry, "Moka pot", "until_off", done=DROP)
    await hass.async_block_till_done(wait_background_tasks=True)
    store = entry.runtime_data.runs
    (run,) = store.list(station)
    assert run["source"] == "history" and run["simulated"] and run["trigger"] == "rule"
    assert station in store.imported
    # read again (e.g. after a failed read): only before the first stored run, so nothing twice
    from custom_components.home_timer.runs import async_import_history

    store.imported.discard(station)
    config = entry.runtime_data.stations[station].power
    assert await async_import_history(hass, store, station, config) == 0
    assert len(store.list(station)) == 1
    # only once: a reload does not read it again
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(entry.runtime_data.runs.list(station)) == 1
