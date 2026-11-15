"""One to-do list for all areas: the ringing timers as open to-dos; checking one off
acknowledges it.

The list belongs to Home Timer as a whole, not to an area: it is set up once (from
``async_setup``, as a discovered platform) and shows the ringing timers of every
loaded area. It only offers checking items off: nothing can be added, deleted or
moved, so it never holds anything but Home Timer's own items.
"""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import BY_TODO, DOMAIN, SIGNAL_TIMERS_CHANGED, STATUS_RINGING
from .engine import AreaTimers, TimerError


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    async_add_entities([RingingTimersList(hass)])


class RingingTimersList(TodoListEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "ringing_timers"
    _attr_unique_id = f"{DOMAIN}_ringing_timers"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._attr_todo_items = self._items()

    def _engines(self) -> list[AreaTimers]:
        # running areas (an area starts while its entry is still being set up)
        engines = (getattr(e, "runtime_data", None) for e in self.hass.config_entries.async_entries(DOMAIN))
        return [e for e in engines if isinstance(e, AreaTimers) and e.running]

    def _items(self) -> list[TodoItem]:
        areas = ar.async_get(self.hass)
        ringing = []
        for engine in self._engines():
            area = areas.async_get_area(engine.area_id)
            area_name = area.name if area else engine.entry.title
            stations = engine.stations
            for timer in engine.timers.values():
                if timer.status != STATUS_RINGING:
                    continue
                station = stations.get(timer.station) if timer.station else None
                station_name = station.name if station else timer.station_name
                where = f"{station_name} · {area_name}" if station_name else area_name
                ringing.append(
                    TodoItem(
                        uid=timer.id,
                        summary=f"{timer.name} · {where}",
                        status=TodoItemStatus.NEEDS_ACTION,
                        due=timer.finished_at,
                        description=timer.note,
                    )
                )
        return sorted(ringing, key=lambda item: item.due)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_TIMERS_CHANGED, self._refresh))
        self._refresh()

    @callback
    def _refresh(self) -> None:
        self._attr_todo_items = self._items()
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="todo_check_off_only")
        uid = item.uid or ""
        engine = next((e for e in self._engines() if uid in e.timers), None)
        if engine is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unknown_timer", translation_placeholders={"timer_id": uid}
            )
        try:
            engine.acknowledge(uid, by=BY_TODO)
        except TimerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
            ) from err
