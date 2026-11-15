"""Checks of what the panel saves: an area's options and quick timers, a station.

Pure functions: they take what the panel sends and return the cleaned data to
store, or errors by field (``{"presets.2.name": "preset_exists"}``); the panel shows
each error next to its field. Unknown keys are dropped.
"""

from __future__ import annotations

from typing import Any

from homeassistant.util.ulid import ulid_now

from ..const import (
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_REPORT_MISSED,
    CONF_RING_UNTIL_ACK,
    DEFAULT_OPTIONS,
)
from ..power import RULE_DEFAULTS, RULE_FIELDS, RULE_OFF, RULE_TYPES
from ..stations import (
    CONF_ICON,
    CONF_MODE,
    CONF_POWER,
    CONF_PRESETS,
    KIND_START_PRESET,
    KIND_UNTIL_OFF,
    KINDS,
    MODE_MANUAL,
    MODE_POWER,
    POWER_DEFAULTS,
)

MAX_PRESET_SECONDS = 24 * 3600 - 1
NAME_LENGTH = 60
NOTE_LENGTH = 200

# (min, max) of each number
OPTION_RANGES: dict[str, tuple[float, float]] = {CONF_REMINDER_MINUTES: (0, 60), CONF_AUTO_DISMISS_MINUTES: (0, 240)}
POWER_RANGES: dict[str, tuple[float, float]] = {
    "on_above": (0, 5000),
    "off_after": (1, 3600),
    "warn_after": (1, 1440),
    "tolerance": (5, 900),
}

Errors = dict[str, str]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _in_range(value: Any, lo: float, hi: float) -> float | None:
    number = _number(value)
    return number if number is not None and lo <= number <= hi else None


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def check_presets(raw: Any, prefix: str = "presets") -> tuple[list[dict[str, Any]], Errors]:
    """Presets in their order: a name (unique), a duration in seconds, a note; ids kept or new."""
    errors: Errors = {}
    presets: list[dict[str, Any]] = []
    seen: set[str] = set()
    ids: set[str] = set()
    for i, item in enumerate(raw if isinstance(raw, list) else []):
        item = item if isinstance(item, dict) else {}
        name = _text(item.get("name"), NAME_LENGTH)
        if not name:
            errors[f"{prefix}.{i}.name"] = "name_required"
        elif name.casefold() in seen:
            errors[f"{prefix}.{i}.name"] = "preset_exists"
        seen.add(name.casefold())
        duration = _in_range(item.get("duration"), 1, MAX_PRESET_SECONDS)
        if duration is None:
            errors[f"{prefix}.{i}.duration"] = "invalid_duration"
        raw_id = item.get("id")
        preset_id = raw_id if isinstance(raw_id, str) and raw_id.strip() and raw_id not in ids else ulid_now()
        ids.add(preset_id)
        preset: dict[str, Any] = {"id": preset_id, "name": name, "duration": round(duration or 0)}
        if note := _text(item.get("note"), NOTE_LENGTH):
            preset["note"] = note
        presets.append(preset)
    return presets, errors


def check_options(raw: Any) -> tuple[dict[str, Any], Errors]:
    """An area's options: how its timers behave when they run out."""
    raw = raw if isinstance(raw, dict) else {}
    errors: Errors = {}
    options: dict[str, Any] = {}
    for key in (CONF_RING_UNTIL_ACK, CONF_REPORT_MISSED):
        value = raw.get(key, DEFAULT_OPTIONS[key])
        if not isinstance(value, bool):
            errors[key] = "invalid"
        options[key] = bool(value)
    for key, (lo, hi) in OPTION_RANGES.items():
        value = _in_range(raw.get(key, DEFAULT_OPTIONS[key]), lo, hi)
        if value is None:
            errors[key] = "out_of_range"
        options[key] = int(value or 0)
    return options, errors


def check_rule(raw: Any) -> tuple[dict[str, Any], Errors]:
    raw = raw if isinstance(raw, dict) else {}
    rule_type = raw.get("type", RULE_OFF)
    if rule_type not in RULE_TYPES:
        return {"type": RULE_OFF}, {"power.done.type": "invalid"}
    errors: Errors = {}
    rule: dict[str, Any] = {"type": rule_type}
    for key, lo, hi, _step, _unit in RULE_FIELDS[rule_type]:
        value = _in_range(raw.get(key, RULE_DEFAULTS[rule_type].get(key)), lo, hi)
        if value is None:
            errors[f"power.done.{key}"] = "out_of_range"
        rule[key] = value if value is not None else RULE_DEFAULTS[rule_type].get(key)
    return rule, errors


def check_station(raw: Any, other_names: list[str], sensor_exists: Any = None) -> tuple[str, dict[str, Any], Errors]:
    """A station: (title, subentry data, errors).

    ``other_names``: the names of the area's other stations (names are unique per
    area). ``sensor_exists(entity_id)``: whether a power sensor is known.
    """
    raw = raw if isinstance(raw, dict) else {}
    errors: Errors = {}
    name = _text(raw.get("name"), NAME_LENGTH)
    if not name:
        errors["name"] = "name_required"
    elif name.casefold() in {n.casefold() for n in other_names}:
        errors["name"] = "station_exists"
    presets, preset_errors = check_presets(raw.get(CONF_PRESETS))
    errors.update(preset_errors)
    mode = raw.get(CONF_MODE, MODE_MANUAL)
    if mode not in (MODE_MANUAL, MODE_POWER):
        errors[CONF_MODE] = "invalid"
        mode = MODE_MANUAL
    data: dict[str, Any] = {CONF_ICON: _text(raw.get(CONF_ICON), 100) or None, CONF_PRESETS: presets, CONF_MODE: mode}

    power_raw = raw.get(CONF_POWER) if isinstance(raw.get(CONF_POWER), dict) else {}
    if mode == MODE_POWER or power_raw:
        power, power_errors = _check_power(power_raw, presets, sensor_exists)
        data[CONF_POWER] = power
        if mode == MODE_POWER:  # a manual station keeps its power settings (invalid values reset), unchecked
            errors.update(power_errors)
    return name, data, errors


def _check_power(raw: dict[str, Any], presets: list[dict[str, Any]], sensor_exists: Any) -> tuple[dict[str, Any], Errors]:
    errors: Errors = {}
    sensor = raw.get("sensor") if isinstance(raw.get("sensor"), str) else ""
    if not sensor.startswith("sensor."):
        errors["power.sensor"] = "sensor_required"
    elif sensor_exists is not None and not sensor_exists(sensor):
        errors["power.sensor"] = "unknown_sensor"
    power: dict[str, Any] = {"sensor": sensor}
    for key, (lo, hi) in POWER_RANGES.items():
        value = _in_range(raw.get(key, POWER_DEFAULTS[key]), lo, hi)
        if value is None:
            errors[f"power.{key}"] = "out_of_range"
        power[key] = value if value is not None else POWER_DEFAULTS[key]
    kind = raw.get("kind", KIND_UNTIL_OFF)
    if kind not in KINDS:
        errors["power.kind"] = "invalid"
        kind = KIND_UNTIL_OFF
    power["kind"] = kind
    preset = raw.get("preset")
    if kind == KIND_START_PRESET:
        ids = {p["id"] for p in presets}
        if not preset or preset not in ids:
            errors["power.preset"] = "preset_required"
    power["preset"] = preset if isinstance(preset, str) and preset else None
    power["cancel_on_off"] = bool(raw.get("cancel_on_off", POWER_DEFAULTS["cancel_on_off"]))
    power["done"], rule_errors = check_rule(raw.get("done"))
    errors.update(rule_errors)
    return power, errors


def draft_power(raw: Any, saved: dict[str, Any]) -> dict[str, Any]:
    """Power settings as being edited, for the preview: each value that cannot be used
    (a field being typed, out of range) falls back to the saved one – never an error."""
    raw = raw if isinstance(raw, dict) else {}
    power = {**POWER_DEFAULTS, **saved}
    for key, (lo, hi) in POWER_RANGES.items():
        value = _in_range(raw.get(key), lo, hi)
        if value is not None:
            power[key] = value
    if raw.get("kind") in KINDS:
        power["kind"] = raw["kind"]
    if "preset" in raw:
        power["preset"] = raw["preset"] if isinstance(raw["preset"], str) else None
    if isinstance(raw.get("cancel_on_off"), bool):
        power["cancel_on_off"] = raw["cancel_on_off"]
    done = raw.get("done") if isinstance(raw.get("done"), dict) else {}
    rule_type = done.get("type") if done.get("type") in RULE_TYPES else (power.get("done") or {}).get("type", RULE_OFF)
    before = power.get("done") if isinstance(power.get("done"), dict) and power["done"].get("type") == rule_type else {}
    rule: dict[str, Any] = {"type": rule_type}
    for key, lo, hi, _step, _unit in RULE_FIELDS.get(rule_type, []):
        value = _in_range(done.get(key), lo, hi)
        if value is None:
            value = _in_range(before.get(key), lo, hi)
        rule[key] = value if value is not None else RULE_DEFAULTS[rule_type].get(key)
    power["done"] = rule
    return power
