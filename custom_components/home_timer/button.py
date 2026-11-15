"""Button of an area: acknowledge all of its ringing timers."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HomeTimerConfigEntry
from .const import BY_BUTTON
from .entity import AreaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeTimerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AcknowledgeAllButton(entry.runtime_data, "acknowledge_all")])


class AcknowledgeAllButton(AreaEntity, ButtonEntity):
    """Does nothing while nothing rings (always available, so a dashboard button stays usable)."""

    async def async_press(self) -> None:
        self.engine.acknowledge_all(by=BY_BUTTON)
