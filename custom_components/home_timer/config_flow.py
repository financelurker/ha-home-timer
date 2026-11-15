"""Setup: one config entry per area.

The area is the entry's unique id, so each area can be set up only once. The name
(default: the area's name) prefixes the area's devices, e.g. "Kitchen: Hob 1".
Stations follow as config subentries of the area.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector as sel

from .const import CONF_AREA_ID, DOMAIN


class HomeTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

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
                name = (user_input.get(CONF_NAME) or "").strip() or area.name
                return self.async_create_entry(title=name, data={CONF_AREA_ID: area_id})

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_ID): sel.AreaSelector(),
                vol.Optional(CONF_NAME): sel.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )
