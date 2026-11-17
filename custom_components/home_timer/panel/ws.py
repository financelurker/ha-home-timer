"""Websocket commands of the panel (admins only).

config/get        the areas, their stations, the free areas, the field specs
area/create       set up an area (through the config flow, source "panel")
area/save         an area's options and quick timers
area/delete       remove an area (its entry)
station/save      add a station or change one
station/delete    remove a station
runs/list         a power station's stored runs (without curves), the current run, the sensor's report rate and noise
runs/get          one run with its curve ("current": the run going on)
runs/delete       delete a stored run
runs/reread       read a stored run again from the recorder's history (its real start, its whole curve)
runs/preview      what saved settings and a draft make of each stored run (and of the current one)
runs/mark         mark when a run was done (or remove the mark); "current": done now
runs/skip         leave a run out of learning, or take it back in
runs/suggest      done rules that fit the marked runs (learn.py)
runs/suggest_status   how far the station's search is (and its last result)
runs/suggest_cancel   stop the station's search
settings/save     the panel's own settings (shown in the sidebar)

Saves send the ``version`` they started from; if the stored data changed meanwhile
(e.g. "save as preset" on a dashboard), the save is refused with ``changed``
and the panel loads the new state. Anything saved reloads the area once.
"""

from __future__ import annotations

from functools import partial
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from ..const import CONF_AREA_ID, DOMAIN
from ..learn import MIN_MARKED, SEARCHES, Cancelled, forget_searches, search
from ..runs import CURRENT, KEEP, RereadError, async_reread_run, compact, simulate, summary
from ..stations import CONF_PRESETS, SUBENTRY_STATION, PowerConfig
from .edit import check_options, check_presets, check_station, draft_power
from .settings import async_save_settings, get_settings
from .view import area_options, config_view, version

PREFIX = DOMAIN


@callback
def async_setup_ws(hass: HomeAssistant) -> None:
    for command in (ws_config_get, ws_area_create, ws_area_save, ws_area_delete, ws_station_save, ws_station_delete, ws_settings_save,
                    ws_runs_list, ws_runs_get, ws_runs_delete, ws_runs_reread, ws_runs_preview,
                    ws_runs_mark, ws_runs_skip, ws_runs_suggest, ws_runs_suggest_status, ws_runs_suggest_cancel):
        websocket_api.async_register_command(hass, command)
    # searches running in the executor stop with Home Assistant
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _event: forget_searches())


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
    registry = er.async_get(hass)
    title, data, errors = check_station(
        msg["station"], others, lambda e: hass.states.get(e) is not None or registry.async_get(e) is not None
    )
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


# ------------------------------------------------------------------ runs of power stations

RUN_SCHEMA = {vol.Required("entry_id"): str, vol.Required("station_id"): str}


def _engine(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> Any:
    if (entry := _entry(hass, connection, msg)) is None:
        return None
    engine = getattr(entry, "runtime_data", None)
    if entry.state is not ConfigEntryState.LOADED or engine is None or not hasattr(engine, "runs"):
        connection.send_error(msg["id"], "not_loaded", "The area is not loaded")
        return None
    return engine


def _current(engine: Any, station_id: str, *, curve: bool) -> dict[str, Any] | None:
    """The run going on now, if the station's appliance runs."""
    watcher = engine.watcher(station_id)
    if watcher is None or not watcher.running or watcher.run_start is None:
        return None
    run = {
        "id": CURRENT,
        "start": watcher.run_start.isoformat(),
        "duration": round(watcher.elapsed(), 1),
        "rang": round(watcher.rang, 1) if watcher.rang is not None else None,
        "trigger": watcher.trigger,
        "source": "live",
        "running": True,
        "from": round(watcher.trace.start, 1),
        "marks": {"done": watcher.mark} if watcher.mark is not None else {},
    }
    if curve:
        run["t"], run["w"] = compact(watcher.trace.times, watcher.trace.watts)
    return run


def _sensor(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The sensor's usual report interval and noise, from the newest runs."""
    newest = runs[-10:]
    out: dict[str, Any] = {}
    for key in ("interval", "noise"):
        values = sorted(r[key] for r in newest if isinstance(r.get(key), (int, float)))
        out[key] = values[len(values) // 2] if values else None
    return out


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/list", **RUN_SCHEMA})
@callback
def ws_runs_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (engine := _engine(hass, connection, msg)) is None:
        return
    runs = engine.runs.list(msg["station_id"])
    connection.send_result(
        msg["id"],
        {
            "runs": [summary(r) for r in reversed(runs)],  # newest first
            "current": _current(engine, msg["station_id"], curve=False),
            "sensor": _sensor(runs),
            "imported": msg["station_id"] in engine.runs.imported,
            "keep": KEEP,
            "min_marked": MIN_MARKED,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/get", **RUN_SCHEMA, vol.Required("run_id"): str})
@callback
def ws_runs_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (engine := _engine(hass, connection, msg)) is None:
        return
    if msg["run_id"] == CURRENT:
        run = _current(engine, msg["station_id"], curve=True)
    else:
        run = engine.runs.get(msg["station_id"], msg["run_id"])
    if run is None:
        connection.send_error(msg["id"], "not_found", "Unknown run")
        return
    connection.send_result(msg["id"], run)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/delete", **RUN_SCHEMA, vol.Required("run_id"): str})
@callback
def ws_runs_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (engine := _engine(hass, connection, msg)) is None:
        return
    if not engine.runs.delete(msg["station_id"], msg["run_id"]):
        connection.send_error(msg["id"], "not_found", "Unknown run")
        return
    connection.send_result(msg["id"], {"deleted": True})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/reread", **RUN_SCHEMA, vol.Required("run_id"): str})
@websocket_api.async_response
async def ws_runs_reread(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A stored run read again from the history: ``{"run": summary}`` when it got more (an earlier
    start, its whole curve; pieces of the same run joined), ``{"run": None}`` when not."""
    if (engine := _engine(hass, connection, msg)) is None:
        return
    station = engine.stations.get(msg["station_id"])
    if station is None or station.power is None or engine.runs.get(msg["station_id"], msg["run_id"]) is None:
        connection.send_error(msg["id"], "not_found", "Unknown run")
        return
    try:  # (the user's click: pieces of the same run are joined)
        run = await async_reread_run(hass, engine.runs, msg["station_id"], msg["run_id"], station.power)
    except RereadError as err:
        connection.send_error(msg["id"], str(err), str(err))
        return
    connection.send_result(msg["id"], {"run": summary(run) if run else None})


def _preset_seconds(presets: list[Any], preset_id: str | None) -> float | None:
    preset = next((p for p in presets if isinstance(p, dict) and p.get("id") == preset_id), None)
    try:
        seconds = float(preset["duration"]) if preset else None
    except (KeyError, TypeError, ValueError):
        return None
    return seconds if seconds and 0 < seconds <= 86400 else None


# saved settings simulated on stored runs, per station: they only change with the settings
_SAVED: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{PREFIX}/runs/preview",
        **RUN_SCHEMA,
        vol.Required("power"): dict,
        vol.Optional("presets"): list,
        vol.Optional("current_only", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_runs_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The panel's live preview: each run as the saved settings and as the draft see it.

    The draft is the station's power settings as edited; a value that cannot be used
    (a field being typed, out of range) falls back to the saved one. ``presets``: the
    draft's presets (default: the saved ones). ``current_only``: only the run going on.
    """
    if (engine := _engine(hass, connection, msg)) is None:
        return
    station_id = msg["station_id"]
    subentry = engine.entry.subentries.get(station_id)
    station = engine.stations.get(station_id)
    if station is None or station.power is None or subentry is None:
        connection.send_error(msg["id"], "not_found", "Unknown power station")
        return
    saved = station.power
    saved_presets = [p.to_data() for p in station.presets]
    saved_preset = _preset_seconds(saved_presets, saved.preset)
    draft = PowerConfig.from_data(draft_power(msg["power"], dict(subentry.data.get("power") or {}))) or saved
    draft_preset = _preset_seconds(msg["presets"] if "presets" in msg else saved_presets, draft.preset)
    same_settings = draft == saved and draft_preset == saved_preset
    runs = [] if msg["current_only"] else list(engine.runs.list(station_id))
    current = _current(engine, station_id, curve=True)
    key = f"{engine.entry.entry_id}:{station_id}"
    sig = repr((saved, saved_preset))
    cache = _SAVED.get(key)
    cached = cache[1] if cache and cache[0] == sig else {}

    def _run() -> dict[str, Any]:
        def both(run: dict[str, Any], ended: bool) -> dict[str, Any]:
            if ended and run["id"] in cached:
                res_saved = cached[run["id"]]
            else:
                res_saved = simulate(run, saved, saved_preset, ended=ended)
                if ended:
                    cached[run["id"]] = res_saved
            res_draft = res_saved if same_settings else simulate(run, draft, draft_preset, ended=ended)
            return {"id": run["id"], "saved": res_saved, "draft": res_draft}

        return {
            "runs": [both(r, True) for r in runs],
            "current": both(current, False) if current else None,
        }

    result = await hass.async_add_executor_job(_run)
    _SAVED[key] = (sig, cached)
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{PREFIX}/runs/mark",
        **RUN_SCHEMA,
        vol.Required("run_id"): str,
        vol.Optional("done"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0, max=86400 * 2))),
    }
)
@callback
def ws_runs_mark(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Mark when a run was done (``done`` seconds on its curve; None removes it).

    ``run_id`` "current": the run going on – done now (or at ``done``); the mark is
    kept with the run when it is stored.
    """
    if (engine := _engine(hass, connection, msg)) is None:
        return
    if msg["run_id"] == CURRENT:
        watcher = engine.watcher(msg["station_id"])
        try:
            done = watcher.mark_done(msg.get("done")) if watcher is not None else None
        except ValueError:
            done = None
        if done is None:
            connection.send_error(msg["id"], "not_running", "The appliance is not running")
            return
        connection.send_result(msg["id"], {"done": done})
        return
    run = engine.runs.set_mark(msg["station_id"], msg["run_id"], msg.get("done"))
    if run is None:
        connection.send_error(msg["id"], "not_found", "Unknown run")
        return
    connection.send_result(msg["id"], {"marks": run["marks"]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/skip", **RUN_SCHEMA, vol.Required("run_id"): str, vol.Required("skip"): bool})
@callback
def ws_runs_skip(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (engine := _engine(hass, connection, msg)) is None:
        return
    run = engine.runs.set_skip(msg["station_id"], msg["run_id"], msg["skip"])
    if run is None:
        connection.send_error(msg["id"], "not_found", "Unknown run")
        return
    connection.send_result(msg["id"], {"skip": run["skip"]})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{PREFIX}/runs/suggest", **RUN_SCHEMA, vol.Optional("power", default=dict): dict, vol.Optional("sig"): str}
)
@websocket_api.async_response
async def ws_runs_suggest(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Done rules that fit the marked runs, with the draft's "on and off" and tolerance.

    One search per station at a time; ``runs/suggest_status`` tells how far it is (``sig``,
    the panel's note of what it searched with, comes back there), ``runs/suggest_cancel``
    stops it (this command then answers ``cancelled``)."""
    if (engine := _engine(hass, connection, msg)) is None:
        return
    subentry = engine.entry.subentries.get(msg["station_id"])
    station = engine.stations.get(msg["station_id"])
    if station is None or station.power is None or subentry is None:
        connection.send_error(msg["id"], "not_found", "Unknown power station")
        return
    config = PowerConfig.from_data(draft_power(msg["power"], dict(subentry.data.get("power") or {}))) or station.power
    runs = list(engine.runs.list(msg["station_id"]))
    key = f"{engine.entry.entry_id}:{msg['station_id']}"
    if SEARCHES.get(key, {}).get("busy"):
        connection.send_error(msg["id"], "busy", "A search for this station is running")
        return
    state: dict[str, Any] = {"busy": True, "progress": 0.0, "stage": "rules", "cancel": False, "sig": msg.get("sig")}
    SEARCHES[key] = state

    def report(share: float, stage: str) -> bool:  # in the executor: plain writes and a read
        state["progress"], state["stage"] = round(share, 3), stage
        return not state["cancel"]

    try:
        result = await hass.async_add_executor_job(partial(search, runs, config, config.tolerance, report=report))
    except Cancelled:
        state.update(busy=False, cancelled=True)
        connection.send_error(msg["id"], "cancelled", "The search was cancelled")
        return
    except Exception:
        state.update(busy=False, failed=True)
        raise
    state.update(busy=False, progress=1.0, result=result)
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/suggest_status", **RUN_SCHEMA})
@callback
def ws_runs_suggest_status(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """How far the station's search is; when it is over, its result (or that it was cancelled)."""
    state = SEARCHES.get(f"{msg['entry_id']}:{msg['station_id']}")
    if state is None:
        connection.send_result(msg["id"], {"busy": False})
        return
    connection.send_result(msg["id"], {k: v for k, v in state.items() if k != "cancel"})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{PREFIX}/runs/suggest_cancel", **RUN_SCHEMA})
@callback
def ws_runs_suggest_cancel(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Stop the station's search (at its next step)."""
    state = SEARCHES.get(f"{msg['entry_id']}:{msg['station_id']}")
    running = bool(state and state.get("busy"))
    if running:
        state["cancel"] = True
    connection.send_result(msg["id"], {"cancelled": running})

