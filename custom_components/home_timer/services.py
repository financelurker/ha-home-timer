"""Actions of Home Timer.

Targets: whatever the action editor's target selector picks (area, floor, label,
device, entity), or a timer by ``timer_id``.

- A **station** is targeted by its device or one of its entities, named directly.
- An **area** is targeted by anything that reaches its Home Timer devices or
  entities (area, floor, label, the hub device …) or names the area itself.

``start`` on a station replaces its timer (or starts a preset); on an area it starts
a timer without a station. The other actions take a timer id or stations.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID
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
    ATTR_PRESET,
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
    STATUS_RINGING,
)
from .engine import AreaTimers, Timer, TimerError

_DURATION = vol.All(cv.time_period, vol.Range(min=timedelta(seconds=1)))

START_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Exclusive(ATTR_PRESET, "what"): vol.All(cv.string, vol.Length(min=1)),
        vol.Exclusive(ATTR_NAME, "what"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(ATTR_DURATION): _DURATION,
        vol.Optional(ATTR_NOTE): cv.string,
        vol.Optional(ATTR_SOURCE): dict,
    }
)
TIMER_SCHEMA = vol.Schema({**cv.TARGET_SERVICE_FIELDS, vol.Optional(ATTR_TIMER_ID): cv.string})
TIME_SCHEMA = vol.Schema(
    {**cv.TARGET_SERVICE_FIELDS, vol.Optional(ATTR_TIMER_ID): cv.string, vol.Required(ATTR_DURATION): _DURATION}
)
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


def _targeted(hass: HomeAssistant, data: Mapping[str, Any]) -> list[AreaTimers]:
    """The areas a target refers to (empty without a target).

    Any target works – area, floor, label, device, entity: it refers to an area when
    it reaches that area's Home Timer devices or entities, or names the area itself.
    """
    selection = TargetSelection(data)
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


def _split(hass: HomeAssistant, call: ServiceCall) -> tuple[list[tuple[AreaTimers, str]], dict[str, Any]]:
    """Stations named directly by the target (their devices or entities), and the rest of the target."""
    selection = TargetSelection(call.data)
    engines = _engines(hass)
    found: dict[str, tuple[AreaTimers, str]] = {}
    used_devices: set[str] = set()
    used_entities: set[str] = set()
    devices = dr.async_get(hass)
    for device_id in selection.device_ids:
        device = devices.async_get(device_id)
        if device is None or (engine := engines.get(device.config_entry_id)) is None:
            continue
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier in engine.stations:
                found[identifier] = (engine, identifier)
                used_devices.add(device_id)
    entities = er.async_get(hass)
    for entity_id in selection.entity_ids:
        entity = entities.async_get(entity_id)
        if entity is None or (engine := engines.get(entity.config_entry_id)) is None:
            continue
        if entity.config_subentry_id in engine.stations:
            found[entity.config_subentry_id] = (engine, entity.config_subentry_id)
            used_entities.add(entity_id)
    rest: dict[str, Any] = {
        ATTR_AREA_ID: list(selection.area_ids),
        ATTR_FLOOR_ID: list(selection.floor_ids),
        ATTR_LABEL_ID: list(selection.label_ids),
        ATTR_DEVICE_ID: list(selection.device_ids - used_devices),
        ATTR_ENTITY_ID: list(selection.entity_ids - used_entities),
    }
    return list(found.values()), {k: v for k, v in rest.items() if v}


def _timers(hass: HomeAssistant, call: ServiceCall) -> list[tuple[AreaTimers, Timer]]:
    """The timers an action works on: by timer id, or the timers of the targeted stations."""
    if timer_id := call.data.get(ATTR_TIMER_ID):
        engine = _find(hass, timer_id)
        return [(engine, engine.timers[timer_id])]
    stations, rest = _split(hass, call)
    if not stations:
        raise _error("timer_or_station")
    if rest:
        raise _error("mixed_target")
    result = []
    for engine, station_id in stations:
        timer = engine.timer_at(station_id)
        if timer is None:
            raise _error("station_idle", station=engine.stations[station_id].name)
        result.append((engine, timer))
    return result


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
        stations, rest = _split(hass, call)
        preset = call.data.get(ATTR_PRESET)
        if preset is not None and ATTR_DURATION in call.data:
            raise _error("preset_with_duration")
        if stations:
            if rest:
                raise _error("mixed_target")
            if len(stations) != 1:
                raise _error("one_station")
            engine, station_id = stations[0]
        else:
            if preset is not None:
                raise _error("preset_needs_station")
            areas = _targeted(hass, rest)
            if len(areas) != 1:
                raise _error("one_area")
            engine, station_id = areas[0], None
        if preset is not None:
            timer = _run(
                engine.start_preset,
                station_id,
                preset,
                note=call.data.get(ATTR_NOTE),
                source=call.data.get(ATTR_SOURCE),
            )
        else:
            if ATTR_NAME not in call.data or ATTR_DURATION not in call.data:
                raise _error("name_and_duration")
            timer = _run(
                engine.start,
                call.data[ATTR_NAME],
                call.data[ATTR_DURATION].total_seconds(),
                station=station_id,
                note=call.data.get(ATTR_NOTE),
                source=call.data.get(ATTR_SOURCE),
            )
        return {"timer": timer.as_dict(timer.started_at)}

    async def pause(call: ServiceCall) -> None:
        for engine, timer in _timers(hass, call):
            _run(engine.pause, timer.id)

    async def resume(call: ServiceCall) -> None:
        for engine, timer in _timers(hass, call):
            _run(engine.resume, timer.id)

    async def add_time(call: ServiceCall) -> None:
        for engine, timer in _timers(hass, call):
            _run(engine.add_time, timer.id, call.data[ATTR_DURATION].total_seconds())

    async def subtract_time(call: ServiceCall) -> None:
        for engine, timer in _timers(hass, call):
            _run(engine.add_time, timer.id, -call.data[ATTR_DURATION].total_seconds())

    async def cancel(call: ServiceCall) -> None:
        for engine, timer in _timers(hass, call):
            _run(engine.cancel, timer.id)

    async def acknowledge(call: ServiceCall) -> None:
        if call.data.get(ATTR_TIMER_ID):
            for engine, timer in _timers(hass, call):
                _run(engine.acknowledge, timer.id, by=BY_ACTION)
            return
        stations, rest = _split(hass, call)
        areas = _targeted(hass, rest)
        if not stations and not areas:
            raise _error("timer_or_area")
        for engine, station_id in stations:
            timer = engine.timer_at(station_id)
            if timer is not None and timer.status == STATUS_RINGING:
                engine.acknowledge(timer.id, by=BY_ACTION)
        for engine in areas:
            engine.acknowledge_all(by=BY_ACTION)

    hass.services.async_register(DOMAIN, SERVICE_START, start, START_SCHEMA, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE, pause, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, resume, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ADD_TIME, add_time, TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SUBTRACT_TIME, subtract_time, TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, cancel, TIMER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ACKNOWLEDGE, acknowledge, ACKNOWLEDGE_SCHEMA)
