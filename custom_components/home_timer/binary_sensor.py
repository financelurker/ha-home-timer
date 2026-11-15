"""Binary sensor of an area: any of its timers rings."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import STATUS_RINGING
from .entity import AreaEntity, StationEntity, remove_orphans


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    engine = entry.runtime_data
    async_add_entities([RingingSensor(engine, "ringing")])
    wanted = {f"{engine.entry.entry_id}_ringing"}
    for station in engine.stations.values():
        if station.by_power:
            sensor = ApplianceRunningSensor(engine, station, "appliance_running")
            wanted.add(f"{station.id}_appliance_running")
            async_add_entities([sensor], config_subentry_id=station.id)
    remove_orphans(hass, entry.entry_id, "binary_sensor", wanted)


class RingingSensor(AreaEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return any(t.status == STATUS_RINGING for t in self.engine.timers.values())


class ApplianceRunningSensor(StationEntity, BinarySensorEntity):
    """The power station's appliance runs (power above "runs above")."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool:
        watcher = self.engine.watcher(self.station.id)
        return bool(watcher and watcher.running)
