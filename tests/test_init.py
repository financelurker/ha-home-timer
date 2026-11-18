"""The area's hub device: created in the area, left alone when moved, removed with the entry."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.home_timer.const import CONF_AREA_ID, DOMAIN


async def _setup(hass: HomeAssistant, area_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=area_id, data={CONF_AREA_ID: area_id})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_hub_device_in_the_area(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup(hass, kitchen.id)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None
    assert device.name == "Home Timer"
    assert device.area_id == kitchen.id


async def test_moved_device_stays(hass: HomeAssistant) -> None:
    areas = ar.async_get(hass)
    kitchen, living = areas.async_create("Kitchen"), areas.async_create("Living room")
    entry = await _setup(hass, kitchen.id)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    devices.async_update_device(device.id, area_id=living.id)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get(device.id).area_id == living.id


async def test_unload_and_remove(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup(hass, kitchen.id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id) is None


async def test_device_taken_out_of_the_area_stays_out(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = await _setup(hass, kitchen.id)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    devices.async_update_device(device.id, area_id=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get(device.id).area_id is None


async def test_deleted_area_is_not_written_back(hass: HomeAssistant, caplog) -> None:
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    entry = await _setup(hass, kitchen.id)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    areas.async_delete(kitchen.id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert devices.async_get(device.id).area_id is None
    assert "no longer exists" in caplog.text


async def test_area_gone_before_the_first_setup(hass: HomeAssistant) -> None:
    entry = await _setup(hass, "gone")
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device is not None
    assert device.area_id is None


async def test_device_of_0_0_1_loses_its_area_prefix(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = MockConfigEntry(domain=DOMAIN, title="Kitchen", unique_id=kitchen.id, data={CONF_AREA_ID: kitchen.id})
    entry.add_to_hass(hass)
    old = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name="Kitchen: Home Timer"
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert dr.async_get(hass).async_get(old.id).name == "Home Timer"


async def test_entity_ids_carry_the_area_once(hass: HomeAssistant) -> None:
    kitchen = ar.async_get(hass).async_create("Kitchen")
    await _setup(hass, kitchen.id)
    assert hass.states.get("sensor.kitchen_home_timer_timers") is not None
    assert hass.states.get("binary_sensor.kitchen_home_timer_ringing") is not None
    assert hass.states.get("button.kitchen_home_timer_stop_ringing") is not None
