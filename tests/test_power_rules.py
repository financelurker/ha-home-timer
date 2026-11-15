"""Done rules on traces: the real moka pot run, and synthetic appliances."""

from __future__ import annotations

import pytest

from custom_components.home_timer.power import DoneRule, Trace

from .fixtures import moka


def moka_trace() -> Trace:
    trace = Trace()
    for t, w in moka.SAMPLES:
        trace.add(t - moka.START, w)
    return trace


def first_fire(rule: DoneRule, trace: Trace, end: float, step: float = 5) -> float | None:
    t = 0.0
    while t <= end:
        if rule.fires(trace, t):
            return t
        t += step
    return None


@pytest.mark.parametrize(
    ("rule", "low", "high"),
    [
        (DoneRule.from_data({"type": "drop", "percent": 10, "window": 60}), 0, 60),
        (DoneRule.from_data({"type": "knee", "factor": 3, "window": 60, "baseline": 180}), 0, 60),
    ],
)
def test_moka_drop_and_knee_fire_soon_after_the_coffee_is_ready(rule, low, high) -> None:
    trace = moka_trace()
    done = moka.MARK_DONE - moka.START
    fired = first_fire(rule, trace, moka.MARK_OFF - moka.START - 1)
    assert fired is not None
    assert low <= fired - done <= high, fired - done


def test_moka_rules_that_do_not_fit() -> None:
    trace = moka_trace()
    end = moka.MARK_OFF - moka.START - 1
    assert first_fire(DoneRule.from_data({"type": "pause", "below": 10, "seconds": 10}), trace, end) is None
    assert first_fire(DoneRule.from_data({"type": "cycling", "level": 50, "share": 60}), trace, end) is None
    # without ignoring the start, the switch-on spike looks like a drop
    assert first_fire(DoneRule.from_data({"type": "drop", "percent": 10, "ignore": 0}), trace, end) <= 60


def test_cycling_thermostat_appliance() -> None:
    trace = Trace()
    trace.add(0, 800)
    t = 290.0  # heats steadily for 290 s, then the thermostat cycles 10 s on / 20 s off
    while t < 600:
        trace.add(t, 0)
        trace.add(t + 20, 800)
        t += 30
    rule = DoneRule.from_data({"type": "cycling", "level": 50, "window": 60, "share": 60})
    fired = first_fire(rule, trace, 600)
    assert 290 <= fired <= 360
    pause = DoneRule.from_data({"type": "pause", "below": 10, "seconds": 10})
    assert 290 <= first_fire(pause, trace, 600) <= 310


def test_off_time_energy() -> None:
    trace = Trace()
    trace.add(0, 2000)
    assert DoneRule.from_data({"type": "off"}).fires(trace, 10, stopped=True)
    assert not DoneRule.from_data({"type": "off"}).fires(trace, 10)
    assert DoneRule.from_data({"type": "time", "seconds": 60}).fires(trace, 60)
    assert not DoneRule.from_data({"type": "energy", "wh": 30}).fires(trace, 53)
    assert DoneRule.from_data({"type": "energy", "wh": 30}).fires(trace, 54)


def test_rule_data_and_words() -> None:
    rule = DoneRule.from_data({"type": "drop", "percent": 12, "bogus": 1, "window": -5})
    assert rule.to_data() == {"type": "drop", "percent": 12.0, "window": 60.0, "ignore": 120.0}
    assert rule.describe() == "falls by ≥ 12 % within 60 s (first 120 s ignored)"
    assert DoneRule.from_data({"type": "nonsense"}).type == "off"
