"""Timers survive a restart: a missed end is reported late, ringing goes on, paused stays paused."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.home_timer.const import (
    CONF_AREA_ID,
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_REPORT_MISSED,
    CONF_RING_UNTIL_ACK,
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


async def test_late_timer_rings_from_the_start_on(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_AUTO_DISMISS_MINUTES: 30})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await restart(hass, entry, freezer, 3 * 3600)
    assert ev["finished"][0].data["late"] is True
    assert not ev["acknowledged"]
    timer = hass.states.get(SENSOR).attributes["timers"][0]
    assert timer["overtime"] >= 3 * 3600 - 60
    await tick(hass, freezer, 120)
    assert len(ev["reminder"]) == 1
    await tick(hass, freezer, 28 * 60)
    assert ev["acknowledged"][0].data["by"] == "timeout"


async def test_ringing_off_acknowledges_ringing_timers(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_AUTO_DISMISS_MINUTES: 0})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 61)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_RING_UNTIL_ACK: False})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert ev["acknowledged"][0].data["by"] == "timeout"
    assert hass.states.get(SENSOR).state == "0"
    assert hass.states.get("binary_sensor.kitchen_home_timer_ringing").state == "off"


async def test_timers_survive_an_options_reload(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await start(hass, kitchen.data["area_id"])
    hass.config_entries.async_update_entry(kitchen, options={CONF_REMINDER_MINUTES: 5})
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 601)
    assert len(ev["finished"]) == 1


async def test_unreadable_stored_timers_are_skipped(hass: HomeAssistant, hass_storage) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id})
    good = {"id": "a", "name": "Ok", "duration": 60, "status": "paused", "paused_remaining": 30,
            "started_at": "2026-11-15T10:00:00+00:00"}
    hass_storage[storage_key(entry.entry_id)] = {
        "version": STORAGE_VERSION, "key": storage_key(entry.entry_id),
        "data": {"timers": [
            good,
            {"id": "b", "name": "Bad stamp", "duration": 60, "status": "active", "ends_at": "nonsense"},
            {"id": "c", "name": "Idle", "duration": 60, "status": "idle"},
            {"id": "d", "name": "No end", "duration": 60, "status": "active"},
            "not a dict",
        ]},
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert list(entry.runtime_data.timers) == ["a"]


async def test_storage_that_is_not_a_dict(hass: HomeAssistant, hass_storage) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id})
    hass_storage[storage_key(entry.entry_id)] = {"version": STORAGE_VERSION, "key": storage_key(entry.entry_id), "data": []}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.timers == {}


async def test_missed_timer_reaches_automations_at_boot(hass: HomeAssistant, hass_storage, freezer) -> None:
    """At a real boot, late events wait until Home Assistant has started and automations listen."""
    hass.set_state(CoreState.not_running)
    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id})
    hass_storage[storage_key(entry.entry_id)] = {
        "version": STORAGE_VERSION, "key": storage_key(entry.entry_id),
        "data": {"timers": [{"id": "a", "name": "Rice", "duration": 600, "status": "active",
                             "started_at": "2026-11-15T09:00:00+00:00", "ends_at": "2026-11-15T09:10:00+00:00"}]},
    }
    freezer.move_to("2026-11-15T10:00:00+00:00")
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(hass, "automation", {"automation": {
        "triggers": {"trigger": "event", "event_type": "home_timer_finished"},
        "actions": {"action": "test.automation", "data": {"late": "{{ trigger.event.data.late }}"}},
    }})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not calls
    await hass.async_start()
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["late"] is True
