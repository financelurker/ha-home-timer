"""Test fixtures: a kitchen and a bathroom set up in Home Timer."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.home_timer.const import CONF_AREA_ID, DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/home_timer in every test."""
    yield


async def setup_area(hass: HomeAssistant, name: str, options: dict | None = None) -> MockConfigEntry:
    area = ar.async_get(hass).async_get_or_create(name)
    entry = MockConfigEntry(
        domain=DOMAIN, title=name, unique_id=area.id, data={CONF_AREA_ID: area.id}, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Let time pass and run what is due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.fixture
async def kitchen(hass: HomeAssistant) -> MockConfigEntry:
    return await setup_area(hass, "Kitchen")
