"""What the panel shows: the areas with their stations, the free areas, the field specs.

Live state is not sent: the panel reads the entities (``entities``) from Home
Assistant's states, which it gets anyway.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import CONF_AREA_ID, DEFAULT_OPTIONS, DOMAIN
from ..power import MAX_STEPS, RULE_DEFAULTS, RULE_FIELDS, RULE_TYPES
from ..stations import CONF_ICON, CONF_MODE, CONF_POWER, CONF_PRESETS, KINDS, MODE_MANUAL, POWER_DEFAULTS, SUBENTRY_STATION
from .edit import MAX_PRESET_SECONDS, OPTION_RANGES, POWER_RANGES


def version(data: Any) -> str:
    """A short fingerprint of stored data: a save based on an older one is refused."""
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _entity(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def _device(hass: HomeAssistant, identifier: str, entry_id: str) -> str | None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, identifier), entry_id)
    return device.id if device else None


def area_options(entry: ConfigEntry) -> dict[str, Any]:
    return {key: entry.options.get(key, default) for key, default in DEFAULT_OPTIONS.items()}


def area_view(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    stations = []
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_STATION:
            continue
        data = dict(sub.data)
        stations.append(
            {
                "id": sub.subentry_id,
                "name": sub.title,
                "icon": data.get(CONF_ICON),
                "mode": data.get(CONF_MODE, MODE_MANUAL),
                "presets": list(data.get(CONF_PRESETS) or []),
                "power": {**POWER_DEFAULTS, **(data.get(CONF_POWER) or {})},
                "version": version({"title": sub.title, "data": data}),
                "device_id": _device(hass, sub.subentry_id, entry.entry_id),
                "entities": {
                    "timer": _entity(hass, "sensor", f"{sub.subentry_id}_station"),
                    "running": _entity(hass, "binary_sensor", f"{sub.subentry_id}_appliance_running"),
                },
            }
        )
    area = ar.async_get(hass).async_get_area(entry.data[CONF_AREA_ID])
    quick = list(entry.options.get(CONF_PRESETS) or [])
    options = area_options(entry)
    return {
        "entry_id": entry.entry_id,
        "area_id": entry.data[CONF_AREA_ID],
        "name": entry.title,
        "icon": area.icon if area else None,
        "loaded": entry.state is ConfigEntryState.LOADED,
        "disabled": entry.disabled_by is not None,
        "options": options,
        "quick_timers": quick,
        "version": version({"options": options, "quick": quick}),
        "device_id": _device(hass, entry.entry_id, entry.entry_id),
        "entities": {
            "timers": _entity(hass, "sensor", f"{entry.entry_id}_timers"),
            "ringing": _entity(hass, "binary_sensor", f"{entry.entry_id}_ringing"),
        },
        "stations": stations,
    }


def config_view(hass: HomeAssistant, settings: dict[str, Any]) -> dict[str, Any]:
    entries = hass.config_entries.async_entries(DOMAIN)
    areas = sorted((area_view(hass, e) for e in entries), key=lambda a: a["name"].casefold())
    taken = {e.data.get(CONF_AREA_ID) for e in entries}
    free = sorted(
        ({"area_id": a.id, "name": a.name, "icon": a.icon} for a in ar.async_get(hass).async_list_areas() if a.id not in taken),
        key=lambda a: a["name"].casefold(),
    )
    return {"areas": areas, "free_areas": free, "settings": settings, "specs": specs()}


def specs() -> dict[str, Any]:
    """Ranges and choices of the panel's fields (the same the checks use)."""
    return {
        "options": {k: {"min": lo, "max": hi} for k, (lo, hi) in OPTION_RANGES.items()},
        "power": {k: {"min": lo, "max": hi} for k, (lo, hi) in POWER_RANGES.items()},
        "power_defaults": POWER_DEFAULTS,
        "kinds": list(KINDS),
        "rules": {
            t: {
                "defaults": RULE_DEFAULTS[t],
                "fields": [{"key": k, "min": lo, "max": hi, "step": st, "unit": u} for k, lo, hi, st, u in RULE_FIELDS[t]],
            }
            for t in RULE_TYPES
        },
        "max_preset_seconds": MAX_PRESET_SECONDS,
        "max_steps": MAX_STEPS,
    }
