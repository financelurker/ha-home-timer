"""Targets of the actions: area, floor, label, device, entity – resolved to Home Timer areas."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from custom_components.home_timer.const import DOMAIN

from .conftest import setup_area

SENSOR = "sensor.kitchen_home_timer_timers"


async def _start(hass: HomeAssistant, target: dict) -> dict:
    return await hass.services.async_call(
        DOMAIN, "start", {**target, "name": "Tea", "duration": "00:04:00"}, blocking=True, return_response=True
    )


async def test_floor_label_entity_device(hass: HomeAssistant) -> None:
    kitchen = await setup_area(hass, "Kitchen")
    await setup_area(hass, "Bathroom")
    ground = fr.async_get(hass).async_create("Ground floor")
    ar.async_get(hass).async_update(kitchen.data["area_id"], floor_id=ground.floor_id)
    cooking = lr.async_get(hass).async_create("Cooking")
    ar.async_get(hass).async_update(kitchen.data["area_id"], labels={cooking.label_id})

    for target in (
        {"floor_id": ground.floor_id},
        {"label_id": cooking.label_id},
        {"entity_id": SENSOR},
        {"device_id": kitchen.runtime_data.hub_device_id},
        {"area_id": kitchen.data["area_id"]},
    ):
        await _start(hass, target)
    assert len(kitchen.runtime_data.timers) == 5


async def test_moved_hub_device_follows_its_new_area(hass: HomeAssistant) -> None:
    kitchen = await setup_area(hass, "Kitchen")
    living = ar.async_get(hass).async_create("Living room")
    dr.async_get(hass).async_update_device(kitchen.runtime_data.hub_device_id, area_id=living.id)
    await _start(hass, {"area_id": living.id})
    await _start(hass, {"area_id": kitchen.data["area_id"]})  # the entry's own area still works
    assert len(kitchen.runtime_data.timers) == 2


async def test_target_errors(hass: HomeAssistant) -> None:
    kitchen = await setup_area(hass, "Kitchen")
    bathroom = await setup_area(hass, "Bathroom")
    garage = ar.async_get(hass).async_create("Garage")
    with pytest.raises(ServiceValidationError, match="no_area_in_target|does not reach"):
        await _start(hass, {"area_id": garage.id})
    with pytest.raises(ServiceValidationError):
        await _start(hass, {"area_id": [kitchen.data["area_id"], bathroom.data["area_id"]]})
    with pytest.raises(ServiceValidationError):
        await _start(hass, {})
    with pytest.raises(ServiceValidationError):
        await _start(hass, {"device_id": "unknown"})
    assert await hass.config_entries.async_unload(kitchen.entry_id)
    with pytest.raises(ServiceValidationError):
        await _start(hass, {"area_id": kitchen.data["area_id"]})
