"""Timers survive a restart: a missed end is reported late, ringing goes on, paused stays paused."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.home_timer.const import (
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REPORT_MISSED,
    DOMAIN,
    STORAGE_VERSION,
)
from custom_components.home_timer.engine import storage_key

from .conftest import setup_area, tick
from .test_engine import SENSOR, events, start


async def restart(hass: HomeAssistant, entry, freezer, downtime: float) -> None:
    """Unload (stores the timers), let time pass with nothing running, set up again."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(downtime)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_still_running_after_restart(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await start(hass, kitchen.data["area_id"])
    await restart(hass, kitchen, freezer, 60)
    assert hass.states.get(SENSOR).attributes["timers"][0]["remaining"] == 540
    await tick(hass, freezer, 541)
    assert ev["finished"][0].data["late"] is False


async def test_ran_out_during_restart(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await start(hass, kitchen.data["area_id"], duration="00:01:00")
    await restart(hass, kitchen, freezer, 120)
    assert ev["finished"][0].data["late"] is True
    timer = hass.states.get(SENSOR).attributes["timers"][0]
    assert timer["status"] == "ringing"
    assert timer["overtime"] >= 60


async def test_missed_timer_dropped_when_not_reported(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_REPORT_MISSED: False})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await restart(hass, entry, freezer, 120)
    assert not ev["finished"]
    assert hass.states.get(SENSOR).state == "0"


async def test_ringing_goes_on_and_dismisses_on_time(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_AUTO_DISMISS_MINUTES: 10})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 61)
    await restart(hass, entry, freezer, 3 * 60)
    assert hass.states.get(SENSOR).attributes["timers"][0]["status"] == "ringing"
    reminders = len(ev["reminder"])
    await tick(hass, freezer, 60)  # minute 4 after the end: reminder every 2 min
    assert len(ev["reminder"]) == reminders + 1
    await restart(hass, entry, freezer, 10 * 60)
    assert ev["acknowledged"][-1].data["by"] == "timeout"
    assert hass.states.get(SENSOR).state == "0"


async def test_paused_stays_paused(hass: HomeAssistant, kitchen, freezer) -> None:
    timer_id = await start(hass, kitchen.data["area_id"])
    await hass.services.async_call(DOMAIN, "pause", {"timer_id": timer_id}, blocking=True)
    await restart(hass, kitchen, freezer, 3600)
    timer = hass.states.get(SENSOR).attributes["timers"][0]
    assert timer["status"] == "paused"
    assert timer["remaining"] == 600


async def test_removing_the_area_removes_its_timers(hass: HomeAssistant, kitchen) -> None:
    await start(hass, kitchen.data["area_id"])
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    assert await Store(hass, STORAGE_VERSION, storage_key(kitchen.entry_id)).async_load()
    await hass.config_entries.async_remove(kitchen.entry_id)
    await hass.async_block_till_done()
    assert await Store(hass, STORAGE_VERSION, storage_key(kitchen.entry_id)).async_load() is None
    assert kitchen.state is ConfigEntryState.NOT_LOADED
