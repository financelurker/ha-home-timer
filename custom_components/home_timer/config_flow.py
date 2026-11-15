"""Setup: one config entry per area.

The area is the entry's unique id, so each area can be set up only once; the entry
is named after the area. Devices are not prefixed with the area's name: Home
Assistant puts the area into entity ids and shows it next to device names.
Stations are config subentries of the area ("Add station" on the entry): a name, an
icon and presets, edited in a menu. The options are a menu too: how the area's
timers behave when they run out, and the area's quick timers (presets without a
station).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector as sel
from homeassistant.util.ulid import ulid_now

from .const import (
    CONF_AREA_ID,
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_REPORT_MISSED,
    CONF_RING_UNTIL_ACK,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .power import (
    RULE_CYCLING,
    RULE_DEFAULTS,
    RULE_DROP,
    RULE_ENERGY,
    RULE_KNEE,
    RULE_OFF,
    RULE_PAUSE,
    RULE_TIME,
    RULE_TYPES,
    DoneRule,
)
from .stations import (
    CONF_ICON,
    CONF_MODE,
    CONF_POWER,
    CONF_PRESETS,
    KIND_START_PRESET,
    KIND_UNTIL_OFF,
    KIND_WARN,
    KINDS,
    MODE_MANUAL,
    MODE_POWER,
    POWER_DEFAULTS,
    SUBENTRY_STATION,
)

CONF_DURATION = "duration"
CONF_NOTE = "note"
CONF_PRESET = "preset"
CONF_DELETE = "delete"


class HomeTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HomeTimerOptionsFlow:
        return HomeTimerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_STATION: StationFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            area_id = user_input[CONF_AREA_ID]
            area = ar.async_get(self.hass).async_get_area(area_id)
            if area is None:
                errors[CONF_AREA_ID] = "unknown_area"
            else:
                await self.async_set_unique_id(area_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=area.name, data={CONF_AREA_ID: area_id})

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_ID): sel.AreaSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )


def _minutes(maximum: int) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(min=0, max=maximum, step=1, mode=sel.NumberSelectorMode.BOX, unit_of_measurement="min")
    )


def _duration_seconds(value: dict[str, Any]) -> float:
    return cv.time_period_dict(value).total_seconds()


def _duration_dict(seconds: float) -> dict[str, int]:
    seconds = round(seconds)
    return {"hours": seconds // 3600, "minutes": seconds % 3600 // 60, "seconds": seconds % 60}


def _format(seconds: float) -> str:
    seconds = round(seconds)
    hours, minutes, secs = seconds // 3600, seconds % 3600 // 60, seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d} h"
    return f"{minutes}:{secs:02d} min"


class PresetSteps:
    """Steps to add, change and delete presets in ``self._presets``, back to ``menu``.

    Shared by the station flow (a station's presets) and the options flow (the
    area's quick timers). ``self._label`` names what the presets belong to.
    """

    _presets: list[dict[str, Any]]
    _editing: str | None
    _label: str

    def _listing(self) -> str:
        return "\n".join(f"- {p['name']} · {_format(p['duration'])}" for p in self._presets) or "–"

    async def async_step_add_preset(self, user_input: dict[str, Any] | None = None) -> Any:
        return await self._preset_form("add_preset", user_input, None)

    async def async_step_edit_preset(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            self._editing = user_input[CONF_PRESET]
            return await self.async_step_preset()
        schema = vol.Schema(
            {
                vol.Required(CONF_PRESET): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=[
                            sel.SelectOptionDict(value=p["id"], label=f"{p['name']} · {_format(p['duration'])}")
                            for p in self._presets
                        ],
                        mode=sel.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="edit_preset", data_schema=schema)  # type: ignore[attr-defined]

    async def async_step_preset(self, user_input: dict[str, Any] | None = None) -> Any:
        current = next((p for p in self._presets if p["id"] == self._editing), None)
        if current is None:
            return await self.async_step_menu()  # type: ignore[attr-defined]
        if user_input is not None and user_input.get(CONF_DELETE):
            self._presets.remove(current)
            return await self.async_step_menu()  # type: ignore[attr-defined]
        return await self._preset_form("preset", user_input, current)

    async def _preset_form(
        self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any] | None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            seconds = _duration_seconds(user_input[CONF_DURATION])
            taken = any(p["name"].casefold() == name.casefold() and p is not current for p in self._presets)
            if not name:
                errors[CONF_NAME] = "name_required"
            elif taken:
                errors[CONF_NAME] = "preset_exists"
            elif seconds <= 0:
                errors[CONF_DURATION] = "invalid_duration"
            else:
                preset = current if current is not None else {"id": ulid_now()}
                preset.update({CONF_NAME: name, CONF_DURATION: seconds})
                note = (user_input.get(CONF_NOTE) or "").strip()
                if note:
                    preset[CONF_NOTE] = note
                else:
                    preset.pop(CONF_NOTE, None)
                if current is None:
                    self._presets.append(preset)
                return await self.async_step_menu()  # type: ignore[attr-defined]
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): sel.TextSelector(),
            vol.Required(CONF_DURATION): sel.DurationSelector(sel.DurationSelectorConfig(enable_day=False)),
            vol.Optional(CONF_NOTE): sel.TextSelector(),
        }
        if current is not None:
            fields[vol.Optional(CONF_DELETE, default=False)] = sel.BooleanSelector()
        suggested = user_input or (
            {
                CONF_NAME: current["name"],
                CONF_DURATION: _duration_dict(current["duration"]),
                CONF_NOTE: current.get(CONF_NOTE, ""),
            }
            if current is not None
            else {}
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),  # type: ignore[attr-defined]
            errors=errors,
            description_placeholders={"station": self._label},
        )


class HomeTimerOptionsFlow(PresetSteps, OptionsFlow):
    """Options of an area as a menu: behaviour, quick timers; saving reloads the area."""

    def __init__(self) -> None:
        self._behaviour: dict[str, Any] = {}
        self._presets: list[dict[str, Any]] = []
        self._started_with: dict[str, dict[str, Any]] = {}
        self._editing: str | None = None
        self._label = ""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        self._behaviour = {key: options.get(key, default) for key, default in DEFAULT_OPTIONS.items()}
        self._presets = [dict(p) for p in options.get(CONF_PRESETS, [])]
        self._started_with = {p["id"]: dict(p) for p in self._presets}
        self._label = self.config_entry.title
        return await self.async_step_menu()

    async def async_step_menu(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = ["behaviour", "add_preset"]
        if self._presets:
            options.append("edit_preset")
        options.append("done")
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={"area": self._label, "presets": self._listing()},
        )

    async def async_step_behaviour(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._behaviour = {
                CONF_RING_UNTIL_ACK: bool(user_input[CONF_RING_UNTIL_ACK]),
                CONF_REMINDER_MINUTES: round(user_input[CONF_REMINDER_MINUTES]),
                CONF_AUTO_DISMISS_MINUTES: round(user_input[CONF_AUTO_DISMISS_MINUTES]),
                CONF_REPORT_MISSED: bool(user_input[CONF_REPORT_MISSED]),
            }
            return await self.async_step_menu()
        schema = vol.Schema(
            {
                vol.Required(CONF_RING_UNTIL_ACK): sel.BooleanSelector(),
                vol.Required(CONF_REMINDER_MINUTES): _minutes(60),
                vol.Required(CONF_AUTO_DISMISS_MINUTES): _minutes(240),
                vol.Required(CONF_REPORT_MISSED): sel.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="behaviour", data_schema=self.add_suggested_values_to_schema(schema, self._behaviour)
        )

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data={**self._behaviour, CONF_PRESETS: self._merged_presets()})

    def _merged_presets(self) -> list[dict[str, Any]]:
        """The flow's changes applied to the presets as they are now.

        Quick timers may have been saved from the dashboard while this flow was open;
        those are kept. Only what was added, changed or deleted here is applied.
        """
        mine = {p["id"]: p for p in self._presets}
        result: list[dict[str, Any]] = []
        for preset in self.config_entry.options.get(CONF_PRESETS, []):
            pid = preset.get("id")
            if pid in self._started_with and pid not in mine:
                continue  # deleted here
            result.append(dict(mine.get(pid, preset)))
        known = {p.get("id") for p in result}
        result += [dict(p) for p in self._presets if p["id"] not in self._started_with and p["id"] not in known]
        return result


def _number(minimum: float, maximum: float, step: float, unit: str) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=sel.NumberSelectorMode.BOX, unit_of_measurement=unit)
    )


def _choice(options: list[str], key: str) -> sel.SelectSelector:
    return sel.SelectSelector(
        sel.SelectSelectorConfig(options=options, translation_key=key, mode=sel.SelectSelectorMode.LIST)
    )


# fields of each done rule type: (key, min, max, step, unit)
RULE_FIELDS: dict[str, list[tuple[str, float, float, float, str]]] = {
    RULE_OFF: [],
    RULE_PAUSE: [("below", 0, 5000, 1, "W"), ("seconds", 1, 3600, 1, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_CYCLING: [
        ("level", 0, 5000, 1, "W"),
        ("window", 10, 3600, 5, "s"),
        ("share", 1, 100, 1, "%"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_DROP: [("percent", 1, 100, 1, "%"), ("window", 10, 3600, 5, "s"), ("ignore", 0, 3600, 5, "s")],
    RULE_KNEE: [
        ("factor", 1.5, 20, 0.5, "×"),
        ("window", 10, 3600, 5, "s"),
        ("baseline", 30, 7200, 10, "s"),
        ("ignore", 0, 3600, 5, "s"),
    ],
    RULE_TIME: [("seconds", 1, 86400, 1, "s")],
    RULE_ENERGY: [("wh", 0.1, 100000, 0.1, "Wh")],
}


class StationFlow(PresetSteps, ConfigSubentryFlow):
    """Add or change a station: name, icon and how it starts (by hand or by power), then
    its presets and power settings in a menu.

    Nothing is saved before "Done"; adding a station ends with creating it,
    configuring one with updating it (which reloads the area). Power stations are set
    up here in a plain way for now; an own configuration panel will replace this.
    """

    def __init__(self) -> None:
        self._name = ""
        self._icon: str | None = None
        self._presets: list[dict[str, Any]] = []
        self._editing: str | None = None
        self._mode = MODE_MANUAL
        self._power: dict[str, Any] = {}

    @property
    def _label(self) -> str:  # type: ignore[override]
        return self._name

    def _manual_controls(self) -> bool:
        return self._mode == MODE_MANUAL or self._power.get("kind") == KIND_START_PRESET

    # ----------------------------------------------------------- entry points

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_settings(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        self._name = subentry.title
        self._icon = subentry.data.get(CONF_ICON)
        self._presets = [dict(p) for p in subentry.data.get(CONF_PRESETS, [])]
        self._mode = subentry.data.get(CONF_MODE, MODE_MANUAL)
        self._power = dict(subentry.data.get(CONF_POWER) or {})
        return await self.async_step_menu()

    # ------------------------------------------------------------------ steps

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            elif self._name_taken(name):
                errors[CONF_NAME] = "station_exists"
            else:
                self._name = name
                self._icon = user_input.get(CONF_ICON) or None
                self._mode = user_input.get(CONF_MODE, MODE_MANUAL)
                if self._mode == MODE_POWER and not self._power.get("sensor"):
                    return await self.async_step_power()
                return await self.async_step_menu()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): sel.TextSelector(),
                vol.Optional(CONF_ICON): sel.IconSelector(),
                vol.Optional(CONF_MODE, default=MODE_MANUAL): _choice([MODE_MANUAL, MODE_POWER], "mode"),
            }
        )
        suggested = user_input or {CONF_NAME: self._name, CONF_ICON: self._icon or "", CONF_MODE: self._mode}
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={"area": self._get_entry().title},
        )

    async def async_step_menu(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        options = ["settings"]
        if self._mode == MODE_POWER:
            options.append("power")
        if self._manual_controls():
            options.append("add_preset")
            if self._presets:
                options.append("edit_preset")
        options.append("done")
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={"station": self._name, "presets": self._listing(), "power": self._power_text()},
        )

    async def async_step_power(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Power sensor, when the appliance counts as running, and what then happens."""
        if user_input is not None:
            self._power.update(
                sensor=user_input["sensor"],
                on_above=float(user_input["on_above"]),
                off_after=float(user_input["off_after"]),
                kind=user_input["kind"],
            )
            return await {
                KIND_UNTIL_OFF: self.async_step_done_rule,
                KIND_START_PRESET: self.async_step_power_preset,
                KIND_WARN: self.async_step_warn,
            }[user_input["kind"]]()
        schema = vol.Schema(
            {
                vol.Required("sensor"): sel.EntitySelector(
                    sel.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required("on_above"): _number(0, 5000, 1, "W"),
                vol.Required("off_after"): _number(1, 3600, 1, "s"),
                vol.Required("kind"): _choice(list(KINDS), "kind"),
            }
        )
        current = {**POWER_DEFAULTS, **self._power}
        return self.async_show_form(
            step_id="power",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={"station": self._name},
        )

    async def async_step_done_rule(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            rule_type = user_input["type"]
            if (self._power.get("done") or {}).get("type") != rule_type:
                self._power["done"] = {"type": rule_type, **RULE_DEFAULTS[rule_type]}
            if not RULE_FIELDS[rule_type]:
                return await self.async_step_menu()
            return await self.async_step_done_params()
        schema = vol.Schema({vol.Required("type"): _choice(list(RULE_TYPES), "rule")})
        current = {"type": (self._power.get("done") or {}).get("type", RULE_OFF)}
        return self.async_show_form(
            step_id="done_rule",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={"station": self._name},
        )

    async def async_step_done_params(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        done = dict(self._power.get("done") or {"type": RULE_OFF})
        fields = RULE_FIELDS[done["type"]]
        if user_input is not None:
            done.update({key: float(user_input[key]) for key, *_ in fields})
            self._power["done"] = done
            return await self.async_step_menu()
        schema = vol.Schema({vol.Required(key): _number(lo, hi, step, unit) for key, lo, hi, step, unit in fields})
        current = {**RULE_DEFAULTS[done["type"]], **done}
        return self.async_show_form(
            step_id="done_params",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={"station": self._name, "rule": DoneRule.from_data(done).describe()},
        )

    async def async_step_power_preset(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if not self._presets:
            return self.async_show_form(
                step_id="power_preset",
                data_schema=vol.Schema({}),
                errors={"base": "no_presets"},
                description_placeholders={"station": self._name},
            ) if user_input is None else await self.async_step_menu()
        if user_input is not None:
            self._power.update(preset=user_input["preset"], cancel_on_off=bool(user_input["cancel_on_off"]))
            return await self.async_step_menu()
        schema = vol.Schema(
            {
                vol.Required("preset"): sel.SelectSelector(
                    sel.SelectSelectorConfig(
                        options=[
                            sel.SelectOptionDict(value=p["id"], label=f"{p['name']} · {_format(p['duration'])}")
                            for p in self._presets
                        ],
                        mode=sel.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required("cancel_on_off"): sel.BooleanSelector(),
            }
        )
        current = {"preset": self._power.get("preset") or self._presets[0]["id"], **POWER_DEFAULTS, **self._power}
        current["preset"] = current["preset"] or self._presets[0]["id"]
        return self.async_show_form(
            step_id="power_preset",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={"station": self._name},
        )

    async def async_step_warn(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._power["warn_after"] = float(user_input["warn_after"])
            return await self.async_step_menu()
        schema = vol.Schema({vol.Required("warn_after"): _number(1, 1440, 1, "min")})
        return self.async_show_form(
            step_id="warn",
            data_schema=self.add_suggested_values_to_schema(schema, {**POWER_DEFAULTS, **self._power}),
            description_placeholders={"station": self._name},
        )

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if self._mode == MODE_POWER and not self._power.get("sensor"):
            return await self.async_step_power()
        data: dict[str, Any] = {CONF_ICON: self._icon, CONF_PRESETS: self._presets, CONF_MODE: self._mode}
        if self._mode == MODE_POWER:
            data[CONF_POWER] = self._power
        if self.source == SOURCE_USER:
            return self.async_create_entry(title=self._name, data=data)
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), title=self._name, data=data
        )

    # ---------------------------------------------------------------- helpers

    def _power_text(self) -> str:
        if self._mode != MODE_POWER or not self._power.get("sensor"):
            return "–"
        kind = self._power.get("kind", KIND_UNTIL_OFF)
        text = f"{self._power['sensor']} · > {self._power.get('on_above', 10):g} W · {kind}"
        if kind == KIND_UNTIL_OFF:
            text += f" · {DoneRule.from_data(self._power.get('done')).describe()}"
        return text

    def _name_taken(self, name: str) -> bool:
        own = self._get_reconfigure_subentry().subentry_id if self.source != SOURCE_USER else None
        return any(
            s.subentry_type == SUBENTRY_STATION and s.title.casefold() == name.casefold() and s.subentry_id != own
            for s in self._get_entry().subentries.values()
        )
