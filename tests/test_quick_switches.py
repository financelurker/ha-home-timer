"""Quick timers and the custom timer of an area as switches, their ends, and the area's list of timers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.home_timer.const import DOMAIN, STORAGE_VERSION
from custom_components.home_timer.engine import storage_key

from .conftest import add_station, tick
from .test_engine import events

TEA = "switch.kitchen_home_timer_tea"
TEA_ENDS = "sensor.kitchen_home_timer_tea_ends"
CUSTOM = "switch.kitchen_home_timer_custom_timer"
CUSTOM_ENDS = "sensor.kitchen_home_timer_custom_timer_ends"
LIST = "todo.kitchen_timers"


async def quick(hass, entry, *presets) -> None:
    hass.config_entries.async_update_entry(
        entry, options={"presets": [{"id": f"q{i}", "name": n, "duration": d} for i, (n, d) in enumerate(presets)]}
    )
    await hass.async_block_till_done()


async def switch(hass, entity_id, on: bool) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def items(hass) -> list[dict]:
    response = await hass.services.async_call("todo", "get_items", {"entity_id": LIST}, blocking=True, return_response=True)
    return response[LIST]["items"]


async def test_a_quick_timer_switch_starts_stops_and_acknowledges(hass: HomeAssistant, kitchen, freezer) -> None:
    await quick(hass, kitchen, ("Tea", 60))
    ev = events(hass)
    assert hass.states.get(TEA).state == "off"
    assert hass.states.get(TEA_ENDS).state == "unknown"
    await switch(hass, TEA, True)
    assert hass.states.get(TEA).state == "on"
    assert hass.states.get(TEA_ENDS).state not in ("unknown", "unavailable")
    await switch(hass, TEA, True)  # on already: nothing new
    assert len(ev["started"]) == 1
    await switch(hass, TEA, False)
    assert hass.states.get(TEA).state == "off" and ev["cancelled"][0].data["reason"] == "manual"
    await switch(hass, TEA, True)
    await tick(hass, freezer, 61)
    state = hass.states.get(TEA)
    assert state.state == "on" and state.attributes["status"] == "ringing" and state.attributes["icon"] == "mdi:bell-ring"
    assert hass.states.get(TEA_ENDS).state not in ("unknown", "unavailable")  # when it ran out
    await switch(hass, TEA, False)
    assert ev["acknowledged"][0].data["by"] == "switch"
    assert hass.states.get(TEA).state == "off"


async def test_one_run_per_quick_timer_also_by_action(hass: HomeAssistant, kitchen) -> None:
    await quick(hass, kitchen, ("Tea", 60))
    ev = events(hass)
    for _ in range(2):
        await hass.services.async_call(DOMAIN, "start", {"area_id": kitchen.data["area_id"], "preset": "Tea"}, blocking=True)
    assert [t.name for t in kitchen.runtime_data.timers.values()] == ["Tea"]
    assert ev["cancelled"][0].data["reason"] == "replaced"
    # a timer of the same name, started without the preset, is not the quick timer's run
    kitchen.runtime_data.start("Tea", 30)
    await hass.async_block_till_done()
    assert len(kitchen.runtime_data.timers) == 2
    await switch(hass, TEA, False)
    assert [t.name for t in kitchen.runtime_data.timers.values()] == ["Tea"]


async def test_the_custom_timer_switch_and_its_end(hass: HomeAssistant, kitchen) -> None:
    await switch(hass, CUSTOM, True)
    timer = next(iter(kitchen.runtime_data.timers.values()))
    assert (timer.name, round(timer.duration)) == ("Timer", 300)
    assert hass.states.get(CUSTOM_ENDS).state not in ("unknown", "unavailable")
    await switch(hass, CUSTOM, False)
    assert kitchen.runtime_data.timers == {}


async def test_assigned_to_a_station_it_leaves_the_switch(hass: HomeAssistant, kitchen) -> None:
    await quick(hass, kitchen, ("Tea", 600))
    await add_station(hass, kitchen, "Hob 1")
    await switch(hass, TEA, True)
    timer = next(iter(kitchen.runtime_data.timers.values()))
    await hass.services.async_call(DOMAIN, "assign", {"timer_id": timer.id, "entity_id": "sensor.kitchen_hob_1"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(TEA).state == "off"
    await switch(hass, TEA, True)
    assert len(kitchen.runtime_data.timers) == 2


async def test_the_area_list_of_timers(hass: HomeAssistant, kitchen, freezer) -> None:
    await quick(hass, kitchen, ("Tea", 60))
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    assert hass.states.get(LIST).attributes["friendly_name"] == "Home Timer: Kitchen timers"
    from homeassistant.helpers import entity_registry as er
    assert er.async_get(hass).async_get(LIST).area_id == kitchen.data["area_id"]
    await switch(hass, TEA, True)
    kitchen.runtime_data.start_preset(hob, "Rice")
    paused = kitchen.runtime_data.start("Dough", 1800)
    kitchen.runtime_data.pause(paused.id)
    await tick(hass, freezer, 61)
    listed = await items(hass)
    assert [i["summary"] for i in listed] == ["Tea", "Rice · Hob 1", "Dough"]  # by due: ringing (overdue) first, paused last
    assert listed[0]["description"] == "Ringing" and listed[2]["description"] == "Paused · 30 min"
    assert "due" in listed[1] and "due" not in listed[2]
    ev = events(hass)
    for item in listed[:2]:
        await hass.services.async_call("todo", "update_item", {"entity_id": LIST, "item": item["uid"], "status": "completed"}, blocking=True)
    await hass.async_block_till_done()
    assert ev["acknowledged"][0].data["by"] == "todo" and ev["cancelled"][0].data["name"] == "Rice"
    assert [i["summary"] for i in await items(hass)] == ["Dough"]


async def test_running_timers_are_linked_on_update(hass: HomeAssistant, hass_storage, freezer) -> None:
    """Timers stored before the switches: a quick timer's run (by its preset) and the last custom timer."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.home_timer.const import CONF_AREA_ID

    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id},
                            options={"presets": [{"id": "q0", "name": "Tea", "duration": 600}]})
    now = dt_util.utcnow()

    def stored(tid, name, preset, minutes_ago):
        start = now - dt_util.dt.timedelta(minutes=minutes_ago)
        return {"id": tid, "name": name, "duration": 600, "status": "active", "started_at": start.isoformat(),
                "ends_at": (start + dt_util.dt.timedelta(seconds=600)).isoformat(), "preset": preset, "station": None}

    hass_storage[storage_key(entry.entry_id)] = {"version": STORAGE_VERSION, "key": storage_key(entry.entry_id), "data": {
        "timers": [stored("old-tea", "Tea", "Tea", 5), stored("new-tea", "Tea", "tea", 1), stored("custom", "Timer", None, 2),
                   stored("other", "Dough", None, 3)],
        "last_custom": {"": {"timer_id": "custom", "duration": 600}}}}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    assert {t.id: t.quick for t in engine.timers.values()} == {"old-tea": None, "new-tea": "q0", "custom": "custom", "other": None}
    assert hass.states.get(TEA).state == "on" and hass.states.get(TEA).attributes["timer_id"] == "new-tea"
    assert hass.states.get(CUSTOM).state == "on"
    await switch(hass, TEA, False)
    assert "new-tea" not in engine.timers and "old-tea" in engine.timers  # the older one goes on as a plain timer
    # linked once: after a reload the older one does not become Tea's run
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TEA).state == "off" and entry.runtime_data.timers["old-tea"].quick is None


async def test_the_list_on_a_leap_day_and_in_a_deleted_area(hass: HomeAssistant, freezer) -> None:
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.home_timer.const import CONF_AREA_ID

    freezer.move_to("2028-02-29 12:00:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, title="Attic", unique_id="gone", data={CONF_AREA_ID: "gone"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.start("Dough", 60)
    paused = entry.runtime_data.start("Bread", 60)
    entry.runtime_data.pause(paused.id)
    await hass.async_block_till_done()
    assert hass.states.get("todo.attic_timers").state == "2"
    assert er.async_get(hass).async_get("todo.attic_timers").area_id is None  # no area that does not exist
    assert ar.async_get(hass).async_get_area("gone") is None


async def test_custom_timer_keeps_its_id_next_to_a_quick_timer_of_that_name(hass: HomeAssistant, kitchen) -> None:
    await quick(hass, kitchen, ("Custom timer", 60))
    from homeassistant.helpers import entity_registry as er
    entity = er.async_get(hass).async_get(CUSTOM)
    assert entity is not None and entity.unique_id.endswith("_custom_timer")


async def test_a_station_has_at_most_one_switch_on(hass: HomeAssistant, kitchen, freezer) -> None:
    await add_station(hass, kitchen, "Hob 1", [("Rice", 60), ("Pasta", 540)])
    rice, pasta, custom = "switch.kitchen_hob_1_rice", "switch.kitchen_hob_1_pasta", "switch.kitchen_hob_1_custom_timer"
    ev = events(hass)
    await switch(hass, rice, True)
    assert [hass.states.get(e).state for e in (rice, pasta, custom)] == ["on", "off", "off"]
    await switch(hass, pasta, True)  # replaces Rice
    assert [hass.states.get(e).state for e in (rice, pasta, custom)] == ["off", "on", "off"]
    assert ev["cancelled"][0].data["reason"] == "replaced"
    await switch(hass, custom, True)
    assert [hass.states.get(e).state for e in (rice, pasta, custom)] == ["off", "off", "on"]
    await switch(hass, custom, False)
    assert hass.states.get("sensor.kitchen_hob_1").state == "idle"
    await switch(hass, rice, True)
    await tick(hass, freezer, 61)
    assert hass.states.get(rice).attributes["icon"] == "mdi:bell-ring"
    await switch(hass, rice, False)
    assert ev["acknowledged"][0].data["by"] == "switch"


async def test_assigned_to_a_station_with_that_preset_it_is_its_run(hass: HomeAssistant, kitchen) -> None:
    await quick(hass, kitchen, ("Rice", 600))
    await add_station(hass, kitchen, "Hob 1", [("Rice", 900)])
    await switch(hass, "switch.kitchen_home_timer_rice", True)
    timer = next(iter(kitchen.runtime_data.timers.values()))
    await hass.services.async_call(DOMAIN, "assign", {"timer_id": timer.id, "entity_id": "sensor.kitchen_hob_1"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.kitchen_home_timer_rice").state == "off"
    assert hass.states.get("switch.kitchen_hob_1_rice").state == "on"


async def test_station_timers_are_linked_on_update(hass: HomeAssistant, hass_storage) -> None:
    """Stored after the area's switches came (quick_linked: true), before the stations' did."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.home_timer.const import CONF_AREA_ID

    area = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", unique_id=area.id, data={CONF_AREA_ID: area.id},
        options={"presets": [{"id": "q0", "name": "Tea", "duration": 600}]},
        subentries_data=[
            {"subentry_id": "hob1", "subentry_type": "station", "title": "Hob 1", "unique_id": None,
             "data": {"name": "Hob 1", "presets": [{"id": "p1", "name": "Rice", "duration": 600}]}},
            {"subentry_id": "hob2", "subentry_type": "station", "title": "Hob 2", "unique_id": None,
             "data": {"name": "Hob 2", "presets": []}},
        ],
    )
    now = dt_util.utcnow()

    def stored(tid, name, preset, station):
        return {"id": tid, "name": name, "duration": 600, "status": "active", "started_at": now.isoformat(),
                "ends_at": (now + dt_util.dt.timedelta(seconds=600)).isoformat(), "preset": preset, "station": station}

    hass_storage[storage_key(entry.entry_id)] = {"version": STORAGE_VERSION, "key": storage_key(entry.entry_id), "data": {
        "timers": [stored("rice", "Rice", "Rice", "hob1"), stored("custom", "Timer", None, "hob2"), stored("tea", "Tea", "Tea", None)],
        "last_custom": {"hob2": {"timer_id": "custom", "duration": 600}}, "quick_linked": True}}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert {t.id: t.quick for t in entry.runtime_data.timers.values()} == {"rice": "p1", "custom": "custom", "tea": None}
    assert hass.states.get("switch.kitchen_hob_1_rice").state == "on"
    assert hass.states.get("switch.kitchen_hob_2_custom_timer").state == "on"
    assert hass.states.get(TEA).state == "off"  # the area's timers were linked before (and Tea was not Tea's run then)


async def test_the_old_station_start_buttons_are_removed(hass: HomeAssistant, kitchen) -> None:
    station = await add_station(hass, kitchen, "Hob 1", [("Rice", 60)])
    registry = er.async_get(hass)
    for unique_id in (f"{station}_preset_Hob 1-0", f"{station}_start_custom"):
        registry.async_get_or_create("button", DOMAIN, unique_id, config_entry=kitchen, config_subentry_id=station)
    await hass.config_entries.async_reload(kitchen.entry_id)
    await hass.async_block_till_done()
    buttons = {e.unique_id for e in er.async_entries_for_config_entry(registry, kitchen.entry_id) if e.domain == "button"}
    assert not buttons & {f"{station}_preset_Hob 1-0", f"{station}_start_custom"}
    assert registry.async_get_entity_id("switch", DOMAIN, f"{station}_preset_Hob 1-0")
