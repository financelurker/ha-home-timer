"""Stations: devices and entities, presets, one timer per station, station targets, removal."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.home_timer.const import DOMAIN

from .conftest import add_station, tick
from .test_engine import events

HOB = "sensor.kitchen_hob_1"
HOB_END = "sensor.kitchen_hob_1_timer_end"
RICE = "button.kitchen_hob_1_start_rice"
PASTA = "button.kitchen_hob_1_start_pasta"
PAUSE = "button.kitchen_hob_1_pause_resume"
PLUS = "button.kitchen_hob_1_1_minute"
STOP = "button.kitchen_hob_1_stop_acknowledge"


@pytest.fixture
async def hob(hass: HomeAssistant, kitchen) -> str:
    return await add_station(hass, kitchen, "Hob 1", [("Rice", 600, "Level 6, then 3"), ("Pasta", 540)])


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def test_device_and_entities(hass: HomeAssistant, kitchen, hob) -> None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id)
    assert device.name == "Hob 1"
    assert device.area_id == kitchen.data["area_id"]
    assert (device.config_entry_id, device.config_subentry_id) == (kitchen.entry_id, hob)
    state = hass.states.get(HOB)
    assert state.state == "idle"
    assert state.attributes["presets"] == ["Rice", "Pasta"]
    for entity_id in (HOB_END, RICE, PASTA, PAUSE, PLUS, STOP):
        assert hass.states.get(entity_id) is not None, entity_id
    assert er.async_get(hass).async_get(RICE).config_subentry_id == hob


async def test_preset_button_and_station_buttons(hass: HomeAssistant, kitchen, hob, freezer) -> None:
    ev = events(hass)
    await press(hass, RICE)
    state = hass.states.get(HOB)
    assert state.state == "active"
    assert state.attributes["timer_name"] == "Rice"
    assert state.attributes["preset"] == "Rice"
    assert state.attributes["note"] == "Level 6, then 3"
    assert hass.states.get(HOB_END).state not in ("unknown", "unavailable")
    started = ev["started"][0].data
    assert started["station"] == hob
    assert started["station_name"] == "Hob 1"
    assert started["device_id"] == dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id).id

    await press(hass, PAUSE)
    assert hass.states.get(HOB).state == "paused"
    assert hass.states.get(HOB_END).state == "unknown"
    await press(hass, PAUSE)
    assert hass.states.get(HOB).state == "active"
    await press(hass, PLUS)
    assert hass.states.get(HOB).attributes["duration"] == 660

    await press(hass, PASTA)  # replaces Rice
    assert ev["cancelled"][0].data["reason"] == "replaced"
    assert hass.states.get(HOB).attributes["timer_name"] == "Pasta"

    await tick(hass, freezer, 541)
    assert hass.states.get(HOB).state == "ringing"
    await press(hass, PLUS)  # ringing: starts again with a minute
    assert hass.states.get(HOB).state == "active"
    await tick(hass, freezer, 61)
    await press(hass, STOP)
    assert ev["acknowledged"][0].data["by"] == "button"
    assert hass.states.get(HOB).state == "idle"

    await press(hass, RICE)
    await press(hass, STOP)  # running: cancels
    assert ev["cancelled"][-1].data["reason"] == "manual"
    await press(hass, STOP)  # idle: nothing happens
    await press(hass, PAUSE)


async def test_actions_on_a_station(hass: HomeAssistant, kitchen, hob, freezer) -> None:
    device_id = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id).id
    response = await hass.services.async_call(
        DOMAIN, "start", {"device_id": device_id, "preset": "rice"}, blocking=True, return_response=True
    )
    assert response["timer"]["station"] == hob
    assert response["timer"]["duration"] == 600
    await hass.services.async_call(DOMAIN, "pause", {"entity_id": HOB}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(HOB).state == "paused"
    await hass.services.async_call(DOMAIN, "resume", {"device_id": device_id}, blocking=True)
    await hass.services.async_call(DOMAIN, "subtract_time", {"device_id": device_id, "duration": "00:30:00"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(HOB).state == "ringing"
    await hass.services.async_call(DOMAIN, "acknowledge", {"entity_id": HOB}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(HOB).state == "idle"

    response = await hass.services.async_call(
        DOMAIN, "start", {"entity_id": HOB, "name": "Eggs", "duration": "00:06:00"}, blocking=True, return_response=True
    )
    assert response["timer"]["station"] == hob
    # an area target starts a timer without a station
    response = await hass.services.async_call(
        DOMAIN, "start", {"area_id": kitchen.data["area_id"], "name": "Dough", "duration": "00:30:00"},
        blocking=True, return_response=True,
    )
    assert response["timer"]["station"] is None
    assert hass.states.get(HOB).attributes["timer_name"] == "Eggs"


async def test_station_action_errors(hass: HomeAssistant, kitchen, hob) -> None:
    other = await add_station(hass, kitchen, "Rice cooker", [("Rice", 900)])
    devices = dr.async_get(hass)
    hob_device = devices.async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id).id
    cooker_device = devices.async_get_device_by_identifier((DOMAIN, other), kitchen.entry_id).id
    with pytest.raises(ServiceValidationError):  # two stations
        await hass.services.async_call(DOMAIN, "start", {"device_id": [hob_device, cooker_device], "preset": "Rice"}, blocking=True)
    with pytest.raises(ServiceValidationError):  # preset without station
        await hass.services.async_call(DOMAIN, "start", {"area_id": kitchen.data["area_id"], "preset": "Rice"}, blocking=True)
    with pytest.raises(ServiceValidationError):  # unknown preset
        await hass.services.async_call(DOMAIN, "start", {"device_id": hob_device, "preset": "Soup"}, blocking=True)
    with pytest.raises(ServiceValidationError):  # neither preset nor name/duration
        await hass.services.async_call(DOMAIN, "start", {"device_id": hob_device, "name": "Eggs"}, blocking=True)
    with pytest.raises(ServiceValidationError):  # station without a timer
        await hass.services.async_call(DOMAIN, "pause", {"device_id": hob_device}, blocking=True)
    with pytest.raises(ServiceValidationError):  # neither timer nor station
        await hass.services.async_call(DOMAIN, "cancel", {}, blocking=True)


async def test_removing_a_station(hass: HomeAssistant, kitchen, hob) -> None:
    ev = events(hass)
    await press(hass, RICE)
    hass.config_entries.async_remove_subentry(kitchen, hob)
    await hass.async_block_till_done()
    assert ev["cancelled"][-1].data["reason"] == "station_removed"
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id) is None
    assert hass.states.get(HOB) is None
    assert kitchen.runtime_data.timers == {}


async def test_deleting_a_preset_removes_its_button(hass: HomeAssistant, kitchen, hob) -> None:
    subentry = kitchen.subentries[hob]
    data = dict(subentry.data)
    data["presets"] = [p for p in data["presets"] if p["name"] != "Pasta"]
    hass.config_entries.async_update_subentry(kitchen, subentry, data=data)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(PASTA) is None
    assert hass.states.get(RICE) is not None


async def test_renaming_a_preset_keeps_its_button(hass: HomeAssistant, kitchen, hob) -> None:
    subentry = kitchen.subentries[hob]
    data = dict(subentry.data)
    data["presets"] = [dict(p, name="Basmati") if p["name"] == "Rice" else p for p in data["presets"]]
    hass.config_entries.async_update_subentry(kitchen, subentry, data=data)
    await hass.async_block_till_done()
    entry = er.async_get(hass).async_get(RICE)
    assert entry is not None
    assert hass.states.get(RICE).attributes["friendly_name"].endswith("Start Basmati")


async def test_station_timer_survives_a_reload(hass: HomeAssistant, kitchen, hob, freezer) -> None:
    await press(hass, RICE)
    assert await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(HOB).state == "active"


async def test_button_error_is_translated(hass: HomeAssistant, kitchen, hob) -> None:
    from custom_components.home_timer.button import _run
    from custom_components.home_timer.engine import TimerError

    def boom():
        raise TimerError("unknown_station", station="x")

    with pytest.raises(HomeAssistantError):
        _run(boom)


async def test_mixed_targets(hass: HomeAssistant, kitchen, hob, freezer) -> None:
    from .conftest import setup_area

    bathroom = await setup_area(hass, "Bathroom")
    ev = events(hass)
    device_id = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id).id
    await press(hass, RICE)
    bathroom.runtime_data.start("Bath", 60)
    await tick(hass, freezer, 601)
    await hass.services.async_call(
        DOMAIN, "acknowledge", {"device_id": device_id, "area_id": bathroom.data["area_id"]}, blocking=True
    )
    await hass.async_block_till_done()
    assert len(ev["acknowledged"]) == 2
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start", {"device_id": device_id, "area_id": bathroom.data["area_id"], "preset": "Rice"}, blocking=True
        )
    await press(hass, RICE)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "pause", {"device_id": device_id, "area_id": bathroom.data["area_id"]}, blocking=True
        )


async def test_preset_note_override_and_no_duration(hass: HomeAssistant, kitchen, hob) -> None:
    response = await hass.services.async_call(
        DOMAIN, "start", {"entity_id": HOB, "preset": "Rice", "note": "Lid on"}, blocking=True, return_response=True
    )
    assert response["timer"]["note"] == "Lid on"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "start", {"entity_id": HOB, "preset": "Rice", "duration": "00:01:00"}, blocking=True
        )


async def test_station_removed_event_keeps_the_name(hass: HomeAssistant, kitchen, hob) -> None:
    ev = events(hass)
    await press(hass, RICE)
    hass.config_entries.async_remove_subentry(kitchen, hob)
    await hass.async_block_till_done()
    cancelled = ev["cancelled"][-1].data
    assert cancelled["reason"] == "station_removed"
    assert cancelled["station_name"] == "Hob 1"


async def test_upgrade_keeps_area_entities(hass: HomeAssistant, kitchen) -> None:
    """An area of the previous release: no stations, the same three entities."""
    for entity_id in (
        "sensor.kitchen_home_timer_timers",
        "binary_sensor.kitchen_home_timer_ringing",
        "button.kitchen_home_timer_acknowledge_all",
    ):
        assert hass.states.get(entity_id) is not None
    assert kitchen.subentries == {}


async def test_ringing_sensors_are_red_while_ringing(hass: HomeAssistant, kitchen, freezer) -> None:
    """Area and station: device class problem (red while on), states Ringing / Quiet."""
    await add_station(hass, kitchen, "Hob 1", [("Rice", 60)])
    station = hass.states.get("binary_sensor.kitchen_hob_1_ringing")
    area = hass.states.get("binary_sensor.kitchen_home_timer_ringing")
    assert station.state == area.state == "off"
    assert station.attributes["device_class"] == area.attributes["device_class"] == "problem"
    await hass.services.async_call("button", "press", {"entity_id": RICE}, blocking=True)
    assert hass.states.get("binary_sensor.kitchen_hob_1_ringing").state == "off"
    await tick(hass, freezer, 61)
    assert hass.states.get("binary_sensor.kitchen_hob_1_ringing").state == "on"
    assert hass.states.get("binary_sensor.kitchen_home_timer_ringing").state == "on"
    await hass.services.async_call(DOMAIN, "acknowledge", {"entity_id": "sensor.kitchen_hob_1"}, blocking=True)
    assert hass.states.get("binary_sensor.kitchen_hob_1_ringing").state == "off"

