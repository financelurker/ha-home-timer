"""Setup: one config entry per area.

The area is the entry's unique id, so each area can be set up only once; the entry
is named after the area. Devices are not prefixed with the area's name: Home
Assistant puts the area into entity ids and shows it next to device names.
Stations follow as config subentries of the area. The options set how the area's
timers behave when they run out.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.helpers import area_registry as ar
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    CONF_AREA_ID,
    CONF_AUTO_DISMISS_MINUTES,
    CONF_REMINDER_MINUTES,
    CONF_REPORT_MISSED,
    CONF_RING_UNTIL_ACK,
    DEFAULT_OPTIONS,
    DOMAIN,
)


class HomeTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HomeTimerOptionsFlow:
        return HomeTimerOptionsFlow()

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


class HomeTimerOptionsFlow(OptionsFlowWithReload):
    """How the area's timers behave when they run out; saving reloads the area."""

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
