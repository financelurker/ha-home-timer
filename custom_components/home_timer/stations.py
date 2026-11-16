"""Stations and their presets, as stored in the area's config subentries.

The area's own presets ("quick timers", for timers without a station) are kept in
the entry's options under ``presets``, in the same format.

A station is a config subentry of type ``station``: its id is the subentry id, its
name the subentry title. ``data`` holds the icon and the presets:

    {"icon": "mdi:stove", "presets": [{"id": "…", "name": "Rice", "duration": 600, "note": "…"}]}

Each preset has its own id, so renaming it keeps its button's entity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .power import DoneSteps

SUBENTRY_STATION = "station"
CONF_ICON = "icon"
CONF_PRESETS = "presets"
CONF_MODE = "mode"
CONF_POWER = "power"

MODE_MANUAL = "manual"
MODE_POWER = "power"

KIND_UNTIL_OFF = "until_off"  # runs until it switches off by itself; done by a rule
KIND_START_PRESET = "start_preset"  # switching on starts a preset
KIND_WARN = "warn"  # warns if it runs too long
KINDS = (KIND_UNTIL_OFF, KIND_START_PRESET, KIND_WARN)

POWER_DEFAULTS: dict[str, Any] = {
    "on_above": 10.0,  # W: runs above this
    "off_after": 30.0,  # s below it: stopped
    "kind": KIND_UNTIL_OFF,
    "preset": None,  # start_preset: the preset's id
    "cancel_on_off": True,  # start_preset: switching off before the end cancels the timer
    "warn_after": 120.0,  # warn: minutes
    "done": {"type": "off"},  # until_off: the done rule
    "tolerance": 60.0,  # s: learning – a ring up to this long after the user's mark is on time
}


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    duration: float  # seconds
    note: str | None = None

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Preset:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            duration=float(data["duration"]),
            note=data.get("note") or None,
        )

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "name": self.name, "duration": self.duration}
        if self.note:
            data["note"] = self.note
        return data


@dataclass(frozen=True)
class PowerConfig:
    """How a power station reads its appliance."""

    sensor: str
    on_above: float = POWER_DEFAULTS["on_above"]
    off_after: float = POWER_DEFAULTS["off_after"]
    kind: str = KIND_UNTIL_OFF
    preset: str | None = None
    cancel_on_off: bool = True
    warn_after: float = POWER_DEFAULTS["warn_after"]
    done: DoneSteps = field(default_factory=DoneSteps)
    tolerance: float = field(default=POWER_DEFAULTS["tolerance"], compare=False)  # learning only

    @classmethod
    def from_data(cls, data: dict[str, Any] | None) -> PowerConfig | None:
        if not data or not data.get("sensor"):
            return None
        merged = {**POWER_DEFAULTS, **data}
        kind = merged["kind"] if merged["kind"] in KINDS else KIND_UNTIL_OFF
        return cls(
            sensor=str(merged["sensor"]),
            on_above=float(merged["on_above"]),
            off_after=float(merged["off_after"]),
            kind=kind,
            preset=merged.get("preset") or None,
            cancel_on_off=bool(merged["cancel_on_off"]),
            warn_after=float(merged["warn_after"]),
            done=DoneSteps.from_data(merged.get("done")),
            tolerance=float(merged["tolerance"]),
        )


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    icon: str | None = None
    presets: tuple[Preset, ...] = field(default_factory=tuple)
    power: PowerConfig | None = None  # None: started by hand

    @property
    def by_power(self) -> bool:
        return self.power is not None

    @property
    def manual_controls(self) -> bool:
        """Presets, custom timer, pause and +1: by hand, or a power station that starts presets."""
        return self.power is None or self.power.kind == KIND_START_PRESET

    def preset(self, key: str) -> Preset | None:
        """A preset by id or by name (case-insensitive)."""
        return find_preset(self.presets, key)


def parse_presets(raw_presets: Any) -> tuple[Preset, ...]:
    presets = []
    for raw in raw_presets or []:
        try:
            presets.append(Preset.from_data(raw))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(presets)


def area_presets(entry: ConfigEntry) -> tuple[Preset, ...]:
    """The area's quick timers: presets without a station, kept in the entry's options."""
    return parse_presets(entry.options.get(CONF_PRESETS))


def find_preset(presets: tuple[Preset, ...], key: str) -> Preset | None:
    """A preset by id or by name (case-insensitive)."""
    for preset in presets:
        if preset.id == key:
            return preset
    wanted = key.strip().casefold()
    return next((p for p in presets if p.name.casefold() == wanted), None)


def stations(entry: ConfigEntry) -> dict[str, Station]:
    """The area's stations by id (subentry id), in the order they were added."""
    result: dict[str, Station] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_STATION:
            continue
        result[subentry.subentry_id] = Station(
            id=subentry.subentry_id,
            name=subentry.title,
            icon=subentry.data.get(CONF_ICON) or None,
            presets=parse_presets(subentry.data.get(CONF_PRESETS)),
            power=PowerConfig.from_data(subentry.data.get(CONF_POWER))
            if subentry.data.get(CONF_MODE) == MODE_POWER
            else None,
        )
    return result
