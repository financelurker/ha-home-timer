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
    await hass.async_block_till_done()


async def test_area_only_once(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    for expected in (FlowResultType.CREATE_ENTRY, FlowResultType.ABORT):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREA_ID: kitchen.id})
        assert result["type"] is expected
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()


async def test_unknown_area(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_AREA_ID: "gone"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_AREA_ID: "unknown_area"}


async def test_no_options_flow(hass: HomeAssistant) -> None:
    """Configure opens the panel (config_panel_domain); stations have no flow."""
    from .conftest import setup_area

    entry = await setup_area(hass, "Kitchen")
    assert not entry.supports_options
    assert entry.supported_subentry_types == {}


async def test_created_from_the_panel(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "panel"}, data={CONF_AREA_ID: kitchen.id})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].data == {CONF_AREA_ID: kitchen.id}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "panel"}, data={CONF_AREA_ID: kitchen.id})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "panel"}, data={CONF_AREA_ID: "gone"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "unknown_area"
