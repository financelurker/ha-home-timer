"""To-do list of an area: its ringing timers as open to-dos; checking one off acknowledges it.

The list only offers checking items off: nothing can be added, deleted or moved, so
it never holds anything but Home Timer's own items.
"""

from __future__ import annotations

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import BY_TODO, DOMAIN, STATUS_RINGING
from .engine import TimerError
from .entity import AreaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RingingTimersList(entry.runtime_data, "ringing_timers")])


class RingingTimersList(AreaEntity, TodoListEntity):
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, engine, key) -> None:
        super().__init__(engine, key)
        self._attr_todo_items = self._items()

    def _items(self) -> list[TodoItem]:
        ringing = sorted(
            (t for t in self.engine.timers.values() if t.status == STATUS_RINGING),
            key=lambda t: t.finished_at,
        )
        stations = self.engine.stations
        items = []
        for timer in ringing:
            station = stations.get(timer.station) if timer.station else None
            station_name = station.name if station else timer.station_name
            items.append(
                TodoItem(
                    uid=timer.id,
                    summary=f"{timer.name} · {station_name}" if station_name else timer.name,
                    status=TodoItemStatus.NEEDS_ACTION,
                    due=timer.finished_at,
                    description=timer.note,
                )
            )
        return items

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.engine.async_add_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        self._attr_todo_items = self._items()
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="todo_check_off_only")
        try:
            self.engine.acknowledge(item.uid or "", by=BY_TODO)
        except TimerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
            ) from err
