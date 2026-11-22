"""Power stations: the three kinds driven by a power sensor."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant

from .conftest import tick
from .fixtures import moka
from .test_engine import events

SENSOR = "sensor.plug_power"


async def add_power_station(hass, entry, name, kind, presets=None, **power) -> str:
    data = {
        "icon": None,
        "presets": [{"id": f"p{i}", "name": n, "duration": d} for i, (n, d) in enumerate(presets or [])],
        "mode": "power",
        "power": {"sensor": SENSOR, "kind": kind, "on_above": 10, "off_after": 30, **power},
    }
    subentry = ConfigSubentry(data=MappingProxyType(data), subentry_type="station", title=name, unique_id=None)
    hass.config_entries.async_add_subentry(entry, subentry)
    await hass.async_block_till_done()
    return subentry.subentry_id


async def watts(hass, freezer, value, seconds_after=0):
    if seconds_after:
        await tick(hass, freezer, seconds_after)
    hass.states.async_set(SENSOR, str(value))
    await hass.async_block_till_done()


async def test_until_off_with_switch_off_rule(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off", done={"type": "off"})
    ev = events(hass)
    assert hass.states.get("binary_sensor.kitchen_kettle_running").state == "off"
    for name in ("button.kitchen_kettle_start_custom_timer", "number.kitchen_kettle_custom_timer_minutes",
                 "button.kitchen_kettle_pause_resume"):
        assert hass.states.get(name) is None, name
    assert hass.states.get("button.kitchen_kettle_stop") is not None
    await watts(hass, freezer, 2100)
    assert hass.states.get("binary_sensor.kitchen_kettle_running").state == "on"
    state = hass.states.get("sensor.kitchen_kettle")
    assert state.state == "active"
    assert state.attributes["remaining"] is None
    assert state.attributes["done_rule"] == "when it switches off"
    assert hass.states.get("sensor.kitchen_kettle_timer_end").state == "unknown"  # nothing learned yet
    await watts(hass, freezer, 0, seconds_after=180)
    await tick(hass, freezer, 31)
    assert ev["finished"][0].data["trigger"] == "off"
    assert hass.states.get("sensor.kitchen_kettle").state == "ringing"
    assert hass.states.get("binary_sensor.kitchen_kettle_running").state == "off"
    assert hass.states.get("sensor.kitchen_kettle_runs").state == "1"
    assert float(hass.states.get("sensor.kitchen_kettle_usual_duration").state) == pytest.approx(3, abs=0.1)
    # the next run shows the expected end
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_kettle_stop"}, blocking=True)
    await watts(hass, freezer, 2100, seconds_after=60)
    assert hass.states.get("sensor.kitchen_kettle_timer_end").state not in ("unknown", "unavailable")


async def test_until_off_with_the_moka_drop_rule(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(
        hass, kitchen, "Moka pot", "until_off", done={"type": "drop", "percent": 10, "window": 60, "ignore": 120}
    )
    ev = events(hass)
    last = 0
    fired_at = None
    for t, w in moka.SAMPLES:
        await watts(hass, freezer, w, seconds_after=t - last)
        last = t
        if fired_at is None and ev["finished"]:
            fired_at = t
    assert ev["finished"][0].data["trigger"] == "rule"
    assert 0 <= fired_at - moka.MARK_DONE <= 70  # samples arrive every 5–50 s; the 5 s tick evaluates in between
    await tick(hass, freezer, 31)
    assert len(ev["finished"]) == 1  # switching off later does not ring again
    assert hass.states.get("sensor.kitchen_moka_pot_runs").state == "1"


async def test_start_preset_on_power(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Rice cooker", "start_preset", presets=[("Rice", 1200)], preset="p0")
    ev = events(hass)
    assert hass.states.get("button.kitchen_rice_cooker_start_rice") is not None  # manual controls stay
    await watts(hass, freezer, 500)
    assert ev["started"][0].data["preset"] == "Rice"
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 31)
    assert ev["cancelled"][0].data["reason"] == "power_off"


async def test_warn_if_it_runs_too_long(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Iron", "warn", warn_after=60)
    ev = events(hass)
    await watts(hass, freezer, 1200)
    await tick(hass, freezer, 3601)
    assert hass.states.get("sensor.kitchen_iron").state == "ringing"
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_iron_stop"}, blocking=True)
    await tick(hass, freezer, 10)
    assert hass.states.get("sensor.kitchen_iron").state == "active"  # still on: warns again
    await watts(hass, freezer, 0)
    await tick(hass, freezer, 35)
    assert ev["cancelled"][-1].data["reason"] == "power_off"
    assert hass.states.get("sensor.kitchen_iron").state == "idle"


async def test_running_across_a_reload(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_kettle").state == "active"
    assert len(kitchen.runtime_data.timers) == 1
    # stopped while unloaded: with its curve kept (a short gap), "stopped after" counts as usual …
    await watts(hass, freezer, 2001, seconds_after=60)
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "0")
    assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_kettle").state == "active"
    await tick(hass, freezer, 31)
    assert hass.states.get("sensor.kitchen_kettle").state == "ringing"
    # … without it (a long gap), at once
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_kettle_stop"}, blocking=True)
    await watts(hass, freezer, 2000, seconds_after=5)
    assert hass.states.get("sensor.kitchen_kettle").state == "active"
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "0")
    with patch("custom_components.home_timer.watcher.KEEP_GAP", -1):  # as after a long gap
        assert await hass.config_entries.async_setup(kitchen.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_kettle").state == "ringing"


async def test_unavailable_readings_are_ignored(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, "unavailable", seconds_after=10)
    await tick(hass, freezer, 60)
    assert hass.states.get("sensor.kitchen_kettle").state == "active"


async def test_open_timer_cannot_be_paused(hass: HomeAssistant, kitchen, freezer) -> None:
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.home_timer.const import DOMAIN

    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "pause", {"entity_id": "sensor.kitchen_kettle"}, blocking=True)
    assert err.value.translation_key == "open_timer"


def engine_of(entry):
    return entry.runtime_data


async def reload(hass, entry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "done",
    [
        {"type": "cycling", "level": 50, "window": 60, "share": 60, "ignore": 120},
        {"type": "knee", "factor": 3, "window": 60, "baseline": 180, "ignore": 120},
        {"type": "drop", "percent": 10, "window": 60, "ignore": 120},
    ],
)
async def test_look_back_rules_after_a_reload(hass: HomeAssistant, kitchen, freezer, done) -> None:
    """Before the pickup nothing is known – it must not count as 0 W."""
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Boiler", "until_off", done=done)
    ev = events(hass)
    await watts(hass, freezer, 2000)
    await tick(hass, freezer, 300)
    await reload(hass, kitchen)
    for i in range(60):  # steady with a little noise for 5 minutes
        await watts(hass, freezer, 2000 + (3 if i % 2 else -3), seconds_after=5)
    assert not ev["finished"]
    assert hass.states.get("sensor.kitchen_boiler").state == "active"


async def test_sensor_unknown_at_start_picks_up_later(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Rice cooker", "start_preset", presets=[("Rice", 1200)], preset="p0")
    ev = events(hass)
    await watts(hass, freezer, 500)
    started = engine_of(kitchen).timer_at(next(iter(kitchen.subentries))).id
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "unknown")
    assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    await watts(hass, freezer, 500, seconds_after=20)
    assert not ev["cancelled"]  # not replaced: the same countdown goes on
    assert engine_of(kitchen).timers[started].status == "active"
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 31)
    assert ev["cancelled"][0].data["reason"] == "power_off"


async def test_until_off_unknown_at_start_does_not_ring(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "unavailable")
    assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_kettle").state == "active"
    await watts(hass, freezer, 2000, seconds_after=10)
    assert hass.states.get("sensor.kitchen_kettle").state == "active"
    assert hass.states.get("binary_sensor.kitchen_kettle_running").state == "on"


async def test_stopped_during_downtime(hass: HomeAssistant, kitchen, freezer) -> None:
    """The downtime is not counted as a run; a warning is not left behind."""
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Iron", "warn", warn_after=60)
    ev = events(hass)
    await watts(hass, freezer, 1200)
    await tick(hass, freezer, 60)
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "0")
    with patch("custom_components.home_timer.watcher.KEEP_GAP", -1):  # as after a long gap
        assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kitchen_iron").state == "idle"
    assert ev["cancelled"][0].data["reason"] == "power_off"
    assert engine_of(kitchen).power_stats.get(station, {}).get("runs", 0) == 0


async def test_warn_stop_silences_the_run(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Iron", "warn", warn_after=60)
    ev = events(hass)
    await watts(hass, freezer, 1200)
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_iron_stop"}, blocking=True)
    await tick(hass, freezer, 10)
    assert hass.states.get("sensor.kitchen_iron").state == "idle"  # stopped: no new warning this run
    assert len(ev["started"]) == 1


async def test_warn_leaves_your_own_timer_alone(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Iron", "warn", warn_after=60)
    ev = events(hass)
    await watts(hass, freezer, 1200)
    engine = engine_of(kitchen)
    mine = engine.start("Shirts", 600, station=station)
    await tick(hass, freezer, 20)
    assert engine.timer_at(station).id == mine.id
    assert [e.data["reason"] for e in ev["cancelled"]] == ["replaced"]  # only the warning, by your start
    await watts(hass, freezer, 0)
    await tick(hass, freezer, 35)
    assert engine.timer_at(station).id == mine.id  # not ours: switching off leaves it


async def test_snoozing_a_finished_open_timer(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off")
    await watts(hass, freezer, 2000)
    await watts(hass, freezer, 0, seconds_after=120)
    await tick(hass, freezer, 31)
    engine = engine_of(kitchen)
    timer = engine.timer_at(station)
    assert timer.status == "ringing"
    assert timer.duration == pytest.approx(120, abs=1)  # ended when it went off, not 30 s later
    engine.add_time(timer.id, 60)
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.kitchen_kettle").attributes
    assert attrs["remaining"] == pytest.approx(60, abs=1)
    assert timer.open is False
    engine.pause(timer.id)


async def test_blips_are_not_runs(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    station = await add_power_station(hass, kitchen, "Kettle", "until_off")
    ev = events(hass)
    await watts(hass, freezer, 12)
    await watts(hass, freezer, 0, seconds_after=1)
    await tick(hass, freezer, 31)
    assert not ev["finished"]
    assert ev["cancelled"][0].data["reason"] == "power_off"
    assert engine_of(kitchen).power_stats.get(station, {}).get("runs", 0) == 0


async def test_a_moved_timer_is_left_alone(hass: HomeAssistant, kitchen, freezer) -> None:
    hass.states.async_set(SENSOR, "0")
    await add_power_station(hass, kitchen, "Kettle", "until_off")
    other = await add_power_station(hass, kitchen, "Hob", "until_off", sensor="sensor.other")
    ev = events(hass)
    await watts(hass, freezer, 2000)
    engine = engine_of(kitchen)
    timer = next(iter(engine.timers.values()))
    engine.assign(timer.id, other)
    await watts(hass, freezer, 0, seconds_after=60)
    await tick(hass, freezer, 31)
    assert not ev["finished"]
    assert engine.timers[timer.id].status == "active"
