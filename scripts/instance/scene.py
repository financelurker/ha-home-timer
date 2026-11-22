"""Set the scene for the screenshots: timers running, one ringing, the moka pot brewing.

    python scene.py
"""

import asyncio

import aiohttp

from client import Instance, token

def tile(entity: str, name: str, columns: int = 6) -> dict:
    return {"type": "tile", "entity": entity, "name": name, "grid_options": {"columns": columns}}


DASHBOARD = {
    "title": "Timers",
    "views": [{
        "title": "Kitchen", "path": "kitchen", "type": "sections", "max_columns": 3,
        "sections": [
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Hob 1", "icon": "mdi:stove"},
                tile("sensor.kitchen_hob_1", "Timer"),
                {"type": "tile", "entity": "sensor.kitchen_hob_1_timer_end", "name": "Ends", "grid_options": {"columns": 6}},
                {"type": "tile", "entity": "button.kitchen_hob_1_start_rice", "name": "Rice", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "button.kitchen_hob_1_start_pasta", "name": "Pasta", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "button.kitchen_hob_1_start_eggs_soft", "name": "Eggs", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "button.kitchen_hob_1_pause_resume", "name": "Pause", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "button.kitchen_hob_1_1_minute", "name": "+1 min", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "button.kitchen_hob_1_stop", "name": "Stop", "vertical": True, "hide_state": True, "grid_options": {"columns": 4}},
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Appliances", "icon": "mdi:power-plug-outline"},
                tile("sensor.kitchen_moka_pot", "Moka pot"),
                tile("sensor.moka_pot_plug_power", "Power"),
                tile("binary_sensor.kitchen_kettle_ringing", "Kettle"),
                tile("button.kitchen_kettle_stop", "Got it"),
                tile("binary_sensor.kitchen_rice_cooker_ringing", "Rice cooker"),
                tile("sensor.kitchen_hob_2", "Hob 2"),
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Quick timers", "icon": "mdi:timer-outline"},
                {"type": "tile", "entity": "switch.kitchen_home_timer_tea", "name": "Tea", "vertical": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "switch.kitchen_home_timer_eggs", "name": "Eggs", "vertical": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "switch.kitchen_home_timer_let_dough_rest", "name": "Dough", "vertical": True, "grid_options": {"columns": 4}},
                {"type": "tile", "entity": "switch.kitchen_home_timer_custom_timer", "name": "Custom timer", "grid_options": {"columns": 6}},
                {"type": "tile", "entity": "number.kitchen_home_timer_custom_timer", "name": "Minutes", "features": [{"type": "numeric-input", "style": "buttons"}], "grid_options": {"columns": 6}},
                {"type": "todo-list", "entity": "todo.kitchen_timers", "title": "Kitchen timers", "hide_create": True, "hide_completed": True},
            ]},
        ],
    }],
}


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        ha = Instance(s, token())
        await ha.connect()
        dashboards = await ha.call({"type": "lovelace/dashboards/list"})
        if not any(d["url_path"] == "dashboard-timers" for d in dashboards):
            await ha.call({"type": "lovelace/dashboards/create", "url_path": "dashboard-timers", "title": "Timers", "icon": "mdi:timer-outline", "mode": "storage", "show_in_sidebar": True})
        await ha.call({"type": "lovelace/config/save", "url_path": "dashboard-timers", "config": DASHBOARD})

        svc = ha.service
        # a clean start: appliances off, every timer gone
        for entity in ("input_number.kettle_watts", "input_number.moka_watts", "input_number.rice_watts"):
            await svc("input_number", "set_value", {"entity_id": entity, "value": 0})
        await asyncio.sleep(70)  # runs end ("stopped after" up to 60 s)
        for area_id in ("kitchen", "bathroom"):
            for timer in (await ha.state(f"sensor.{area_id}_home_timer_timers") or {}).get("attributes", {}).get("timers", []):
                await svc("home_timer", "cancel", {"timer_id": timer["id"]})
        # the kettle: boiled and switched itself off → ringing
        await svc("input_number", "set_value", {"entity_id": "input_number.kettle_watts", "value": 2150})
        await svc("input_number", "set_value", {"entity_id": "input_number.moka_watts", "value": 265})
        await asyncio.sleep(2)
        await svc("button", "press", {"entity_id": "button.kitchen_hob_1_start_rice"})
        await svc("button", "press", {"entity_id": "button.kitchen_oven_start_bread"})
        await svc("switch", "turn_on", {"entity_id": "switch.kitchen_home_timer_let_dough_rest"})
        await svc("button", "press", {"entity_id": "button.bathroom_bathtub_start_fill"})
        await svc("button", "press", {"entity_id": "button.kitchen_hob_2_start_sauce"})
        await asyncio.sleep(1)
        await svc("button", "press", {"entity_id": "button.kitchen_hob_2_pause_resume"})
        for watts in (230, 222, 218, 214, 212, 209):
            await asyncio.sleep(10)
            await svc("input_number", "set_value", {"entity_id": "input_number.moka_watts", "value": watts})
        await svc("input_number", "set_value", {"entity_id": "input_number.kettle_watts", "value": 0.3})
        await asyncio.sleep(40)  # "stopped after" 30 s → the kettle rings
        await svc("input_number", "set_value", {"entity_id": "input_number.moka_watts", "value": 205})
        await svc("input_number", "set_value", {"entity_id": "input_number.rice_watts", "value": 0.4})
        print("scene set")
        await ha.close()


asyncio.run(main())
