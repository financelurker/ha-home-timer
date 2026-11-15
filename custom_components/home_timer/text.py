"""The "save as preset" input of the area and of each station.

Entering a name saves the last custom timer's duration (or the custom duration) as a
preset of that name and renames the running custom timer. The text itself stays
empty: it is an input, not a value.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import DOMAIN
from .engine import AreaTimers, TimerError
from .entity import AreaEntity, StationEntity

KEY = "save_preset"


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    async_add_entities([AreaSavePreset(engine, KEY)])
    for station in engine.stations.values():
        async_add_entities([StationSavePreset(engine, station, KEY)], config_subentry_id=station.id)


class _SavePreset(TextEntity):
    engine: AreaTimers
    _attr_native_max = 100
    _attr_native_value = ""

    @property
    def _scope(self) -> str | None:
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        timer = self.engine.last_custom(self._scope)
        return {"custom_timer": timer.name if timer else None, "duration": round(self.engine.save_duration(self._scope))}

    async def async_set_value(self, value: str) -> None:
        if not value.strip():
            return
        try:
            self.engine.save_preset(self._scope, value)
        except TimerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
            ) from err


class AreaSavePreset(AreaEntity, _SavePreset):
    pass


class StationSavePreset(StationEntity, _SavePreset):
    @property
    def _scope(self) -> str | None:
        return self.station.id
