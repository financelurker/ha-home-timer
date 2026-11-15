"""Websocket commands of the panel (admins only).

config/get        the areas, their stations, the free areas, the field specs
area/create       set up an area (through the config flow, source "panel")
area/save         an area's options and quick timers
area/delete       remove an area (its entry)
station/save      add a station or change one
station/delete    remove a station
settings/save     the panel's own settings (shown in the sidebar)

Saves send the ``version`` they started from; if the stored data changed meanwhile
(e.g. "save as preset" on a dashboard), the save is refused with ``changed``
and the panel loads the new state. Anything saved reloads the area once.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback

from ..const import CONF_AREA_ID, DOMAIN
from ..stations import CONF_PRESETS, SUBENTRY_STATION
from .edit import check_options, check_presets, check_station
from .settings import async_save_settings, get_settings
from .view import area_options, config_view, version

PREFIX = DOMAIN


@callback
def async_setup_ws(hass: HomeAssistant) -> None:
    for command in (ws_config_get, ws_area_create, ws_area_save, ws_area_delete, ws_station_save, ws_station_delete, ws_settings_save):
        websocket_api.async_register_command(hass, command)


def _entry(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "Unknown area")
        return None
    return entry


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/config/get"})
@callback
def ws_config_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], config_view(hass, get_settings(hass)))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/area/create", vol.Required("area_id"): str})
@websocket_api.async_response
async def ws_area_create(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "panel"}, data={CONF_AREA_ID: msg["area_id"]})
    if result.get("type") != "create_entry":
        connection.send_result(msg["id"], {"errors": {"area_id": result.get("reason", "invalid")}})
        return
    connection.send_result(msg["id"], {"entry_id": result["result"].entry_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{PREFIX}/area/save",
        vol.Required("entry_id"): str,
        vol.Required("version"): str,
        vol.Required("options"): dict,
        vol.Required("quick_timers"): list,
    }
)
@callback
def ws_area_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (entry := _entry(hass, connection, msg)) is None:
        return
    current = {"options": area_options(entry), "quick": list(entry.options.get(CONF_PRESETS) or [])}
    if version(current) != msg["version"]:
        connection.send_result(msg["id"], {"changed": True})
        return
    options, errors = check_options(msg["options"])
    presets, preset_errors = check_presets(msg["quick_timers"], "quick_timers")
    errors.update(preset_errors)
    if errors:
        connection.send_result(msg["id"], {"errors": errors})
        return
    new = {**entry.options, **options, CONF_PRESETS: presets}
    if new != dict(entry.options):
        hass.config_entries.async_update_entry(entry, options=new)
    connection.send_result(msg["id"], {"saved": True})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/area/delete", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_area_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (entry := _entry(hass, connection, msg)) is None:
        return
    await hass.config_entries.async_remove(entry.entry_id)
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{PREFIX}/station/save",
        vol.Required("entry_id"): str,
        vol.Optional("station_id"): vol.Any(str, None),
        vol.Optional("version"): vol.Any(str, None),
        vol.Required("station"): dict,
    }
)
@callback
def ws_station_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (entry := _entry(hass, connection, msg)) is None:
        return
    station_id = msg.get("station_id")
    subentry = entry.subentries.get(station_id) if station_id else None
    if station_id and (subentry is None or subentry.subentry_type != SUBENTRY_STATION):
        connection.send_error(msg["id"], "not_found", "Unknown station")
        return
    if subentry is not None and version({"title": subentry.title, "data": dict(subentry.data)}) != msg.get("version"):
        connection.send_result(msg["id"], {"changed": True})
        return
    others = [s.title for s in entry.subentries.values() if s.subentry_type == SUBENTRY_STATION and s.subentry_id != station_id]
    title, data, errors = check_station(msg["station"], others, lambda e: hass.states.get(e) is not None)
    if errors:
        connection.send_result(msg["id"], {"errors": errors})
        return
    if subentry is None:
        new = ConfigSubentry(data=MappingProxyType(data), subentry_type=SUBENTRY_STATION, title=title, unique_id=None)
        hass.config_entries.async_add_subentry(entry, new)
        station_id = new.subentry_id
    elif title != subentry.title or data != dict(subentry.data):
        hass.config_entries.async_update_subentry(entry, subentry, title=title, data=data)
    connection.send_result(msg["id"], {"saved": True, "station_id": station_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{PREFIX}/station/delete", vol.Required("entry_id"): str, vol.Required("station_id"): str}
)
@callback
def ws_station_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (entry := _entry(hass, connection, msg)) is None:
        return
    subentry = entry.subentries.get(msg["station_id"])
    if subentry is None or subentry.subentry_type != SUBENTRY_STATION:
        connection.send_error(msg["id"], "not_found", "Unknown station")
        return
    hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/settings/save", vol.Required("sidebar"): bool})
@websocket_api.async_response
async def ws_settings_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    settings = await async_save_settings(hass, {"sidebar": msg["sidebar"]})
    connection.send_result(msg["id"], {"settings": settings})
