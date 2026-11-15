"""Device triggers: a station's timer finished, reminds or is acknowledged – or any
timer of the area, on the area's Home Timer device."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_ACKNOWLEDGED, EVENT_FINISHED, EVENT_REMINDER, EVENT_STARTED

TRIGGER_EVENTS = {
    "started": EVENT_STARTED,
    "finished": EVENT_FINISHED,
    "reminder": EVENT_REMINDER,
    "acknowledged": EVENT_ACKNOWLEDGED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend({vol.Required(CONF_TYPE): vol.In(TRIGGER_EVENTS)})


def _event_filter(hass: HomeAssistant, device_id: str) -> dict[str, str] | None:
    """Event data a device's triggers match: the area (hub device) or the station's device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == device.config_entry_id:
            return {"entry_id": identifier}
        return {"device_id": device_id}
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    if _event_filter(hass, device_id) is None:
        return []
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: trigger_type}
        for trigger_type in TRIGGER_EVENTS
    ]


async def async_attach_trigger(
    hass: HomeAssistant, config: ConfigType, action: TriggerActionType, trigger_info: TriggerInfo
) -> CALLBACK_TYPE:
    event_data = _event_filter(hass, config[CONF_DEVICE_ID]) or {"device_id": config[CONF_DEVICE_ID]}
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: TRIGGER_EVENTS[config[CONF_TYPE]],
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")
