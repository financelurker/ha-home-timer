"""Assign: a timer started without a station is attached to a station later."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, setup_area, tick
from .test_engine import events

HOB = "sensor.kitchen_hob_1"


async def assign(hass: HomeAssistant, timer_id: str, target: dict) -> dict:
    return await hass.services.async_call(
        DOMAIN, "assign", {"timer_id": timer_id, **target}, blocking=True, return_response=True
    )


async def test_assign_keeps_time_and_state(hass: HomeAssistant, kitchen, freezer) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    engine = kitchen.runtime_data
    ev = events(hass)
    assigned = async_capture_events(hass, f"{DOMAIN}_assigned")
    timer = engine.start("Dough", 1800, source={"recipe": 12})
    await tick(hass, freezer, 600)
    response = await assign(hass, timer.id, {"entity_id": HOB})
    await hass.async_block_till_done()
    assert response["timer"]["station"] == hob
    assert response["timer"]["remaining"] == 1200
    state = hass.states.get(HOB)
    assert state.state == "active"
    assert state.attributes["timer_name"] == "Dough"
    data = assigned[0].data
    assert data["station"] == hob
    assert data["station_name"] == "Hob 1"
    assert data["previous_station_name"] is None
    assert data["source"] == {"recipe": 12}
    await tick(hass, freezer, 1201)
    assert ev["finished"][0].data["station_name"] == "Hob 1"


async def test_assign_replaces_and_moves(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    oven = await add_station(hass, kitchen, "Oven")
    engine = kitchen.runtime_data
    ev = events(hass)
    assigned = async_capture_events(hass, f"{DOMAIN}_assigned")
    rice = engine.start_preset(hob, "Rice")
    dough = engine.start("Dough", 1800)
    oven_device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, oven), kitchen.entry_id).id
    await assign(hass, dough.id, {"entity_id": HOB})
    await hass.async_block_till_done()
    assert ev["cancelled"][0].data["timer_id"] == rice.id
    assert ev["cancelled"][0].data["reason"] == "replaced"
    await assign(hass, dough.id, {"device_id": oven_device})  # moves
    await hass.async_block_till_done()
    assert assigned[-1].data["previous_station_name"] == "Hob 1"
    assert engine.timer_at(hob) is None
    assert engine.timer_at(oven).id == dough.id
    await assign(hass, dough.id, {"device_id": oven_device})  # own station: nothing happens
    await hass.async_block_till_done()
    assert len(assigned) == 2


async def test_assign_survives_restart(hass: HomeAssistant, kitchen, freezer) -> None:
    hob = await add_station(hass, kitchen, "Hob 1")
    timer = kitchen.runtime_data.start("Dough", 1800)
    await assign(hass, timer.id, {"entity_id": HOB})
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert kitchen.runtime_data.timer_at(hob).id == timer.id


async def test_assign_errors(hass: HomeAssistant, kitchen) -> None:
    await add_station(hass, kitchen, "Hob 1")
    await add_station(hass, kitchen, "Oven")
    bathroom = await setup_area(hass, "Bathroom")
    await add_station(hass, bathroom, "Bathtub")
    timer = kitchen.runtime_data.start("Dough", 1800)
    with pytest.raises(ServiceValidationError):  # unknown timer
        await assign(hass, "nope", {"entity_id": HOB})
    with pytest.raises(ServiceValidationError):  # no station
        await assign(hass, timer.id, {"area_id": kitchen.data["area_id"]})
    with pytest.raises(ServiceValidationError):  # two stations
        await assign(hass, timer.id, {"entity_id": [HOB, "sensor.kitchen_oven"]})
    with pytest.raises(ServiceValidationError):  # another area
        await assign(hass, timer.id, {"entity_id": "sensor.bathroom_bathtub"})
    with pytest.raises(ServiceValidationError):  # station and area mixed
        await assign(hass, timer.id, {"entity_id": HOB, "area_id": bathroom.data["area_id"]})
