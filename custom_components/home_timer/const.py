"""Constants of Home Timer."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "home_timer"
NAME: Final = "Home Timer"

# config entry: one per Home Assistant area
CONF_AREA_ID: Final = "area_id"

# options per area
CONF_RING_UNTIL_ACK: Final = "ring_until_acknowledged"
CONF_REMINDER_MINUTES: Final = "reminder_minutes"
CONF_AUTO_DISMISS_MINUTES: Final = "auto_dismiss_minutes"
CONF_REPORT_MISSED: Final = "report_missed"

DEFAULT_OPTIONS: Final = {
    CONF_RING_UNTIL_ACK: True,
    CONF_REMINDER_MINUTES: 2,
    CONF_AUTO_DISMISS_MINUTES: 30,
    CONF_REPORT_MISSED: True,
}

# timer status (no timer = idle)
STATUS_ACTIVE: Final = "active"
STATUS_PAUSED: Final = "paused"
STATUS_RINGING: Final = "ringing"
STATUSES: Final = (STATUS_ACTIVE, STATUS_PAUSED, STATUS_RINGING)

# events
EVENT_STARTED: Final = f"{DOMAIN}_started"
EVENT_PAUSED: Final = f"{DOMAIN}_paused"
EVENT_RESUMED: Final = f"{DOMAIN}_resumed"
EVENT_TIME_CHANGED: Final = f"{DOMAIN}_time_changed"
EVENT_FINISHED: Final = f"{DOMAIN}_finished"
EVENT_REMINDER: Final = f"{DOMAIN}_reminder"
EVENT_CANCELLED: Final = f"{DOMAIN}_cancelled"
EVENT_ACKNOWLEDGED: Final = f"{DOMAIN}_acknowledged"
EVENT_ASSIGNED: Final = f"{DOMAIN}_assigned"

# why a timer was cancelled / who acknowledged it
REASON_MANUAL: Final = "manual"
REASON_REPLACED: Final = "replaced"
REASON_STATION_REMOVED: Final = "station_removed"
BY_ACTION: Final = "action"
BY_BUTTON: Final = "button"
BY_TIMEOUT: Final = "timeout"

# actions
SERVICE_START: Final = "start"
SERVICE_PAUSE: Final = "pause"
SERVICE_RESUME: Final = "resume"
SERVICE_ADD_TIME: Final = "add_time"
SERVICE_SUBTRACT_TIME: Final = "subtract_time"
SERVICE_CANCEL: Final = "cancel"
SERVICE_ACKNOWLEDGE: Final = "acknowledge"
SERVICE_ASSIGN: Final = "assign"

ATTR_TIMER_ID: Final = "timer_id"
ATTR_NAME: Final = "name"
ATTR_DURATION: Final = "duration"
ATTR_NOTE: Final = "note"
ATTR_SOURCE: Final = "source"
ATTR_PRESET: Final = "preset"

STORAGE_VERSION: Final = 1
