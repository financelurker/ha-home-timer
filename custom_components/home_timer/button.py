"""Buttons. Area: acknowledge all, its quick timers, start custom timer. Station: its
presets, start custom timer, pause/resume, +1 minute, stop."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import BY_BUTTON, DOMAIN, STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING
from .engine import TimerError
from .entity import AreaEntity, StationEntity, remove_orphans
from .stations import KIND_UNTIL_OFF, Preset

PRESET_KEY = "start_preset"


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    area_buttons: list[ButtonEntity] = [
        AcknowledgeAllButton(engine, "acknowledge_all"),
        *(AreaPresetButton(engine, p) for p in engine.presets(None)),
        AreaStartCustomButton(engine, "start_custom"),
    ]
    async_add_entities(area_buttons)
    wanted: set[str] = {b.unique_id for b in area_buttons if b.unique_id}
    for station in engine.stations.values():
        buttons: list[ButtonEntity] = []
        if station.manual_controls:
            buttons += [PresetButton(engine, station, p) for p in station.presets]
            buttons += [
                StationStartCustomButton(engine, station, "start_custom"),
                PauseResumeButton(engine, station, "pause_resume"),
                AddMinuteButton(engine, station, "add_minute"),
            ]
        buttons.append(StopButton(engine, station, "stop"))
        if station.power is not None and station.power.kind == KIND_UNTIL_OFF:
            buttons.append(DoneNowButton(engine, station, "done_now"))
        wanted.update(b.unique_id for b in buttons if b.unique_id)
        async_add_entities(buttons, config_subentry_id=station.id)
    remove_orphans(hass, entry.entry_id, "button", wanted)


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


class AreaPresetButton(AreaEntity, ButtonEntity):
    """Starts a quick timer of the area (a timer without a station)."""

    def __init__(self, engine, preset: Preset) -> None:
        super().__init__(engine, PRESET_KEY)
        self.preset = preset
        self._attr_unique_id = f"{engine.entry.entry_id}_preset_{preset.id}"
        self._attr_translation_placeholders = {"preset": preset.name}

    async def async_press(self) -> None:
        _run(self.engine.start_preset, None, self.preset.id)


class AreaStartCustomButton(AreaEntity, ButtonEntity):
    """Starts a timer "Timer" with the area's custom duration."""

    async def async_press(self) -> None:
        _run(self.engine.start_custom, None)


class StationStartCustomButton(StationEntity, ButtonEntity):
    """Starts a timer "Timer" with the station's custom duration, replacing its timer."""

    async def async_press(self) -> None:
        _run(self.engine.start_custom, self.station.id)


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


class DoneNowButton(StationEntity, ButtonEntity):
    """Learning: the user says the appliance's run is done now (a mark on the run's curve).

    It does not ring or stop anything; the panel uses the marks to suggest done rules.
    """

    async def async_press(self) -> None:
        watcher = self.engine.watcher(self.station.id)
        try:
            if watcher is None:
                raise ValueError("no watcher")
            watcher.mark_done()
        except ValueError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_running") from err
