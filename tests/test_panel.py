"""The panel's websocket commands, its checks, its registration and the device links."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.home_timer.const import DOMAIN
from custom_components.home_timer.panel.edit import check_options, check_presets, check_station

from .conftest import add_station, setup_area

SENSOR = "sensor.plug_power"


async def call(client, type_: str, **data):
    await client.send_json_auto_id({"type": f"{DOMAIN}/{type_}", **data})
    msg = await client.receive_json()
    return msg


async def config(client) -> dict:
    msg = await call(client, "config/get")
    assert msg["success"], msg
    return msg["result"]


# ------------------------------------------------------------------ reading


async def test_config_lists_areas_stations_and_free_areas(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    ar.async_get(hass).async_get_or_create("Bathroom")
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600, "Lid on")], icon="mdi:stove")
    client = await hass_ws_client(hass)
    result = await config(client)
    assert [a["name"] for a in result["free_areas"]] == ["Bathroom"]
    (area,) = result["areas"]
    assert area["entry_id"] == kitchen.entry_id and area["loaded"]
    assert area["options"]["ring_until_acknowledged"] is True
    assert area["entities"] == {"timers": "sensor.kitchen_home_timer_timers", "ringing": "binary_sensor.kitchen_home_timer_ringing"}
    (station,) = area["stations"]
    assert station["id"] == hob and station["name"] == "Hob 1" and station["mode"] == "manual"
    assert station["presets"] == [{"id": "Hob 1-0", "name": "Rice", "duration": 600, "note": "Lid on"}]
    assert station["entities"] == {"timer": "sensor.kitchen_hob_1", "running": None}
    assert station["power"]["on_above"] == 10.0  # defaults filled in
    assert result["settings"] == {"sidebar": False}
    assert result["specs"]["rules"]["drop"]["fields"][0]["key"] == "percent"


async def test_admins_only(hass: HomeAssistant, kitchen, hass_ws_client, hass_read_only_access_token) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    msg = await call(client, "config/get")
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"


# ------------------------------------------------------------------ areas


async def test_area_create_and_delete(hass: HomeAssistant, hass_ws_client) -> None:
    """Without any area yet (the integration loaded through its first area elsewhere)."""
    assert await async_setup_component(hass, DOMAIN, {})
    area = ar.async_get(hass).async_get_or_create("Laundry")
    client = await hass_ws_client(hass)
    msg = await call(client, "area/create", area_id=area.id)
    entry_id = msg["result"]["entry_id"]
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry_id).title == "Laundry"
    msg = await call(client, "area/create", area_id=area.id)
    assert msg["result"] == {"errors": {"area_id": "already_configured"}}
    msg = await call(client, "area/delete", entry_id=entry_id)
    assert msg["result"] == {"deleted": True}
    assert hass.config_entries.async_get_entry(entry_id) is None


async def test_area_save(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    area = (await config(client))["areas"][0]
    options = {**area["options"], "reminder_minutes": 5}
    quick = [{"id": "new-1", "name": "Tea", "duration": 240}]
    msg = await call(client, "area/save", entry_id=kitchen.entry_id, version=area["version"], options=options, quick_timers=quick)
    assert msg["result"] == {"saved": True}
    await hass.async_block_till_done()
    assert kitchen.options["reminder_minutes"] == 5
    assert kitchen.options["presets"] == [{"id": "new-1", "name": "Tea", "duration": 240}]
    assert hass.states.get("button.kitchen_home_timer_start_tea") is not None
    # the old version is stale now
    msg = await call(client, "area/save", entry_id=kitchen.entry_id, version=area["version"], options=options, quick_timers=[])
    assert msg["result"] == {"changed": True}


async def test_area_save_errors(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    area = (await config(client))["areas"][0]
    msg = await call(
        client, "area/save", entry_id=kitchen.entry_id, version=area["version"],
        options={**area["options"], "reminder_minutes": 999},
        quick_timers=[{"name": "Tea", "duration": 240}, {"name": "tea", "duration": 0}],
    )
    assert msg["result"]["errors"] == {
        "reminder_minutes": "out_of_range",
        "quick_timers.1.name": "preset_exists",
        "quick_timers.1.duration": "invalid_duration",
    }
    assert "presets" not in kitchen.options


async def test_unknown_area(hass: HomeAssistant, hass_ws_client) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)
    msg = await call(client, "area/delete", entry_id="nope")
    assert msg["error"]["code"] == "not_found"


# ------------------------------------------------------------------ stations


async def test_station_add_change_delete(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    station = {"name": "Hob 2", "icon": "mdi:stove", "mode": "manual",
               "presets": [{"id": "tmp-1", "name": "Pasta", "duration": 540, "note": "salt"}]}
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station=station)
    station_id = msg["result"]["station_id"]
    await hass.async_block_till_done()
    assert kitchen.subentries[station_id].title == "Hob 2"
    assert hass.states.get("button.kitchen_hob_2_start_pasta") is not None
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, station_id), kitchen.entry_id)
    assert device.configuration_url == f"homeassistant://home-timer/station/{kitchen.entry_id}/{station_id}"

    current = next(s for s in (await config(client))["areas"][0]["stations"] if s["id"] == station_id)
    changed = {**station, "name": "Hob 2 left", "presets": current["presets"] + [{"name": "Eggs", "duration": 360}]}
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station_id=station_id, version=current["version"], station=changed)
    assert msg["result"]["saved"]
    await hass.async_block_till_done()
    assert kitchen.subentries[station_id].title == "Hob 2 left"
    assert [p["name"] for p in kitchen.subentries[station_id].data["presets"]] == ["Pasta", "Eggs"]
    assert kitchen.subentries[station_id].data["presets"][0]["id"] == "tmp-1"  # ids are kept (entities stay)
    # a stale version is refused
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station_id=station_id, version=current["version"], station=changed)
    assert msg["result"] == {"changed": True}

    msg = await call(client, "station/delete", entry_id=kitchen.entry_id, station_id=station_id)
    assert msg["result"] == {"deleted": True}
    await hass.async_block_till_done()
    assert station_id not in kitchen.subentries


async def test_station_errors(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    await add_station(hass, kitchen, "Hob 1")
    client = await hass_ws_client(hass)
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station={"name": "hob 1", "mode": "power", "power": {"kind": "start_preset"}})
    assert msg["result"]["errors"] == {
        "name": "station_exists",
        "power.sensor": "sensor_required",
        "power.preset": "preset_required",
    }
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station={"name": "Kettle", "mode": "power", "power": {"sensor": SENSOR}})
    assert msg["result"]["errors"] == {"power.sensor": "unknown_sensor"}


async def test_power_station_from_the_panel(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    hass.states.async_set(SENSOR, "0")
    client = await hass_ws_client(hass)
    station = {"name": "Moka pot", "mode": "power", "presets": [],
               "power": {"sensor": SENSOR, "on_above": 10, "off_after": 30, "kind": "until_off",
                         "done": {"type": "drop", "percent": 12, "window": 60, "ignore": 120}}}
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station=station)
    station_id = msg["result"]["station_id"]
    await hass.async_block_till_done()
    data = kitchen.subentries[station_id].data
    assert data["power"]["done"] == {"type": "drop", "percent": 12, "window": 60, "ignore": 120}
    assert hass.states.get("binary_sensor.kitchen_moka_pot_running").state == "off"
    current = next(s for s in (await config(client))["areas"][0]["stations"] if s["id"] == station_id)
    assert current["entities"]["running"] == "binary_sensor.kitchen_moka_pot_running"
    # back to manual: the power settings are kept, not checked
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station_id=station_id, version=current["version"],
                     station={**station, "mode": "manual", "power": {**station["power"], "sensor": ""}})
    assert msg["result"]["saved"]


# ------------------------------------------------------------------ checks


def test_check_presets_keeps_order_and_ids() -> None:
    presets, errors = check_presets([{"id": "a", "name": " Rice ", "duration": 600}, {"id": "a", "name": "Pasta", "duration": 540.4}])
    assert not errors
    assert presets[0] == {"id": "a", "name": "Rice", "duration": 600}
    assert presets[1]["id"] != "a" and presets[1]["duration"] == 540


def test_check_options_rejects_wrong_types() -> None:
    _, errors = check_options({"ring_until_acknowledged": "yes", "reminder_minutes": True})
    assert errors == {"ring_until_acknowledged": "invalid", "reminder_minutes": "out_of_range"}


def test_check_station_rule_ranges() -> None:
    _, data, errors = check_station(
        {"name": "Moka", "mode": "power", "power": {"sensor": SENSOR, "done": {"type": "knee", "factor": 0.5}}}, [], lambda e: True
    )
    assert errors == {"power.done.factor": "out_of_range"}
    assert data["power"]["done"]["baseline"] == 180  # defaults filled in


# ------------------------------------------------------------------ registration and links


async def test_area_device_links_to_the_panel(hass: HomeAssistant, kitchen) -> None:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, kitchen.entry_id), kitchen.entry_id)
    assert device.configuration_url == f"homeassistant://home-timer/area/{kitchen.entry_id}"


async def test_panel_registered_hidden_and_follows_the_setting(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    from custom_components.home_timer.panel import DATA_MODULE, async_register_panel

    hass.data[DATA_MODULE] = "/home_timer_panel/0.0.0/home-timer-panel.js"
    with patch("homeassistant.components.frontend.async_register_built_in_panel") as register:
        async_register_panel(hass)
        kw = register.call_args.kwargs
        assert kw["frontend_url_path"] == "home-timer"
        assert kw["config_panel_domain"] == DOMAIN
        assert kw["require_admin"] is True
        assert kw["show_in_sidebar"] is False
        client = await hass_ws_client(hass)
        msg = await call(client, "settings/save", sidebar=True)
        assert msg["result"] == {"settings": {"sidebar": True}}
        kw = register.call_args.kwargs
        assert kw["show_in_sidebar"] is True and kw["update"] is True


async def test_setting_survives_a_restart(hass: HomeAssistant, hass_storage) -> None:
    hass_storage["home_timer.panel"] = {"version": 1, "key": "home_timer.panel", "data": {"sidebar": True}}
    await setup_area(hass, "Kitchen")
    from custom_components.home_timer.panel.settings import get_settings

    assert get_settings(hass) == {"sidebar": True}


@pytest.mark.parametrize("path", ["home-timer-panel.js", "i18n/en.json", "i18n/de.json", "vendor/lit.js"])
def test_frontend_files_exist(path: str) -> None:
    from custom_components.home_timer.panel import FRONTEND

    assert (FRONTEND / path).is_file()


def test_panel_texts_complete() -> None:
    """en and de have the same keys, and every key the panel uses exists."""
    import json
    import re

    from custom_components.home_timer.panel import FRONTEND

    en = json.loads((FRONTEND / "i18n/en.json").read_text(encoding="utf-8"))
    de = json.loads((FRONTEND / "i18n/de.json").read_text(encoding="utf-8"))
    assert sorted(set(en) ^ set(de)) == []
    assert all(isinstance(v, str) and v.strip() for v in [*en.values(), *de.values()])
    source = (FRONTEND / "home-timer-panel.js").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\("([a-z_.]+)"', source))
    assert sorted(used - set(en)) == []
    # keys built from values: every kind, rule, rule field, status and error
    from custom_components.home_timer.panel.edit import OPTION_RANGES  # noqa: F401
    from custom_components.home_timer.power import RULE_FIELDS
    from custom_components.home_timer.stations import KINDS

    built = {f"kind.{k}" for k in KINDS} | {f"kind.{k}_long" for k in KINDS}
    built |= {f"rule.{r}" for r in RULE_FIELDS} | {f"rule.{r}_hint" for r in RULE_FIELDS}
    built |= {f"rule_verb.{r}" for r in RULE_FIELDS} | {f"rule_sentence.{r}" for r in RULE_FIELDS}
    built |= {f"rule_field.{f[0]}" for fields in RULE_FIELDS.values() for f in fields}
    built |= {f"status.{s}" for s in ("idle", "active", "paused", "ringing")} | {"mode.manual", "mode.power", "mode.manual_hint", "mode.power_hint"}
    edit = (FRONTEND.parent / "panel" / "edit.py").read_text(encoding="utf-8") + (FRONTEND.parent / "panel" / "ws.py").read_text(encoding="utf-8")
    built |= {f"error.{code}" for code in re.findall(r'= "([a-z_]+)"$', edit, re.M) if code not in ("panel",)}
    built |= {"error.already_configured", "error.unknown_area"}
    built |= {f"outcome.{x}" for x in ("rule", "off", "never", "cancelled")} | {f"mark.{x}" for x in ("rule", "off", "never", "cancelled")}
    built |= {f"preview.n.{x}" for x in ("rings", "warns", "cancelled", "no_ring", "no_warning", "blip", "rule", "off", "never")}
    assert sorted(built - set(en)) == []
    # every value of a rule appears in its sentence, in both languages
    for lang in (en, de):
        for rule, fields in RULE_FIELDS.items():
            sentence = lang[f"rule_sentence.{rule}"]
            assert "{type}" in sentence, rule
            assert all("{" + f[0] + "}" in sentence for f in fields), (rule, sentence)


async def test_save_as_preset_meanwhile_is_not_overwritten(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    """A preset saved from a dashboard while the panel is open: the panel's older save is refused."""
    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    client = await hass_ws_client(hass)
    area = (await config(client))["areas"][0]
    station = area["stations"][0]
    engine = kitchen.runtime_data
    engine.set_custom_duration(hob, 240)
    engine.save_preset(hob, "Tea")
    engine.set_custom_duration(None, 300)
    engine.save_preset(None, "Eggs")
    await hass.async_block_till_done()
    msg = await call(client, "station/save", entry_id=kitchen.entry_id, station_id=hob, version=station["version"],
                     station={"name": "Hob 1", "mode": "manual", "presets": station["presets"]})
    assert msg["result"] == {"changed": True}
    msg = await call(client, "area/save", entry_id=kitchen.entry_id, version=area["version"], options=area["options"], quick_timers=[])
    assert msg["result"] == {"changed": True}
    assert [p["name"] for p in kitchen.subentries[hob].data["presets"]] == ["Rice", "Tea"]
    assert [p["name"] for p in kitchen.options["presets"]] == ["Eggs"]


async def test_registered_sensor_without_state_is_accepted(hass: HomeAssistant, kitchen, hass_ws_client) -> None:
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get_or_create("sensor", "shelly", "plug-1", suggested_object_id="plug_later_power")
    client = await hass_ws_client(hass)
    msg = await call(client, "station/save", entry_id=kitchen.entry_id,
                     station={"name": "Toaster", "mode": "power", "power": {"sensor": entry.entity_id}})
    assert msg["result"]["saved"]


def test_defaults_are_within_their_ranges() -> None:
    from custom_components.home_timer.panel.edit import POWER_RANGES, check_rule
    from custom_components.home_timer.power import RULE_TYPES
    from custom_components.home_timer.stations import POWER_DEFAULTS

    for key, (lo, hi) in POWER_RANGES.items():
        assert lo <= POWER_DEFAULTS[key] <= hi, key
    for rule_type in RULE_TYPES:
        assert check_rule({"type": rule_type})[1] == {}, rule_type


def test_empty_preset_id_gets_a_new_one() -> None:
    presets, _ = check_presets([{"id": " ", "name": "Rice", "duration": 600}])
    assert presets[0]["id"].strip()
