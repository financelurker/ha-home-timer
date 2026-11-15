"""Device triggers of stations and of the area's device."""

from __future__ import annotations

from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_get_device_automations, async_mock_service

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, tick


async def test_triggers_listed(hass: HomeAssistant, kitchen) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 60)])
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id)
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, device.id)
    assert {t["type"] for t in triggers if t["domain"] == DOMAIN} == {
        "started", "assigned", "finished", "reminder", "acknowledged",
    }


async def test_station_and_area_triggers_fire(hass: HomeAssistant, kitchen, freezer) -> None:
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 60)])
    devices = dr.async_get(hass)
    hob_device = devices.async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id).id
    hub_device = kitchen.runtime_data.hub_device_id
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(hass, "automation", {"automation": [
        {"triggers": {"trigger": "device", "domain": DOMAIN, "device_id": hob_device, "type": "finished"},
         "actions": {"action": "test.automation", "data": {"who": "station"}}},
        {"triggers": {"trigger": "device", "domain": DOMAIN, "device_id": hub_device, "type": "finished"},
         "actions": {"action": "test.automation", "data": {"who": "area"}}},
    ]})
    kitchen.runtime_data.start_preset(hob, "Rice")
    kitchen.runtime_data.start("Free", 60)
    await tick(hass, freezer, 61)
    assert sorted(c.data["who"] for c in calls) == ["area", "area", "station"]


async def test_all_trigger_types_and_other_areas(hass: HomeAssistant, kitchen, freezer) -> None:
    from .conftest import setup_area

    bathroom = await setup_area(hass, "Bathroom")
    hub_device = kitchen.runtime_data.hub_device_id
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(hass, "automation", {"automation": [
        {"triggers": {"trigger": "device", "domain": DOMAIN, "device_id": hub_device, "type": kind},
         "actions": {"action": "test.automation", "data": {"kind": kind}}}
        for kind in ("started", "finished", "reminder", "acknowledged")
    ]})
    bathroom.runtime_data.start("Bath", 60)
    timer = kitchen.runtime_data.start("Tea", 60)
    await tick(hass, freezer, 61)
    await tick(hass, freezer, 120)
    kitchen.runtime_data.acknowledge(timer.id, by="action")
    await hass.async_block_till_done()
    assert [c.data["kind"] for c in calls] == ["started", "finished", "reminder", "acknowledged"]
