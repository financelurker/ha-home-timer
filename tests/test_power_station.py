"""Power stations: the three kinds driven by a power sensor."""

from __future__ import annotations

from types import MappingProxyType

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
    for name in ("button.kitchen_kettle_start_custom_timer", "number.kitchen_kettle_custom_timer",
                 "button.kitchen_kettle_pause_resume"):
        assert hass.states.get(name) is None, name
    assert hass.states.get("button.kitchen_kettle_stop_acknowledge") is not None
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
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_kettle_stop_acknowledge"}, blocking=True)
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
    await hass.services.async_call("button", "press", {"entity_id": "button.kitchen_iron_stop_acknowledge"}, blocking=True)
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
    # stopped while unloaded
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    hass.states.async_set(SENSOR, "0")
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
