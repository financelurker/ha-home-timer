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
