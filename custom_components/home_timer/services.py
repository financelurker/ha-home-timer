"""Actions of Home Timer.

Targets: an area (``area_id``) or the area's device (``device_id``), as picked in the
action editor's target selector, or a timer by ``timer_id``.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_DURATION,
    ATTR_NAME,
    ATTR_NOTE,
    ATTR_SOURCE,
    ATTR_TIMER_ID,
    BY_ACTION,
    DOMAIN,
    SERVICE_ACKNOWLEDGE,
    SERVICE_ADD_TIME,
    SERVICE_CANCEL,
    SERVICE_PAUSE,
    SERVICE_RESUME,
    SERVICE_START,
    SERVICE_SUBTRACT_TIME,
)
from .engine import AreaTimers, TimerError

ATTR_AREA_ID = "area_id"
ATTR_DEVICE_ID = "device_id"

_TARGET = {
    vol.Optional(ATTR_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
}
_DURATION = vol.All(cv.time_period, cv.positive_timedelta)

START_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_DURATION): _DURATION,
        vol.Optional(ATTR_NOTE): cv.string,
        vol.Optional(ATTR_SOURCE): dict,
    }
)
TIMER_SCHEMA = vol.Schema({vol.Required(ATTR_TIMER_ID): cv.string})
TIME_SCHEMA = vol.Schema({vol.Required(ATTR_TIMER_ID): cv.string, vol.Required(ATTR_DURATION): _DURATION})
ACKNOWLEDGE_SCHEMA = vol.Schema({**_TARGET, vol.Optional(ATTR_TIMER_ID): cv.string})


def _engines(hass: HomeAssistant) -> dict[str, AreaTimers]:
    return hass.data.get(DOMAIN, {})


def _error(key: str, **placeholders: Any) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def _targeted(hass: HomeAssistant, call: ServiceCall) -> list[AreaTimers]:
    """The areas named by the call's target (area or Home Timer device)."""
    engines = _engines(hass)
    found: dict[str, AreaTimers] = {}
    for area_id in call.data.get(ATTR_AREA_ID, []):
        matches = [e for e in engines.values() if e.area_id == area_id]
        if not matches:
            raise _error("area_not_set_up", area=area_id)
        found.update({e.entry.entry_id: e for e in matches})
    devices = dr.async_get(hass)
    for device_id in call.data.get(ATTR_DEVICE_ID, []):
        device = devices.async_get(device_id)
        engine = next((engines[i] for i in (device.config_entries if device else ()) if i in engines), None)
        if engine is None:
            raise _error("not_a_home_timer_device", device=device_id)
        found[engine.entry.entry_id] = engine
    return list(found.values())


def _find(hass: HomeAssistant, timer_id: str) -> AreaTimers:
    for engine in _engines(hass).values():
        if timer_id in engine.timers:
            return engine
    raise _error("unknown_timer", timer_id=timer_id)


def _run(func: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return func(*args, **kwargs)
    except TimerError as err:
        raise _error(err.key, **err.placeholders) from err


@callback
def async_register_services(hass: HomeAssistant) -> None:
    async def start(call: ServiceCall) -> ServiceResponse:
        areas = _targeted(hass, call)
        if len(areas) != 1:
            raise _error("one_area")
        engine = areas[0]
        timer = _run(
            engine.start,
            call.data[ATTR_NAME],
            call.data[ATTR_DURATION].total_seconds(),
            note=call.data.get(ATTR_NOTE),
            source=call.data.get(ATTR_SOURCE),
        )
        return {"timer": timer.as_dict(timer.started_at)}

    async def pause(call: ServiceCall) -> None:
        timer_id = call.data[ATTR_TIMER_ID]
        _run(_find(hass, timer_id).pause, timer_id)

    async def resume(call: ServiceCall) -> None:
        timer_id = call.data[ATTR_TIMER_ID]
        _run(_find(hass, timer_id).resume, timer_id)

    async def add_time(call: ServiceCall) -> None:
        timer_id = call.data[ATTR_TIMER_ID]
        _run(_find(hass, timer_id).add_time, timer_id, call.data[ATTR_DURATION].total_seconds())

    async def subtract_time(call: ServiceCall) -> None:
        timer_id = call.data[ATTR_TIMER_ID]
        _run(_find(hass, timer_id).add_time, timer_id, -call.data[ATTR_DURATION].total_seconds())

    async def cancel(call: ServiceCall) -> None:
        timer_id = call.data[ATTR_TIMER_ID]
        _run(_find(hass, timer_id).cancel, timer_id)

    async def acknowledge(call: ServiceCall) -> None:
        if timer_id := call.data.get(ATTR_TIMER_ID):
            _run(_find(hass, timer_id).acknowledge, timer_id, by=BY_ACTION)
            return
        areas = _targeted(hass, call)
        if not areas:
            raise _error("timer_or_area")
        for engine in areas:
            engine.acknowledge_all(by=BY_ACTION)

    hass.services.async_register(DOMAIN, SERVICE_START, start, START_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE, pause, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, resume, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ADD_TIME, add_time, TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SUBTRACT_TIME, subtract_time, TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, cancel, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ACKNOWLEDGE, acknowledge, ACKNOWLEDGE_SCHEMA)
