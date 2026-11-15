"""Onboard a fresh instance and set up Home Timer as a user would: areas, stations,
presets, quick timers – through the config flow and the panel's websocket API."""

import asyncio

import aiohttp

from client import Instance, onboard

KITCHEN_STATIONS = [
    {"name": "Hob 1", "icon": "mdi:stove", "mode": "manual", "presets": [
        {"name": "Rice", "duration": 600, "note": "lid on"},
        {"name": "Pasta", "duration": 540, "note": "salt the water"},
        {"name": "Eggs, soft", "duration": 360}]},
    {"name": "Hob 2", "icon": "mdi:stove", "mode": "manual", "presets": [
        {"name": "Sauce", "duration": 900}, {"name": "Potatoes", "duration": 1200}]},
    {"name": "Oven", "icon": "mdi:stove", "mode": "manual", "presets": [
        {"name": "Pizza", "duration": 720}, {"name": "Bread", "duration": 2100, "note": "steam for the first 10 min"}]},
    {"name": "Kettle", "icon": "mdi:kettle", "mode": "power",
     "power": {"sensor": "sensor.kettle_plug_power", "on_above": 10, "off_after": 30, "kind": "until_off", "done": {"type": "off"}}},
    {"name": "Moka pot", "icon": "mdi:coffee-maker-outline", "mode": "power",
     "power": {"sensor": "sensor.moka_pot_plug_power", "on_above": 10, "off_after": 30, "kind": "until_off",
               "done": {"type": "drop", "percent": 10, "window": 60, "ignore": 120}}},
    {"name": "Rice cooker", "icon": "mdi:rice", "mode": "power", "presets": [{"id": "rice", "name": "Rice", "duration": 1200}],
     "power": {"sensor": "sensor.rice_cooker_plug_power", "on_above": 10, "off_after": 60, "kind": "start_preset", "preset": "rice", "cancel_on_off": True}},
]


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        token = await onboard(s)
        ha = Instance(s, token)
        await ha.connect()
        areas = {a["name"]: a for a in await ha.call({"type": "config/area_registry/list"})}
        for name, icon in (("Kitchen", "mdi:silverware-fork-knife"), ("Bathroom", "mdi:shower"), ("Laundry", "mdi:washing-machine")):
            if name in areas:
                await ha.call({"type": "config/area_registry/update", "area_id": areas[name]["area_id"], "icon": icon})
            else:
                areas[name] = await ha.call({"type": "config/area_registry/create", "name": name, "icon": icon})
        kitchen = areas["Kitchen"]["area_id"]
        for entity in ("sensor.kettle_plug_power", "sensor.moka_pot_plug_power", "sensor.rice_cooker_plug_power"):
            await ha.call({"type": "config/entity_registry/update", "entity_id": entity, "area_id": kitchen})

        # the first area as "Add integration" does it
        flow = await ha.rest("POST", "/api/config/config_entries/flow", {"handler": "home_timer"})
        result = await ha.rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"area_id": kitchen})
        entry_id = result["result"]["entry_id"]
        # the second in the panel
        bathroom = (await ha.call({"type": "home_timer/area/create", "area_id": areas["Bathroom"]["area_id"]}))["entry_id"]
        await asyncio.sleep(2)

        for station in KITCHEN_STATIONS:
            res = await ha.call({"type": "home_timer/station/save", "entry_id": entry_id, "station": station})
            assert res.get("saved"), res
            await asyncio.sleep(1.5)
        res = await ha.call({"type": "home_timer/station/save", "entry_id": bathroom,
                             "station": {"name": "Bathtub", "icon": "mdi:bathtub-outline", "mode": "manual",
                                         "presets": [{"name": "Fill", "duration": 780, "note": "38 °C"}]}})
        assert res.get("saved"), res
        await asyncio.sleep(1.5)
        config = await ha.call({"type": "home_timer/config/get"})
        area = next(a for a in config["areas"] if a["entry_id"] == entry_id)
        res = await ha.call({"type": "home_timer/area/save", "entry_id": entry_id, "version": area["version"], "options": area["options"],
                             "quick_timers": [{"name": "Tea", "duration": 240}, {"name": "Eggs", "duration": 420}, {"name": "Let dough rest", "duration": 2700}]})
        assert res.get("saved"), res
        await asyncio.sleep(2)
        print("set up:", entry_id, bathroom)
        await ha.close()


asyncio.run(main())
