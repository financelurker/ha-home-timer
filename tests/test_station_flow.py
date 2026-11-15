"""Adding and configuring a station: name, icon, presets in a menu."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def _menu(hass, flow_id, choice):
    return await hass.config_entries.subentries.async_configure(flow_id, {"next_step_id": choice})


async def test_add_station_with_presets(hass: HomeAssistant, kitchen) -> None:
    result = await hass.config_entries.subentries.async_init((kitchen.entry_id, "station"), context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": " Hob 1 ", "icon": "mdi:stove"})
    assert result["type"] is FlowResultType.MENU
    assert "edit_preset" not in result["menu_options"]

    result = await _menu(hass, result["flow_id"], "add_preset")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Rice", "duration": {"minutes": 10}, "note": "Level 6"}
    )
    assert result["type"] is FlowResultType.MENU
    result = await _menu(hass, result["flow_id"], "add_preset")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "rice", "duration": {"minutes": 9}})
    assert result["errors"] == {"name": "preset_exists"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "Pasta", "duration": {"seconds": 0}})
    assert result["errors"] == {"duration": "invalid_duration"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "Pasta", "duration": {"minutes": 9}})
    result = await _menu(hass, result["flow_id"], "done")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    subentry = next(iter(kitchen.subentries.values()))
    assert subentry.title == "Hob 1"
    assert subentry.data["icon"] == "mdi:stove"
    assert [(p["name"], p["duration"], p.get("note")) for p in subentry.data["presets"]] == [
        ("Rice", 600, "Level 6"), ("Pasta", 540, None),
    ]
    state = hass.states.get("sensor.kitchen_hob_1")
    assert state is not None
    assert state.attributes["icon"] == "mdi:stove"
    assert hass.states.get("button.kitchen_hob_1_start_pasta") is not None


async def test_reconfigure_station(hass: HomeAssistant, kitchen) -> None:
    from .conftest import add_station

    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600), ("Pasta", 540)])
    await add_station(hass, kitchen, "Oven")
    result = await hass.config_entries.subentries.async_init(
        (kitchen.entry_id, "station"), context={"source": SOURCE_RECONFIGURE, "subentry_id": hob}
    )
    assert result["type"] is FlowResultType.MENU
    result = await _menu(hass, result["flow_id"], "settings")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "oven"})
    assert result["errors"] == {"name": "station_exists"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"name": "Front hob"})
    assert result["type"] is FlowResultType.MENU

    result = await _menu(hass, result["flow_id"], "edit_preset")
    rice_id = kitchen.subentries[hob].data["presets"][0]["id"]
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": rice_id})
    assert result["step_id"] == "preset"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Basmati", "duration": {"minutes": 12}, "delete": False}
    )
    result = await _menu(hass, result["flow_id"], "edit_preset")
    pasta_id = kitchen.subentries[hob].data["presets"][1]["id"]
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"preset": pasta_id})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Pasta", "duration": {"minutes": 9}, "delete": True}
    )
    result = await _menu(hass, result["flow_id"], "done")
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()

    subentry = kitchen.subentries[hob]
    assert subentry.title == "Front hob"
    assert [(p["id"], p["name"], p["duration"]) for p in subentry.data["presets"]] == [(rice_id, "Basmati", 720)]
    assert hass.states.get("sensor.kitchen_hob_1") is not None  # entity id stays, name follows
    assert hass.states.get("button.kitchen_hob_1_start_pasta") is None
