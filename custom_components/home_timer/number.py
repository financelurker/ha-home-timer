"""Custom duration (minutes) of the area and of each station, for "Start custom timer"."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import DOMAIN
from .engine import AreaTimers, TimerError
from .entity import AreaEntity, StationEntity

KEY = "custom_minutes"


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    async_add_entities([AreaCustomMinutes(engine, KEY)])
    for station in engine.stations.values():
        async_add_entities([StationCustomMinutes(engine, station, KEY)], config_subentry_id=station.id)


class _CustomMinutes(NumberEntity):
    engine: AreaTimers
    _attr_native_min_value = 1
    _attr_native_max_value = 600
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def _scope(self) -> str | None:
        return None

    @property
    def native_value(self) -> float:
        return round(self.engine.custom_duration(self._scope) / 60, 2)

    async def async_set_native_value(self, value: float) -> None:
        try:
            self.engine.set_custom_duration(self._scope, value * 60)
        except TimerError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=err.key) from err


class AreaCustomMinutes(AreaEntity, _CustomMinutes):
    pass


class StationCustomMinutes(StationEntity, _CustomMinutes):
    @property
    def _scope(self) -> str | None:
        return self.station.id
