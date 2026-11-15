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

SUBENTRY_STATION = "station"
CONF_ICON = "icon"
CONF_PRESETS = "presets"


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
class Station:
    id: str
    name: str
    icon: str | None = None
    presets: tuple[Preset, ...] = field(default_factory=tuple)

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
        )
    return result
