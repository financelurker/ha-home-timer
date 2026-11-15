"""Quick timers of an area, custom timers and "save as preset" (area and station)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, tick
from .test_engine import events

MINUTES = "number.kitchen_home_timer_custom_timer"
START = "button.kitchen_home_timer_start_custom_timer"
SAVE = "text.kitchen_home_timer_save_as_preset"
HOB_MINUTES = "number.kitchen_hob_1_custom_timer"
HOB_START = "button.kitchen_hob_1_start_custom_timer"
HOB_SAVE = "text.kitchen_hob_1_save_as_preset"


async def set_minutes(hass, entity_id, value):
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)


async def press(hass, entity_id):
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def save(hass, entity_id, name):
    await hass.services.async_call("text", "set_value", {"entity_id": entity_id, "value": name}, blocking=True)
    await hass.async_block_till_done()


async def test_area_entities(hass: HomeAssistant, kitchen) -> None:
    assert hass.states.get(MINUTES).state == "5.0"
    assert hass.states.get(START) is not None
    assert hass.states.get(SAVE).state == ""


async def test_custom_timer_then_save_as_preset(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await set_minutes(hass, MINUTES, 4)
    await press(hass, START)
    started = ev["started"][0].data
    assert (started["name"], started["duration"], started["station"]) == ("Timer", 240, None)
    await tick(hass, freezer, 60)
    await save(hass, SAVE, " Tea ")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Tea", 240)]
    timer = next(iter(kitchen.runtime_data.timers.values()))
    assert (timer.name, timer.preset, round(timer.remaining(timer.started_at) )) == ("Tea", "Tea", 240)
    assert hass.states.get("button.kitchen_home_timer_start_tea") is not None
    assert hass.states.get(START) is not None  # the custom row stays
    assert hass.states.get(SAVE).state == ""
    await press(hass, "button.kitchen_home_timer_start_tea")
    assert ev["started"][-1].data["preset"] == "Tea"
    assert len(kitchen.runtime_data.timers) == 2  # timers without a station run side by side


async def test_same_name_updates_the_preset(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 4)
    await press(hass, START)
    await save(hass, SAVE, "Tea")
    await set_minutes(hass, MINUTES, 5)
    await press(hass, START)
    await save(hass, SAVE, "tea")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Tea", 300)]


async def test_save_without_a_custom_timer_saves_the_duration(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 7)
    await save(hass, SAVE, "Steep")
    assert [(p["name"], p["duration"]) for p in kitchen.options["presets"]] == [("Steep", 420)]
    assert kitchen.runtime_data.timers == {}


async def test_last_custom_timer_is_meant(hass: HomeAssistant, kitchen) -> None:
    await set_minutes(hass, MINUTES, 3)
    await press(hass, START)
    await set_minutes(hass, MINUTES, 9)
    await press(hass, START)
    await save(hass, SAVE, "Nine")
    names = sorted((t.name, round(t.duration)) for t in kitchen.runtime_data.timers.values())
    assert names == [("Nine", 540), ("Timer", 180)]


async def test_station_custom_and_save(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    ev = events(hass)
    await set_minutes(hass, HOB_MINUTES, 9)
    await press(hass, "button.kitchen_hob_1_start_rice")
    await press(hass, HOB_START)  # replaces Rice
    assert ev["cancelled"][0].data["reason"] == "replaced"
    assert hass.states.get("sensor.kitchen_hob_1").attributes["timer_name"] == "Timer"
    await save(hass, HOB_SAVE, "Pasta")
    presets = kitchen.subentries[hob].data["presets"]
    assert [(p["name"], p["duration"]) for p in presets] == [("Rice", 600), ("Pasta", 540)]
    assert hass.states.get("sensor.kitchen_hob_1").attributes["timer_name"] == "Pasta"
    assert hass.states.get("button.kitchen_hob_1_start_pasta") is not None
    assert kitchen.options.get("presets", []) == []


async def test_custom_duration_survives_a_reload(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1")
    await set_minutes(hass, MINUTES, 12)
    await set_minutes(hass, HOB_MINUTES, 3)
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MINUTES).state == "12.0"
    assert hass.states.get(HOB_MINUTES).state == "3.0"
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
    assert err.value.translation_key == "unknown_preset"
