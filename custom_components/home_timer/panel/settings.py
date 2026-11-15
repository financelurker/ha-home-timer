"""The panel's own settings (for all areas), kept in .storage/home_timer.panel."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN

STORE_KEY = f"{DOMAIN}.panel"
DATA_SETTINGS = f"{DOMAIN}_panel_settings"
DEFAULTS: dict[str, Any] = {"sidebar": False}  # hidden: opened with an area's cog or a device's link


def get_settings(hass: HomeAssistant) -> dict[str, Any]:
    return dict(hass.data.get(DATA_SETTINGS, DEFAULTS))


async def async_load_settings(hass: HomeAssistant) -> dict[str, Any]:
    stored = await Store(hass, 1, STORE_KEY).async_load() or {}
    settings = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    hass.data[DATA_SETTINGS] = settings
    return settings


async def async_save_settings(hass: HomeAssistant, changes: dict[str, Any]) -> dict[str, Any]:
    from . import async_register_panel  # noqa: PLC0415 (the panel follows its settings)

    settings = {**get_settings(hass), **{k: v for k, v in changes.items() if k in DEFAULTS}}
    hass.data[DATA_SETTINGS] = settings
    await Store(hass, 1, STORE_KEY).async_save(settings)
    async_register_panel(hass, update=True)
    return settings
