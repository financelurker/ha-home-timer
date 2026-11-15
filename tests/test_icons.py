"""icons.json matches the entities and actions."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "home_timer"
ICONS = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
STRINGS = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
SERVICES = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))


def test_every_entity_has_an_icon():
    translated = {(p, k) for p, keys in STRINGS["entity"].items() for k in keys}
    with_icon = {(p, k) for p, keys in ICONS["entity"].items() for k in keys}
    assert translated == with_icon


def test_every_action_has_an_icon_and_translation():
    assert set(ICONS["services"]) == set(SERVICES) == set(STRINGS["services"])
    for name, spec in SERVICES.items():
        assert set(spec.get("fields", {})) == set(STRINGS["services"][name].get("fields", {})), name


def test_services_yaml_selectors_are_valid():
    from homeassistant.helpers import selector

    for spec in SERVICES.values():
        if "target" in spec:
            selector.TargetSelector.CONFIG_SCHEMA(spec["target"])
        for field in spec.get("fields", {}).values():
            selector.validate_selector(field["selector"])


def test_targets_without_device_filters():
    """hassfest: services do not support device filters on targets."""
    for spec in SERVICES.values():
        assert "device" not in spec.get("target", {})
