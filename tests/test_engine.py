"""Timers of an area: start, run out, ring, remind, acknowledge; pause, add time, cancel."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.home_timer.const import (
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_RING_UNTIL_ACK,
    DOMAIN,
)
from custom_components.home_timer.engine import TimerError

from .conftest import setup_area, tick

SENSOR = "sensor.kitchen_home_timer_timers"
RINGING = "binary_sensor.kitchen_home_timer_ringing"
ACK_ALL = "button.kitchen_home_timer_acknowledge_all"


def events(hass: HomeAssistant) -> dict[str, list]:
    names = ("started", "paused", "resumed", "time_changed", "finished", "reminder", "cancelled", "acknowledged")
    return {n: async_capture_events(hass, f"{DOMAIN}_{n}") for n in names}


async def start(hass: HomeAssistant, area_id: str, name: str = "Rice", duration: str = "00:10:00", **data) -> str:
    response = await hass.services.async_call(
        DOMAIN, "start", {"area_id": area_id, "name": name, "duration": duration, **data},
        blocking=True, return_response=True,
    )
    return response["timer"]["id"]


async def test_runs_out_rings_reminds_and_is_acknowledged(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"], note="Level 3", source={"script": {"step": 1}})
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "1"
    assert hass.states.get(RINGING).state == "off"
    started = ev["started"][0].data
    assert started["timer_id"] == timer_id
    assert started["name"] == "Rice"
    assert started["area_id"] == kitchen.data["area_id"]
    assert started["duration"] == 600
    assert started["note"] == "Level 3"
    assert started["source"] == {"script": {"step": 1}}
    assert started["station"] is None
    assert started["device_id"]

    await tick(hass, freezer, 599)
    assert not ev["finished"]
    await tick(hass, freezer, 2)
    assert ev["finished"][0].data["late"] is False
    assert hass.states.get(RINGING).state == "on"
    timers = hass.states.get(SENSOR).attributes["timers"]
    assert timers[0]["status"] == "ringing"

    await tick(hass, freezer, 120)
    assert len(ev["reminder"]) == 1
    assert ev["reminder"][0].data["overtime"] >= 120
    await tick(hass, freezer, 120)
    assert len(ev["reminder"]) == 2

    await hass.services.async_call(DOMAIN, "acknowledge", {"timer_id": timer_id}, blocking=True)
    await hass.async_block_till_done()
    assert ev["acknowledged"][0].data["by"] == "action"
    assert hass.states.get(SENSOR).state == "0"
    assert hass.states.get(RINGING).state == "off"
    await tick(hass, freezer, 600)
    assert len(ev["reminder"]) == 2


async def test_auto_dismiss(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_AUTO_DISMISS_MINUTES: 5, CONF_REMINDER_MINUTES: 0})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 61)
    await tick(hass, freezer, 5 * 60)
    assert ev["acknowledged"][0].data["by"] == "timeout"
    assert not ev["reminder"]
    assert hass.states.get(SENSOR).state == "0"


async def test_without_ringing_the_timer_is_gone_after_it_ran_out(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_RING_UNTIL_ACK: False})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 61)
    assert len(ev["finished"]) == 1
    assert hass.states.get(SENSOR).state == "0"
    assert hass.states.get(RINGING).state == "off"


async def test_pause_resume_add_subtract(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"])
    await tick(hass, freezer, 60)
    await hass.services.async_call(DOMAIN, "pause", {"timer_id": timer_id}, blocking=True)
    await tick(hass, freezer, 3600)
    assert not ev["finished"]
    timer = hass.states.get(SENSOR).attributes["timers"][0]
    assert timer["status"] == "paused"
    assert timer["remaining"] == 540

    await hass.services.async_call(DOMAIN, "resume", {"timer_id": timer_id}, blocking=True)
    await hass.services.async_call(DOMAIN, "add_time", {"timer_id": timer_id, "duration": "00:02:00"}, blocking=True)
    await hass.async_block_till_done()
    assert ev["time_changed"][0].data["change"] == 120
    assert ev["time_changed"][0].data["duration"] == 720
    await tick(hass, freezer, 659)
    assert not ev["finished"]
    await tick(hass, freezer, 2)
    assert len(ev["finished"]) == 1


async def test_subtract_to_zero_runs_out(hass: HomeAssistant, kitchen) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"])
    await hass.services.async_call(DOMAIN, "subtract_time", {"timer_id": timer_id, "duration": "00:20:00"}, blocking=True)
    await hass.async_block_till_done()
    assert len(ev["finished"]) == 1
    assert hass.states.get(RINGING).state == "on"


async def test_add_time_to_a_ringing_timer_starts_it_again(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 61)
    await hass.services.async_call(DOMAIN, "add_time", {"timer_id": timer_id, "duration": "00:02:00"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(RINGING).state == "off"
    assert hass.states.get(SENSOR).attributes["timers"][0]["status"] == "active"
    await tick(hass, freezer, 121)
    assert len(ev["finished"]) == 2
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "subtract_time", {"timer_id": timer_id, "duration": "00:01:00"}, blocking=True
        )


async def test_cancel(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"])
    await hass.services.async_call(DOMAIN, "cancel", {"timer_id": timer_id}, blocking=True)
    await hass.async_block_till_done()
    assert ev["cancelled"][0].data["reason"] == "manual"
    await tick(hass, freezer, 700)
    assert not ev["finished"]
    assert hass.states.get(SENSOR).state == "0"


async def test_station_has_one_timer(hass: HomeAssistant, kitchen) -> None:
    ev = events(hass)
    engine = kitchen.runtime_data
    first = engine.start("Pasta", 540, station="hob_1")
    engine.start("Free", 60)
    second = engine.start("Rice", 600, station="hob_1")
    await hass.async_block_till_done()
    assert first.id not in engine.timers
    assert second.id in engine.timers
    assert len(engine.timers) == 2
    assert ev["cancelled"][0].data["reason"] == "replaced"
    assert ev["cancelled"][0].data["timer_id"] == first.id


async def test_several_timers_without_station(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    for name in ("Dough", "Butter", "Eggs"):
        await start(hass, kitchen.data["area_id"], name=name, duration="00:05:00")
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).state == "3"
    await tick(hass, freezer, 301)
    assert len(ev["finished"]) == 3
    await hass.services.async_call(DOMAIN, "acknowledge", {"area_id": kitchen.data["area_id"]}, blocking=True)
    await hass.async_block_till_done()
    assert len(ev["acknowledged"]) == 3


async def test_acknowledge_all_button(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    await start(hass, kitchen.data["area_id"], duration="00:01:00")
    await hass.services.async_call("button", "press", {"entity_id": ACK_ALL}, blocking=True)  # nothing rings yet
    await tick(hass, freezer, 61)
    await hass.services.async_call("button", "press", {"entity_id": ACK_ALL}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["by"] for e in ev["acknowledged"]] == ["button"]


async def test_errors(hass: HomeAssistant, kitchen) -> None:
    area_id = kitchen.data["area_id"]
    bathroom = await setup_area(hass, "Bathroom")
    timer_id = await start(hass, area_id)
    with pytest.raises(ServiceValidationError, match="unknown_timer|no timer"):
        await hass.services.async_call(DOMAIN, "pause", {"timer_id": "nope"}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "resume", {"timer_id": timer_id}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "acknowledge", {"timer_id": timer_id}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "acknowledge", {}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await start(hass, [area_id, bathroom.data["area_id"]])
    with pytest.raises(TimerError):
        kitchen.runtime_data.start("Zero", 0)


async def test_start_on_the_area_device(hass: HomeAssistant, kitchen) -> None:
    device_id = kitchen.runtime_data.hub_device_id
    response = await hass.services.async_call(
        DOMAIN, "start", {"device_id": device_id, "name": "Tea", "duration": {"minutes": 4}},
        blocking=True, return_response=True,
    )
    assert response["timer"]["name"] == "Tea"
    assert response["timer"]["duration"] == 240
    assert response["timer"]["status"] == "active"


async def test_subtract_more_than_left_runs_out_now(hass: HomeAssistant, kitchen, freezer) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"])
    await tick(hass, freezer, 60)
    await hass.services.async_call(DOMAIN, "subtract_time", {"timer_id": timer_id, "duration": "01:00:00"}, blocking=True)
    await hass.async_block_till_done()
    assert ev["time_changed"][0].data["change"] == -540
    assert ev["time_changed"][0].data["duration"] == 60
    assert len(ev["finished"]) == 1
    assert not ev["acknowledged"]
    assert hass.states.get(SENSOR).attributes["timers"][0]["overtime"] == 0


async def test_subtract_on_a_paused_timer(hass: HomeAssistant, kitchen) -> None:
    ev = events(hass)
    timer_id = await start(hass, kitchen.data["area_id"])
    await hass.services.async_call(DOMAIN, "pause", {"timer_id": timer_id}, blocking=True)
    await hass.services.async_call(DOMAIN, "subtract_time", {"timer_id": timer_id, "duration": "00:04:00"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SENSOR).attributes["timers"][0]["remaining"] == 360
    await hass.services.async_call(DOMAIN, "subtract_time", {"timer_id": timer_id, "duration": "00:10:00"}, blocking=True)
    await hass.async_block_till_done()
    assert len(ev["finished"]) == 1


async def test_zero_duration_is_rejected(hass: HomeAssistant, kitchen) -> None:
    import voluptuous as vol

    timer_id = await start(hass, kitchen.data["area_id"])
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "add_time", {"timer_id": timer_id, "duration": 0}, blocking=True)
    with pytest.raises(vol.Invalid):
        await start(hass, kitchen.data["area_id"], duration="00:00:00")


async def test_no_reminder_when_it_is_dismissed(hass: HomeAssistant, freezer) -> None:
    entry = await setup_area(hass, "Kitchen", {CONF_REMINDER_MINUTES: 2, CONF_AUTO_DISMISS_MINUTES: 4})
    ev = events(hass)
    await start(hass, entry.data["area_id"], duration="00:01:00")
    await tick(hass, freezer, 60)
    for _ in range(5):
        await tick(hass, freezer, 60)
    assert [e.data["overtime"] for e in ev["reminder"]] == [120]
    assert ev["acknowledged"][0].data["by"] == "timeout"


async def test_source_must_be_storable(hass: HomeAssistant, kitchen) -> None:
    with pytest.raises(TimerError):
        kitchen.runtime_data.start("Odd", 60, source={"when": object()})
    source = {"a": [1]}
    timer = kitchen.runtime_data.start("Copy", 60, source=source)
    source["a"].append(2)
    assert timer.source == {"a": [1]}
