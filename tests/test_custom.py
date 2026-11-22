"""Quick timers of an area, custom timers and "save as preset" (area and station)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, tick
from .test_engine import events

MINUTES = "number.kitchen_home_timer_custom_timer_minutes"
CUSTOM = "switch.kitchen_home_timer_custom_timer"
SAVE = "text.kitchen_home_timer_save_as_preset"
HOB_MINUTES = "number.kitchen_hob_1_custom_timer_minutes"
HOB_CUSTOM = "switch.kitchen_hob_1_custom_timer"
HOB_SAVE = "text.kitchen_hob_1_save_as_preset"


async def set_minutes(hass, entity_id, value):
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)


async def press(hass, entity_id):
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def on(hass, entity_id):
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def off(hass, entity_id):
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def save(hass, entity_id, name):
    await hass.services.async_call("text", "set_value", {"entity_id": entity_id, "value": name}, blocking=True)
    await hass.async_block_till_done()


async def test_area_entities(hass: HomeAssistant, kitchen) -> None:
    assert hass.states.get(MINUTES).state == "5"
    assert hass.states.get(CUSTOM).state == "off"
    assert hass.states.get(SAVE).state == ""


async def test_custom_timer_then_save_as_preset(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await set_minutes(hass, MINUTES, 4)
    await on(hass, CUSTOM)
    started = ev["started"][0].data
    assert (started["name"], started["duration"], started["station"]) == ("Timer", 240, None)
    await tick(hass, freezer, 60)
    await save(hass, SAVE, " Tea ")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Tea", 240)]
    timer = next(iter(kitchen.runtime_data.timers.values()))
    assert (timer.name, timer.preset, round(timer.remaining(timer.started_at) )) == ("Tea", "Tea", 240)
    assert hass.states.get("switch.kitchen_home_timer_tea").state == "on"  # the custom timer's run is Tea's now
    assert hass.states.get(CUSTOM).state == "off"
    assert hass.states.get(SAVE).state == ""
    await on(hass, CUSTOM)
    assert len(kitchen.runtime_data.timers) == 2  # timers without a station run side by side


async def test_same_name_updates_the_preset(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 4)
    await on(hass, CUSTOM)
    await save(hass, SAVE, "Tea")
    await set_minutes(hass, MINUTES, 5)
    await on(hass, CUSTOM)
    await save(hass, SAVE, "tea")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Tea", 300)]


async def test_save_without_a_custom_timer_saves_the_duration(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 7)
    await save(hass, SAVE, "Steep")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Steep", 420)]
    assert kitchen.runtime_data.timers == {}


async def test_the_custom_timer_has_one_run(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 3)
    await on(hass, CUSTOM)
    await set_minutes(hass, MINUTES, 9)
    await on(hass, CUSTOM)  # on already: nothing
    assert [round(t.duration) for t in kitchen.runtime_data.timers.values()] == [180]
    await off(hass, CUSTOM)
    assert kitchen.runtime_data.timers == {}
    await on(hass, CUSTOM)
    await save(hass, SAVE, "Nine")
    assert [(t.name, round(t.duration)) for t in kitchen.runtime_data.timers.values()] == [("Nine", 540)]


async def test_station_custom_and_save(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    ev = events(hass)
    await set_minutes(hass, HOB_MINUTES, 9)
    await on(hass, "switch.kitchen_hob_1_rice")
    await on(hass, HOB_CUSTOM)  # replaces Rice
    assert ev["cancelled"][0].data["reason"] == "replaced"
    assert hass.states.get("sensor.kitchen_hob_1").attributes["timer_name"] == "Timer"
    await save(hass, HOB_SAVE, "Pasta")
    presets = kitchen.subentries[hob].data["presets"]
    assert [(p["name"], p["duration"]) for p in presets] == [("Rice", 600), ("Pasta", 540)]
    assert hass.states.get("sensor.kitchen_hob_1").attributes["timer_name"] == "Pasta"
    assert hass.states.get("switch.kitchen_hob_1_pasta").state == "on"  # the custom timer's run is Pasta's now
    assert kitchen.options.get("presets", []) == []


async def test_custom_duration_survives_a_reload(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1")
    await set_minutes(hass, MINUTES, 12)
    await set_minutes(hass, HOB_MINUTES, 3)
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MINUTES).state == "12"
    assert hass.states.get(HOB_MINUTES).state == "3"
    assert kitchen.runtime_data.custom_duration(hob) == 180


async def test_quick_timer_by_action(hass: HomeAssistant, kitchen) -> None:
    hass.config_entries.async_update_entry(
        kitchen, options={"presets": [{"id": "q1", "name": "Tea", "duration": 240, "note": "Green"}]}
    )
    await hass.async_block_till_done()
    response = await hass.services.async_call(
        DOMAIN, "start", {"area_id": kitchen.data["area_id"], "preset": "tea"}, blocking=True, return_response=True
    )
    assert (response["timer"]["name"], response["timer"]["duration"], response["timer"]["note"]) == ("Tea", 240, "Green")
    assert response["timer"]["station"] is None
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start", {"area_id": kitchen.data["area_id"], "preset": "Soup"}, blocking=True)
    assert err.value.translation_key == "unknown_quick_timer"


async def test_moved_custom_timer_is_no_longer_meant(hass: HomeAssistant, kitchen) -> None:
    await add_station(hass, kitchen, "Hob 1")
    await set_minutes(hass, MINUTES, 4)
    await on(hass, CUSTOM)
    timer = next(iter(kitchen.runtime_data.timers.values()))
    await hass.services.async_call(DOMAIN, "assign", {"timer_id": timer.id, "entity_id": "sensor.kitchen_hob_1"}, blocking=True)
    await save(hass, SAVE, "Tea")
    assert kitchen.runtime_data.timers[timer.id].name == "Timer"
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Tea", 240)]


async def test_saved_duration_is_the_started_one(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 3)
    await on(hass, CUSTOM)
    timer = next(iter(kitchen.runtime_data.timers.values()))
    kitchen.runtime_data.add_time(timer.id, 120)
    await hass.async_block_till_done()
    assert hass.states.get(SAVE).attributes["duration"] == 180
    await save(hass, SAVE, "Short")
    assert kitchen.options["presets"][0]["duration"] == 180


async def test_unknown_quick_timer_message(hass: HomeAssistant, kitchen) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start", {"area_id": kitchen.data["area_id"], "preset": "Soup"}, blocking=True)
    assert err.value.translation_key == "unknown_quick_timer"
    assert "quick timer" in str(err.value)


async def test_save_while_ringing_and_onto_a_preset_in_other_case(hass: HomeAssistant, kitchen, freezer) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600, "Lid on")])
    await set_minutes(hass, HOB_MINUTES, 1)
    await on(hass, HOB_CUSTOM)
    await tick(hass, freezer, 61)
    assert hass.states.get("sensor.kitchen_hob_1").state == "ringing"
    await save(hass, HOB_SAVE, "RICE")
    presets = kitchen.subentries[hob].data["presets"]
    assert [(p["name"], p["duration"]) for p in presets] == [("Rice", 60)]
    attrs = hass.states.get("sensor.kitchen_hob_1").attributes
    assert (attrs["timer_name"], attrs["note"]) == ("Rice", "Lid on")


async def test_upgrade_without_new_store_keys(hass: HomeAssistant, hass_storage) -> None:
    from homeassistant.helpers import area_registry as ar
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.home_timer.const import CONF_AREA_ID, STORAGE_VERSION
    from custom_components.home_timer.engine import storage_key

    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id},
                            options={"reminder_minutes": 3})
    hass_storage[storage_key(entry.entry_id)] = {"version": STORAGE_VERSION, "key": storage_key(entry.entry_id),
                                                 "data": {"timers": [], "last_custom": {"": {"timer_id": "x"}}}}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MINUTES).state == "5"
    await save(hass, SAVE, "Five")  # corrupt last_custom is ignored: the custom duration is saved
    assert entry.options["presets"][0]["duration"] == 300
    assert entry.options["reminder_minutes"] == 3
