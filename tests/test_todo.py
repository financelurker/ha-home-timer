"""The area's to-do list: ringing timers only; checking one off acknowledges it."""

from __future__ import annotations

import pytest
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, tick
from .test_engine import events

TODO = "todo.kitchen_home_timer_ringing_timers"


async def items(hass: HomeAssistant) -> list[dict]:
    response = await hass.services.async_call(
        "todo", "get_items", {"entity_id": TODO}, blocking=True, return_response=True
    )
    return response[TODO]["items"]


async def test_only_ringing_timers(hass: HomeAssistant, kitchen, freezer) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 60, "Lid off")])
    engine = kitchen.runtime_data
    state = hass.states.get(TODO)
    assert state.state == "0"
    assert state.attributes["supported_features"] == TodoListEntityFeature.UPDATE_TODO_ITEM
    engine.start_preset(hob, "Rice")
    engine.start("Dough", 1800)
    await hass.async_block_till_done()
    assert await items(hass) == []
    await tick(hass, freezer, 61)
    assert hass.states.get(TODO).state == "1"
    [item] = await items(hass)
    assert item["summary"] == "Rice · Hob 1"
    assert item["status"] == "needs_action"
    assert item["description"] == "Lid off"
    assert item["due"]


async def test_check_off_acknowledges(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer = kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": TODO, "item": timer.id, "status": "completed"}, blocking=True
    )
    await hass.async_block_till_done()
    assert ev["acknowledged"][0].data["by"] == "todo"
    assert await items(hass) == []
    assert hass.states.get(TODO).state == "0"


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
