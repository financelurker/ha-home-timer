"""Setup: one config entry per area.

The area is the entry's unique id, so each area can be set up only once; the entry
is named after the area. Devices are not prefixed with the area's name: Home
Assistant puts the area into entity ids and shows it next to device names.
Stations are config subentries of the area ("Add station" on the entry): a name, an
icon and presets, edited in a menu. The options set how the area's timers behave
when they run out.
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
from .stations import CONF_ICON, CONF_PRESETS, SUBENTRY_STATION

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


class HomeTimerOptionsFlow(OptionsFlow):
    """How the area's timers behave when they run out; saving reloads the area (update listener)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = {
                CONF_RING_UNTIL_ACK: bool(user_input[CONF_RING_UNTIL_ACK]),
                CONF_REMINDER_MINUTES: round(user_input[CONF_REMINDER_MINUTES]),
                CONF_AUTO_DISMISS_MINUTES: round(user_input[CONF_AUTO_DISMISS_MINUTES]),
                CONF_REPORT_MISSED: bool(user_input[CONF_REPORT_MISSED]),
            }
            return self.async_create_entry(data=data)
        schema = vol.Schema(
            {
                vol.Required(CONF_RING_UNTIL_ACK): sel.BooleanSelector(),
                vol.Required(CONF_REMINDER_MINUTES): _minutes(60),
                vol.Required(CONF_AUTO_DISMISS_MINUTES): _minutes(240),
                vol.Required(CONF_REPORT_MISSED): sel.BooleanSelector(),
            }
        )
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, current))


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


class StationFlow(ConfigSubentryFlow):
    """Add or change a station: name and icon, then its presets in a menu.

    Nothing is saved before "Done"; adding a station ends with creating it,
    configuring one with updating it (which reloads the area).
    """

    def __init__(self) -> None:
        self._name = ""
        self._icon: str | None = None
        self._presets: list[dict[str, Any]] = []
        self._editing: str | None = None

    # ----------------------------------------------------------- entry points

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_settings(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        self._name = subentry.title
        self._icon = subentry.data.get(CONF_ICON)
        self._presets = [dict(p) for p in subentry.data.get(CONF_PRESETS, [])]
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
                return await self.async_step_menu()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): sel.TextSelector(),
                vol.Optional(CONF_ICON): sel.IconSelector(),
            }
        )
        suggested = user_input or {CONF_NAME: self._name, CONF_ICON: self._icon or ""}
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={"area": self._get_entry().title},
        )

    async def async_step_menu(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        options = ["settings", "add_preset"]
        if self._presets:
            options.append("edit_preset")
        options.append("done")
        listing = "\n".join(f"- {p['name']} · {_format(p['duration'])}" for p in self._presets) or "–"
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={"station": self._name, "presets": listing},
        )

    async def async_step_add_preset(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._preset_form("add_preset", user_input, None)

    async def async_step_edit_preset(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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
        return self.async_show_form(step_id="edit_preset", data_schema=schema)

    async def async_step_preset(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        current = next((p for p in self._presets if p["id"] == self._editing), None)
        if current is None:
            return await self.async_step_menu()
        if user_input is not None and user_input.get(CONF_DELETE):
            self._presets.remove(current)
            return await self.async_step_menu()
        return await self._preset_form("preset", user_input, current)

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        data = {CONF_ICON: self._icon, CONF_PRESETS: self._presets}
        if self.source == SOURCE_USER:
            return self.async_create_entry(title=self._name, data=data)
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), title=self._name, data=data
        )

    # ---------------------------------------------------------------- helpers

    async def _preset_form(
        self, step_id: str, user_input: dict[str, Any] | None, current: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            seconds = _duration_seconds(user_input[CONF_DURATION])
            taken = any(
                p["name"].casefold() == name.casefold() and p is not current for p in self._presets
            )
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
                return await self.async_step_menu()
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
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
            description_placeholders={"station": self._name},
        )

    def _name_taken(self, name: str) -> bool:
        own = self._get_reconfigure_subentry().subentry_id if self.source != SOURCE_USER else None
        return any(
            s.subentry_type == SUBENTRY_STATION and s.title.casefold() == name.casefold() and s.subentry_id != own
            for s in self._get_entry().subentries.values()
        )
