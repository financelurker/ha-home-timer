"""To-do lists. One for all areas: the ringing timers as open to-dos; checking one off
acknowledges it. And one per area: all its timers with their end as due time; checking
one off stops it (or acknowledges it while it rings).

The list belongs to Home Timer as a whole, not to an area: it is set up once (from
``async_setup``, as a discovered platform) and shows the ringing timers of every
loaded area. It only offers checking items off: nothing can be added, deleted or
moved, so it never holds anything but Home Timer's own items.
"""

from __future__ import annotations

import math

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import BY_TODO, DOMAIN, SIGNAL_TIMERS_CHANGED, STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING
from .engine import AreaTimers, Timer, TimerError
from .entity import AreaEntity


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    if discovery_info is None:
        return
    # kept so an area set up after the list was removed (last area deleted) brings it back
    hass.data.setdefault(DOMAIN, {})[DATA_ADD_LIST] = async_add_entities
    async_add_entities([RingingTimersList(hass)])


DATA_ADD_LIST = "todo_add_entities"
DATA_LIST = "todo_list"
LIST_UNIQUE_ID = f"{DOMAIN}_ringing_timers"


@callback
def async_ensure_list(hass: HomeAssistant) -> None:
    """Add the list again if it was removed (an area is set up after the last one was deleted)."""
    data = hass.data.get(DOMAIN, {})
    if data.get(DATA_LIST) is None and (add := data.get(DATA_ADD_LIST)) is not None:
        add([RingingTimersList(hass)])


@callback
def async_remove_list(hass: HomeAssistant) -> None:
    """The last area is deleted: the list goes too (it belongs to no entry, so HA would keep it)."""
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id("todo", DOMAIN, LIST_UNIQUE_ID):
        registry.async_remove(entity_id)


class RingingTimersList(TodoListEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "ringing_timers"
    _attr_unique_id = LIST_UNIQUE_ID
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
        self.hass.data.setdefault(DOMAIN, {})[DATA_LIST] = self
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_TIMERS_CHANGED, self._refresh))
        # area names are part of the summaries
        self.async_on_remove(
            self.hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, self._area_changed)
        )
        self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self.hass.data.get(DOMAIN, {}).get(DATA_LIST) is self:
            self.hass.data[DOMAIN][DATA_LIST] = None

    @callback
    def _area_changed(self, _event: Event) -> None:
        self._refresh()

    @callback
    def _refresh(self) -> None:
        self._attr_todo_items = self._items()
        self.async_write_ha_state()

    # Assist's list intents call these regardless of the supported features: answer cleanly
    async def async_create_todo_item(self, item: TodoItem) -> None:
        raise intent.IntentHandleError("Items cannot be added to the list of ringing timers")

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        raise intent.IntentHandleError("Ringing timers cannot be removed here; check them off to acknowledge them")

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        raise intent.IntentHandleError("The list of ringing timers cannot be reordered")

    async def async_update_todo_item(self, item: TodoItem) -> None:
        # with status "completed", any other change (e.g. a rename) is ignored: the item is acknowledged
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


# ---------------------------------------------------------------- per area


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    # "Home Timer: Kitchen", next to "Home Timer: Ringing timers" in the to-do panel
    strings = await async_get_translations(hass, hass.config.language, "entity", {DOMAIN})
    pattern = strings.get(f"component.{DOMAIN}.entity.todo.area_timers.name") or "Home Timer: {area}"
    states = {
        status: strings.get(f"component.{DOMAIN}.entity.sensor.station.state.{status}") or status
        for status in (STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING)
    }
    engine = entry.runtime_data
    area = ar.async_get(hass).async_get_area(engine.area_id) if engine.area_id else None
    area_name = area.name if area else entry.title
    new = er.async_get(hass).async_get_entity_id("todo", DOMAIN, f"{entry.entry_id}_area_timers") is None
    async_add_entities([AreaTimersList(engine, "area_timers", pattern.replace("{area}", area_name), area_name, new, states)])


def _check_off(engine: AreaTimers, uid: str) -> None:
    timer = engine.timers.get(uid)
    if timer is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="unknown_timer", translation_placeholders={"timer_id": uid}
        )
    try:
        if timer.status == STATUS_RINGING:
            engine.acknowledge(uid, by=BY_TODO)
        else:
            engine.cancel(uid)
    except TimerError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=err.key, translation_placeholders=err.placeholders
        ) from err


class AreaTimersList(AreaEntity, TodoListEntity):
    """All timers of the area, its stations' too: due when they run out (ringing ones are
    overdue). Named "Home Timer: <area>". Only checking off: stops or acknowledges."""

    _attr_has_entity_name = False  # "Home Timer: Kitchen" – the area's name as the user wrote it, in any language
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(self, engine: AreaTimers, key: str, name: str, area_name: str, new: bool,
                 states: dict[str, str]) -> None:
        super().__init__(engine, key)
        self._new = new
        self._states = states  # "Running", "Paused", "Ringing" in the system language
        self._attr_name = name
        # not on the hub device: Home Assistant would put its name ("Home Timer") in front
        self._attr_device_info = None
        self.entity_id = f"todo.{slugify(area_name)}_timers"  # todo.kitchen_timers (kept once registered)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # in the area (it has no device to take the area from) – once, when it is new, so a changed area stays
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        area = ar.async_get(self.hass).async_get_area(self.engine.area_id) if self.engine.area_id else None
        if self._new and entry is not None and entry.area_id is None and area is not None:
            registry.async_update_entity(self.entity_id, area_id=self.engine.area_id)

    def _item(self, timer: Timer) -> TodoItem:
        station = self.engine.stations.get(timer.station) if timer.station else None
        station_name = station.name if station else timer.station_name
        summary = f"{timer.name} · {station_name}" if station_name and station_name != timer.name else timer.name
        now = dt_util.utcnow()
        state = self._states.get(timer.status, timer.status)
        if timer.status == STATUS_RINGING:
            due = timer.finished_at
        elif timer.status == STATUS_PAUSED:
            due, state = None, f"{state} · {max(1, math.ceil(timer.remaining(now) / 60))} min"
        else:
            due = self.engine.expected_end(timer)
            if timer.open and due is not None and due <= now:
                due = None  # an appliance taking longer than usual is not overdue
        description = " · ".join(x for x in (state, timer.note) if x)
        return TodoItem(uid=timer.id, summary=summary, status=TodoItemStatus.NEEDS_ACTION, due=due, description=description)

    @property
    def todo_items(self) -> list[TodoItem]:
        items = [self._item(t) for t in self.engine.timers.values()]
        return sorted(items, key=lambda item: (item.due is None, item.due or dt_util.utcnow()))

    async def async_create_todo_item(self, item: TodoItem) -> None:
        raise intent.IntentHandleError("Items cannot be added to the list of timers")

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        raise intent.IntentHandleError("Timers cannot be removed here; check them off to stop them")

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        raise intent.IntentHandleError("The list of timers cannot be reordered")

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="todo_check_off_only")
        _check_off(self.engine, item.uid or "")
