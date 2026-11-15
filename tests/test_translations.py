"""en.json and de.json have the same keys."""

from __future__ import annotations

import json
from pathlib import Path

TRANSLATIONS = Path(__file__).resolve().parent.parent / "custom_components" / "home_timer" / "translations"


def _keys(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _keys(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip(".")


def test_same_keys():
    en = set(_keys(json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))))
    de = set(_keys(json.loads((TRANSLATIONS / "de.json").read_text(encoding="utf-8"))))
    assert sorted(en - de) == [], "missing in de.json"
    assert sorted(de - en) == [], "missing in en.json"


def test_keys_used_by_the_flow_exist_and_are_filled():
    for lang in ("en", "de"):
        strings = json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))
        config = strings["config"]
        assert config["error"]["unknown_area"]
        assert config["abort"]["already_configured"]
        step = config["step"]["user"]
        for key in ("area_id",):
            assert step["data"][key] and step["data_description"][key]
        values = list(_values(strings))
        assert all(isinstance(v, str) and v.strip() for v in values), f"empty value in {lang}.json"


def _values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _values(value)
    else:
        yield node


def test_exceptions_have_messages():
    for lang in ("en", "de"):
        strings = json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))
        for key, value in strings["exceptions"].items():
            assert isinstance(value, dict) and value.get("message"), f"{lang}: exceptions.{key}"


async def test_error_text_reaches_the_user(hass, kitchen) -> None:
    import pytest
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import device_registry as dr

    from custom_components.home_timer.const import DOMAIN

    from .conftest import add_station

    hob = await add_station(hass, kitchen, "Hob 1", [("Rice", 600)])
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, hob), kitchen.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "start", {"device_id": device.id, "preset": "Soup"}, blocking=True)
    assert str(err.value).startswith('The station Hob 1 has no preset "Soup"')
