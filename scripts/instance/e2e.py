"""Checks of Home Timer on the running local instance (after setup_demo.py).

Exercises what pytest cannot: the panel as Home Assistant registers and serves it,
the real config flow and websocket API, entities, events, the to-do list and a
power station driven by a sensor. Exit code 1 if anything fails.

It uses its own plug (input_number.e2e_watts) and puts back what it changes – its
test station, its test presets and Hob 1's custom timer – also when a check fails.

    python e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import time

import aiohttp

from client import BASE, Instance, token

FAILED: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" – {detail}"))
    if not ok:
        FAILED.append(name)


async def wait_for(fn, timeout: float = 30, step: float = 0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = await fn()
        if value:
            return value
        await asyncio.sleep(step)
    return None


async def next_event(queue: asyncio.Queue, event_type: str, timeout: float = 30, **match) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            event = await asyncio.wait_for(queue.get(), end - time.monotonic())
        except TimeoutError:
            return None
        if event["event_type"] == event_type and all(event["data"].get(k) == v for k, v in match.items()):
            return event
    return None


async def main() -> int:
    async with aiohttp.ClientSession() as s:
        ha = Instance(s, token())
        await ha.connect()
        ctx: dict = {}
        try:
            await _remove_leftovers(ha)
            await checks(ha, s, ctx)
        finally:
            await cleanup(ha, ctx)
            await ha.close()
    print(f"\n{'all passed' if not FAILED else f'{len(FAILED)} failed'}")
    return 1 if FAILED else 0


async def checks(ha: Instance, s: aiohttp.ClientSession, ctx: dict) -> None:
    # ------------------------------------------------------------ panel
    panels = await ha.call({"type": "get_panels"})
    panel = panels.get("home-timer")
    check("panel registered", panel is not None, list(panels))
    if panel:
        check("panel: cog of an area opens it", panel.get("config_panel_domain") == "home_timer", panel)
        check("panel: admins only", panel.get("require_admin") is True, panel)
        check("panel: hidden from the sidebar", panel.get("show_in_sidebar") is False, panel)
        module = panel["config"]["_panel_custom"]["module_url"]
        async with s.get(f"{BASE}{module}") as r:
            check("panel: module served", r.status == 200 and "home-timer-panel" in await r.text(), r.status)
        async with s.get(f"{BASE}{module.rsplit('/', 1)[0]}/i18n/de.json") as r:
            check("panel: texts served", r.status == 200, r.status)
    await ha.call({"type": "home_timer/settings/save", "sidebar": True})
    panel = (await ha.call({"type": "get_panels"}))["home-timer"]
    check("panel: setting shows it in the sidebar", panel.get("show_in_sidebar") is True, panel)
    await ha.call({"type": "home_timer/settings/save", "sidebar": False})

    config = await ha.call({"type": "home_timer/config/get"})
    kitchen = next((a for a in config["areas"] if a["name"] == "Kitchen"), None)
    check("config: kitchen with stations", kitchen is not None and len(kitchen["stations"]) >= 6, config["areas"])
    if kitchen is None:
        return
    entry_id = ctx["entry_id"] = kitchen["entry_id"]
    entries = await ha.rest("GET", "/api/config/config_entries/entry?domain=home_timer")
    entry = next(e for e in entries if e["entry_id"] == entry_id)
    check("entry: no options flow (the cog opens the panel)", entry["supports_options"] is False, entry)
    check("entry: no station flow", entry.get("supported_subentry_types") == {}, entry.get("supported_subentry_types"))

    devices = await ha.call({"type": "config/device_registry/list"})
    hob = next(st for st in kitchen["stations"] if st["name"] == "Hob 1")
    hob_device = next((d for d in devices if d["id"] == hob["device_id"]), None)
    check("device: link to the station in the panel",
          hob_device and hob_device["configuration_url"] == f"homeassistant://home-timer/station/{entry_id}/{hob['id']}", hob_device)

    # ------------------------------------------------------------ a timer, its events and the to-do list
    events = await ha.subscribe({"type": "subscribe_events"})
    await ha.service("button", "press", {"entity_id": "button.kitchen_hob_1_start_rice"})
    started = await next_event(events, "home_timer_started", station_name="Hob 1")
    check("preset button starts a timer", started is not None and started["data"]["name"] == "Rice", started)
    state = await ha.state(hob["entities"]["timer"])
    check("station sensor active", state and state["state"] == "active", state)
    await ha.service("home_timer", "start", {"area_id": kitchen["area_id"], "name": "E2E", "duration": "00:00:03"})
    finished = await next_event(events, "home_timer_finished", name="E2E", timeout=15)
    check("timer finishes and fires its event", finished is not None, finished)
    todo = await wait_for(lambda: _todo_item(ha, "E2E"), 10)
    check("ringing timer in the to-do list", todo is not None, todo)
    if todo:
        await ha.service("todo", "update_item", {"entity_id": "todo.home_timer_ringing_timers", "item": todo["uid"], "status": "completed"})
        ack = await next_event(events, "home_timer_acknowledged", name="E2E", timeout=10)
        check("checking it off acknowledges it", ack is not None and ack["data"]["by"] == "todo", ack)
    await ha.service("button", "press", {"entity_id": "button.kitchen_hob_1_stop_acknowledge"})

    # ------------------------------------------------------------ the panel saves; conflicts with "save as preset"
    res = await ha.call({"type": "home_timer/station/save", "entry_id": entry_id, "station": {
        "name": "E2E plug", "mode": "power", "power": {"sensor": "sensor.e2e_plug_power", "on_above": 10, "off_after": 2, "kind": "until_off"}}})
    check("panel adds a power station", res.get("saved") is True, res)
    ctx["station_id"] = res.get("station_id")
    await asyncio.sleep(3)
    bad = await ha.call({"type": "home_timer/station/save", "entry_id": entry_id, "station": {"name": "Hob 1", "mode": "power", "power": {}}})
    check("panel refuses invalid stations", bad.get("errors", {}).get("name") == "station_exists", bad)

    config = await ha.call({"type": "home_timer/config/get"})
    hob = next(st for st in next(a for a in config["areas"] if a["entry_id"] == entry_id)["stations"] if st["name"] == "Hob 1")
    ctx["custom"] = (await ha.state("number.kitchen_hob_1_custom_timer") or {}).get("state")
    await ha.service("number", "set_value", {"entity_id": "number.kitchen_hob_1_custom_timer", "value": 7})
    await ha.service("text", "set_value", {"entity_id": "text.kitchen_hob_1_save_as_preset", "value": f"E2E preset {time.time():.0f}"})
    await asyncio.sleep(3)
    stale = await ha.call({"type": "home_timer/station/save", "entry_id": entry_id, "station_id": hob["id"], "version": hob["version"],
                           "station": {"name": "Hob 1", "mode": "manual", "presets": hob["presets"]}})
    check("a stale panel save does not overwrite 'save as preset'", stale == {"changed": True}, stale)

    # ------------------------------------------------------------ a power station run
    timer_entity = "sensor.kitchen_e2e_plug"
    await ha.service("input_number", "set_value", {"entity_id": "input_number.e2e_watts", "value": 2000})
    running = await wait_for(lambda: _state_is(ha, "binary_sensor.kitchen_e2e_plug_running", "on"), 15)
    check("power: appliance running", running, await ha.state("binary_sensor.kitchen_e2e_plug_running"))
    check("power: open timer runs", (await ha.state(timer_entity) or {}).get("state") == "active", await ha.state(timer_entity))
    await asyncio.sleep(12)  # longer than a blip
    await ha.service("input_number", "set_value", {"entity_id": "input_number.e2e_watts", "value": 0})
    done = await next_event(events, "home_timer_finished", station_name="E2E plug", timeout=20)
    check("power: switching off finishes it (trigger off)", done is not None and done["data"].get("trigger") == "off", done)
    ringing = await wait_for(lambda: _state_is(ha, "binary_sensor.kitchen_e2e_plug_ringing", "on"), 10)
    ring_state = await ha.state("binary_sensor.kitchen_e2e_plug_ringing") or {}
    check("ringing sensor on, device class problem (shown red)", ringing and ring_state.get("attributes", {}).get("device_class") == "problem", ring_state)
    runs = await ha.state("sensor.kitchen_e2e_plug_runs")
    check("power: run counted", runs and runs["state"] == "1", runs)
    stored = await ha.call({"type": "home_timer/runs/list", "entry_id": entry_id, "station_id": ctx["station_id"]})
    check("runs: the run is stored", len(stored["runs"]) == 1 and stored["runs"][0]["trigger"] == "off", stored)
    if stored["runs"]:
        run = await ha.call({"type": "home_timer/runs/get", "entry_id": entry_id, "station_id": ctx["station_id"], "run_id": stored["runs"][0]["id"]})
        check("runs: with its curve", run["w"][:1] == [2000] and 10 <= run["duration"] <= 16, {k: run[k] for k in ("duration", "t", "w")})
    moka = next(st for st in kitchen["stations"] if st["name"] == "Moka pot")
    seeded = await ha.call({"type": "home_timer/runs/list", "entry_id": entry_id, "station_id": moka["id"]})
    check("runs: the moka pot's week (seed_runs.py)", len(seeded["runs"]) >= 8 and seeded["sensor"]["interval"], seeded["sensor"])


async def _remove_leftovers(ha: Instance) -> None:
    """A test station left by an aborted run."""
    for area in (await ha.call({"type": "home_timer/config/get"}))["areas"]:
        for station in area["stations"]:
            if station["name"] == "E2E plug":
                await ha.call({"type": "home_timer/station/delete", "entry_id": area["entry_id"], "station_id": station["id"]})
                await asyncio.sleep(2)


async def cleanup(ha: Instance, ctx: dict) -> None:
    """Put the demo back as it was: the test station, the test preset, the custom timer."""
    await ha.service("input_number", "set_value", {"entity_id": "input_number.e2e_watts", "value": 0})
    entry_id = ctx.get("entry_id")
    if not entry_id:
        return
    if ctx.get("station_id"):
        await ha.call({"type": "home_timer/station/delete", "entry_id": entry_id, "station_id": ctx["station_id"]}, ok=False)
        await asyncio.sleep(2)
    config = await ha.call({"type": "home_timer/config/get"})
    area = next(a for a in config["areas"] if a["entry_id"] == entry_id)
    hob = next((st for st in area["stations"] if st["name"] == "Hob 1"), None)
    if hob and any(p["name"].startswith("E2E preset") for p in hob["presets"]):
        res = await ha.call({"type": "home_timer/station/save", "entry_id": entry_id, "station_id": hob["id"], "version": hob["version"],
                             "station": {"name": "Hob 1", "icon": hob["icon"], "mode": "manual", "presets": [p for p in hob["presets"] if not p["name"].startswith("E2E preset")]}})
        check("clean up: test presets removed", res.get("saved") is True, res)
        await asyncio.sleep(2)
    if ctx.get("custom") not in (None, "unknown", "unavailable"):
        await ha.service("number", "set_value", {"entity_id": "number.kitchen_hob_1_custom_timer", "value": float(ctx["custom"])})
    config = await ha.call({"type": "home_timer/config/get"})
    check("clean up: test station removed", not any(st["name"] == "E2E plug" for a in config["areas"] for st in a["stations"]))


async def _todo_item(ha: Instance, name: str):
    res = await ha.call({"type": "todo/item/list", "entity_id": "todo.home_timer_ringing_timers"})
    return next((i for i in res["items"] if i["summary"].startswith(name)), None)


async def _state_is(ha: Instance, entity_id: str, value: str) -> bool:
    return (await ha.state(entity_id) or {}).get("state") == value


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
