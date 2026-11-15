"""One to-do list for all areas: ringing timers only; checking one off acknowledges it."""

from __future__ import annotations

import pytest
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import add_station, setup_area, tick
from .test_engine import events

TODO = "todo.home_timer_ringing_timers"


async def items(hass: HomeAssistant) -> list[dict]:
    response = await hass.services.async_call(
        "todo", "get_items", {"entity_id": TODO}, blocking=True, return_response=True
    )
    return response[TODO]["items"]


async def test_ringing_timers_of_all_areas(hass: HomeAssistant, kitchen, freezer) -> None:
    bathroom = await setup_area(hass, "Bathroom")
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 60, "Lid off")])
    state = hass.states.get(TODO)
    assert state.state == "0"
    assert state.attributes["supported_features"] == TodoListEntityFeature.UPDATE_TODO_ITEM
    kitchen.runtime_data.start_preset(hob, "Rice")
    kitchen.runtime_data.start("Dough", 1800)
    await tick(hass, freezer, 30)
    bathroom.runtime_data.start("Bath", 60)
    await hass.async_block_till_done()
    assert await items(hass) == []
    await tick(hass, freezer, 31)
    assert hass.states.get(TODO).state == "1"
    await tick(hass, freezer, 30)
    listed = await items(hass)
    assert [i["summary"] for i in listed] == ["Rice · Hob 1 · Kitchen", "Bath · Bathroom"]
    assert listed[0]["status"] == "needs_action"
    assert listed[0]["description"] == "Lid off"
    assert listed[0]["due"]


async def test_check_off_acknowledges(hass: HomeAssistant, kitchen, freezer) -> None:
    bathroom = await setup_area(hass, "Bathroom")
    ev = events(hass)
    timer = bathroom.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": TODO, "item": timer.id, "status": "completed"}, blocking=True
    )
    await hass.async_block_till_done()
    assert ev["acknowledged"][0].data["by"] == "todo"
    assert ev["acknowledged"][0].data["area_id"] == bathroom.data["area_id"]
    assert await items(hass) == []


async def test_unloaded_area_leaves_the_list(hass: HomeAssistant, kitchen, freezer) -> None:
    kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    assert hass.states.get(TODO).state == "1"
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TODO).state == "0"
    assert await hass.config_entries.async_setup(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TODO).state == "1"


async def test_nothing_else_can_be_done(hass: HomeAssistant, kitchen, freezer) -> None:
    timer = kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "todo", "update_item", {"entity_id": TODO, "item": timer.id, "rename": "Coffee"}, blocking=True
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("todo", "add_item", {"entity_id": TODO, "item": "Milk"}, blocking=True)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("todo", "remove_item", {"entity_id": TODO, "item": timer.id}, blocking=True)
    assert len(await items(hass)) == 1


async def test_area_rename_updates_the_list(hass: HomeAssistant, kitchen, freezer) -> None:
    from homeassistant.helpers import area_registry as ar

    kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    ar.async_get(hass).async_update(kitchen.data["area_id"], name="Cuisine")
    await hass.async_block_till_done()
    assert [i["summary"] for i in await items(hass)] == ["Tea · Cuisine"]


async def test_assist_add_and_remove_are_refused_cleanly(hass: HomeAssistant, kitchen, freezer, caplog) -> None:
    from homeassistant.helpers import intent
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "intent", {})
    assert await async_setup_component(hass, "todo", {})
    kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    name = hass.states.get(TODO).name
    for intent_type in ("HassListAddItem", "HassListRemoveItem"):
        with pytest.raises(intent.IntentError):
            await intent.async_handle(hass, "test", intent_type, {"item": {"value": "Tea · Kitchen"}, "name": {"value": name}})
    assert "Traceback" not in caplog.text
    await intent.async_handle(hass, "test", "HassListCompleteItem", {"item": {"value": "Tea · Kitchen"}, "name": {"value": name}})
    await hass.async_block_till_done()
    assert await items(hass) == []


async def test_list_goes_with_the_last_area_and_comes_back(hass: HomeAssistant, kitchen) -> None:
    from homeassistant.helpers import entity_registry as er

    bathroom = await setup_area(hass, "Bathroom")
    await hass.config_entries.async_remove(bathroom.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TODO) is not None
    await hass.config_entries.async_remove(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TODO) is None
    assert er.async_get(hass).async_get(TODO) is None
    await setup_area(hass, "Laundry")
    assert hass.states.get(TODO) is not None
