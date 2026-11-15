"""The configuration panel (admins only).

A web component (frontend/, Lit shipped with the integration: it works without
internet) at /home-timer. It reads and writes through the websocket commands in
ws.py; live state comes from the entities, which the panel reads like any card.

"Configure" of an area opens it at that area (``config_panel_domain``), a device's
link ("configuration URL") at the area or the station. It is hidden from the
sidebar unless the panel's setting says otherwise. Without Home Assistant's frontend
(tests) nothing is registered, and the integration works the same.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from ..const import DOMAIN, NAME, PANEL_URL
from .settings import async_load_settings, get_settings
from .ws import async_setup_ws

PANEL_ICON = "mdi:timer-cog-outline"
COMPONENT = "home-timer-panel"
STATIC = f"/{DOMAIN}_panel"  # + /<version>/<file>: a new version is loaded fresh
FRONTEND = Path(__file__).parent.parent / "frontend"
DATA_MODULE = f"{DOMAIN}_panel_module"


async def async_setup_panel(hass: HomeAssistant) -> None:
    async_setup_ws(hass)
    await async_load_settings(hass)
    if "frontend" not in hass.config.components or hass.http is None:
        return
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415 (needs http)

    version = str((await async_get_integration(hass, DOMAIN)).version or "0.0.0")
    base = f"{STATIC}/{version}"
    # a development copy (version 0.0.0) is not cached, so a change shows on reload
    await hass.http.async_register_static_paths([StaticPathConfig(base, str(FRONTEND), version != "0.0.0")])
    hass.data[DATA_MODULE] = f"{base}/{COMPONENT}.js"
    async_register_panel(hass)


@callback
def async_register_panel(hass: HomeAssistant, *, update: bool = False) -> None:
    """Register the panel – again (``update``) when "show in the sidebar" changed."""
    if DATA_MODULE not in hass.data:
        return
    from homeassistant.components import frontend  # noqa: PLC0415 (needs the frontend)

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=NAME,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL,
        config={
            "_panel_custom": {
                "name": COMPONENT,
                "module_url": hass.data[DATA_MODULE],
                "embed_iframe": False,
                "trust_external": False,
            }
        },
        require_admin=True,
        config_panel_domain=DOMAIN,  # "Configure" of an area opens the panel (?config_entry=<id>)
        show_in_sidebar=bool(get_settings(hass)["sidebar"]),
        update=update,
    )
