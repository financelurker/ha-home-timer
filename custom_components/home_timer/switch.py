"""Switches: the area's quick timers and custom timer, a station's presets and custom timer.

A switch is on while its run goes on (running, paused or ringing): turning it on starts
it, turning it off stops a running one or acknowledges a ringing one. Each quick timer
has one run at a time; starting it again starts it anew. A station has one timer, so at
most one of its switches is on: turning on another replaces it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import BY_SWITCH, DOMAIN, STATUS_RINGING
from .engine import QUICK_CUSTOM, Timer, TimerError
from .entity import AreaEntity, StationEntity, remove_orphans
from .stations import Preset


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    switches: list[SwitchEntity] = [
        CustomTimerSwitch(engine, "custom_timer"),  # first: a quick timer called "Custom timer" must not take its id
        *(QuickTimerSwitch(engine, p) for p in engine.presets(None)),
    ]
    async_add_entities(switches)
    wanted = {s.unique_id for s in switches if s.unique_id}
    for station in engine.stations.values():
        if not station.manual_controls:
            continue
        mine: list[SwitchEntity] = [
            StationCustomSwitch(engine, station, "custom_timer"),
            *(StationPresetSwitch(engine, station, p) for p in station.presets),
        ]
        wanted.update(s.unique_id for s in mine if s.unique_id)
        async_add_entities(mine, config_subentry_id=station.id)
    remove_orphans(hass, entry.entry_id, "switch", wanted)


def _run(func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except TimerError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
        ) from err


class _RunSwitch(SwitchEntity):
    """On while the run (``self.key``, in the area or on ``self.scope``'s station) goes on."""

    key: str
    scope: str | None = None

    @property
    def run(self) -> Timer | None:
        return self.engine.quick_timer(self.key, self.scope)

    @property
    def is_on(self) -> bool:
        return self.run is not None

    @property
    def icon(self) -> str | None:
        run = self.run
        return "mdi:bell-ring" if run is not None and run.status == STATUS_RINGING else None  # else icons.json

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self.run
        return {"status": run.status, "timer_id": run.id} if run else {}

    async def async_turn_off(self, **kwargs: Any) -> None:
        run = self.run
        if run is None:
            return
        if run.status == STATUS_RINGING:
            _run(self.engine.acknowledge, run.id, by=BY_SWITCH)
        else:
            _run(self.engine.cancel, run.id)


class QuickTimerSwitch(_RunSwitch, AreaEntity):
    """A quick timer of the area (a timer without a station)."""

    def __init__(self, engine, preset: Preset) -> None:
        super().__init__(engine, "quick_timer")
        self.preset = preset
        self.key = preset.id
        self._attr_unique_id = f"{engine.entry.entry_id}_quick_{preset.id}"
        self._attr_translation_placeholders = {"preset": preset.name}

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            _run(self.engine.start_preset, None, self.preset.id)


class CustomTimerSwitch(_RunSwitch, AreaEntity):
    """The area's custom timer: a timer "Timer" with the custom duration."""

    key = QUICK_CUSTOM

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            _run(self.engine.start_custom, None)


class StationPresetSwitch(_RunSwitch, StationEntity):
    """A preset of the station: on starts it (replacing the station's timer)."""

    def __init__(self, engine, station, preset: Preset) -> None:
        super().__init__(engine, station, "station_preset")
        self.preset = preset
        self.key = preset.id
        self.scope = station.id
        self._attr_unique_id = f"{station.id}_preset_{preset.id}"
        self._attr_translation_placeholders = {"preset": preset.name}

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            _run(self.engine.start_preset, self.station.id, self.preset.id)


class StationCustomSwitch(_RunSwitch, StationEntity):
    """The station's custom timer: a timer "Timer" with its custom duration (replacing its timer)."""

    key = QUICK_CUSTOM

    def __init__(self, engine, station, key: str) -> None:
        super().__init__(engine, station, key)
        self.scope = station.id

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            _run(self.engine.start_custom, self.station.id)
