"""Buttons: acknowledge all (area); per station: its presets, pause/resume, +1 minute, stop."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import BY_BUTTON, DOMAIN, STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING
from .engine import TimerError
from .entity import AreaEntity, StationEntity
from .stations import Preset

PRESET_KEY = "start_preset"


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    async_add_entities([AcknowledgeAllButton(engine, "acknowledge_all")])
    wanted: set[str] = {f"{engine.entry.entry_id}_acknowledge_all"}
    for station in engine.stations.values():
        buttons: list[ButtonEntity] = [PresetButton(engine, station, p) for p in station.presets]
        buttons += [
            PauseResumeButton(engine, station, "pause_resume"),
            AddMinuteButton(engine, station, "add_minute"),
            StopButton(engine, station, "stop"),
        ]
        wanted.update(b.unique_id for b in buttons if b.unique_id)
        async_add_entities(buttons, config_subentry_id=station.id)
    # buttons of deleted presets
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == "button" and entity.platform == DOMAIN and entity.unique_id not in wanted:
            registry.async_remove(entity.entity_id)


def _run(func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except TimerError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
        ) from err


class AcknowledgeAllButton(AreaEntity, ButtonEntity):
    """Does nothing while nothing rings (always available, so a dashboard button stays usable)."""

    async def async_press(self) -> None:
        self.engine.acknowledge_all(by=BY_BUTTON)


class PresetButton(StationEntity, ButtonEntity):
    """Starts the preset on the station, replacing a timer the station has."""

    def __init__(self, engine, station, preset: Preset) -> None:
        super().__init__(engine, station, PRESET_KEY)
        self.preset = preset
        self._attr_unique_id = f"{station.id}_preset_{preset.id}"
        self._attr_translation_placeholders = {"preset": preset.name}

    async def async_press(self) -> None:
        _run(self.engine.start_preset, self.station.id, self.preset.id)


class PauseResumeButton(StationEntity, ButtonEntity):
    async def async_press(self) -> None:
        timer = self.timer
        if timer is None:
            return
        if timer.status == STATUS_ACTIVE:
            _run(self.engine.pause, timer.id)
        elif timer.status == STATUS_PAUSED:
            _run(self.engine.resume, timer.id)


class AddMinuteButton(StationEntity, ButtonEntity):
    """+1 minute; a ringing timer starts again with one minute."""

    async def async_press(self) -> None:
        if (timer := self.timer) is not None:
            _run(self.engine.add_time, timer.id, 60)


class StopButton(StationEntity, ButtonEntity):
    """Acknowledges a ringing timer, cancels a running or paused one."""

    async def async_press(self) -> None:
        timer = self.timer
        if timer is None:
            return
        if timer.status == STATUS_RINGING:
            _run(self.engine.acknowledge, timer.id, by=BY_BUTTON)
        else:
            _run(self.engine.cancel, timer.id)
