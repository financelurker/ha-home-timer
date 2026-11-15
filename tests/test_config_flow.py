"""Setup: one entry per area, named after the area; options per area."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar

from custom_components.home_timer.const import CONF_AREA_ID, DOMAIN


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_area_named_after_the_area(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREA_ID: kitchen.id})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"
    assert result["data"] == {CONF_AREA_ID: kitchen.id}
    assert result["result"].unique_id == kitchen.id


async def test_area_only_once(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    for expected in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREA_ID: kitchen.id})
        assert result["type"] is expected
    assert result["reason"] == "already_configured"


async def test_unknown_area(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREA_ID: "gone"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_AREA_ID: "unknown_area"}


async def test_options(hass: HomeAssistant) -> None:
    from .conftest import setup_area

    entry = await setup_area(hass, "Kitchen")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "ring_until_acknowledged": False,
            "reminder_minutes": 5.0,
            "auto_dismiss_minutes": 0,
            "report_missed": False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "ring_until_acknowledged": False,
        "reminder_minutes": 5,
        "auto_dismiss_minutes": 0,
        "report_missed": False,
    }
    assert entry.runtime_data.option("reminder_minutes") == 5
