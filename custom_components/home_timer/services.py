"""Actions of Home Timer.

Targets: whatever the action editor's target selector picks (area, floor, label,
device, entity) – it resolves to the Home Timer areas it reaches – or a timer by
``timer_id``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from .const import (
    ATTR_DURATION,
    ATTR_NAME,
    ATTR_NOTE,
    ATTR_SOURCE,
    ATTR_TIMER_ID,
    BY_ACTION,
    CONF_AREA_ID,
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

_DURATION = vol.All(cv.time_period, vol.Range(min=timedelta(seconds=1)))

START_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_NAME): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_DURATION): _DURATION,
        vol.Optional(ATTR_NOTE): cv.string,
        vol.Optional(ATTR_SOURCE): dict,
    }
)
TIMER_SCHEMA = vol.Schema({vol.Required(ATTR_TIMER_ID): cv.string})
TIME_SCHEMA = vol.Schema({vol.Required(ATTR_TIMER_ID): cv.string, vol.Required(ATTR_DURATION): _DURATION})
ACKNOWLEDGE_SCHEMA = vol.Schema({**cv.TARGET_SERVICE_FIELDS, vol.Optional(ATTR_TIMER_ID): cv.string})


def _engines(hass: HomeAssistant) -> dict[str, AreaTimers]:
    """The loaded areas by config entry id."""
    return {e.entry_id: e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)}


def _error(key: str, **placeholders: Any) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def _targeted(hass: HomeAssistant, call: ServiceCall) -> list[AreaTimers]:
    """The areas a call's target refers to (empty without a target).

    Any target works – area, floor, label, device, entity: it refers to an area when
    it reaches that area's Home Timer device or entities, or names the area itself.
    """
    selection = TargetSelection(call.data)
    if not selection.has_any_target:
        return []
    selected = async_extract_referenced_entity_ids(hass, selection, primary_entities_only=False)
    engines = _engines(hass)
    found: dict[str, AreaTimers] = {}
    devices = dr.async_get(hass)
    for device_id in selected.referenced_devices:
        device = devices.async_get(device_id)
        if device is not None and device.config_entry_id in engines:
            found[device.config_entry_id] = engines[device.config_entry_id]
    entities = er.async_get(hass)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entity = entities.async_get(entity_id)
        if entity is not None and entity.config_entry_id in engines:
            found[entity.config_entry_id] = engines[entity.config_entry_id]
    for entry_id, engine in engines.items():
        if engine.entry.data[CONF_AREA_ID] in selected.referenced_areas:
            found[entry_id] = engine
    if not found:
        raise _error("no_area_in_target")
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
