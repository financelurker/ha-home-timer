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
