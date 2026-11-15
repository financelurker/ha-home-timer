"""Setup: one config entry per area.

The area is the entry's unique id, so each area can be set up only once; the entry
is named after the area. Devices are not prefixed with the area's name: Home
Assistant puts the area into entity ids and shows it next to device names.

Everything else is configured in the panel (panel/): the area's options and quick
timers, its stations and their presets. "Configure" of an area opens the panel
(``config_panel_domain``), so there is no options flow; stations have no flow of
their own either (no "Add station", no cog). The panel also creates areas, through
this flow with the source ``panel``.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector as sel

from .const import CONF_AREA_ID, DOMAIN, PANEL_URL

SOURCE_PANEL = "panel"


class HomeTimerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            area = ar.async_get(self.hass).async_get_area(user_input[CONF_AREA_ID])
            if area is None:
                errors[CONF_AREA_ID] = "unknown_area"
            else:
                await self.async_set_unique_id(area.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=area.name,
                    data={CONF_AREA_ID: area.id},
                    description_placeholders={"panel": f"/{PANEL_URL}"},
                )

        schema = vol.Schema({vol.Required(CONF_AREA_ID): sel.AreaSelector()})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={"panel": f"/{PANEL_URL}"},
        )

    async def async_step_panel(self, data: dict[str, Any]) -> ConfigFlowResult:
        """An area added in the panel: no form."""
        area = ar.async_get(self.hass).async_get_area(data.get(CONF_AREA_ID, ""))
        if area is None:
            return self.async_abort(reason="unknown_area")
        await self.async_set_unique_id(area.id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=area.name, data={CONF_AREA_ID: area.id})
