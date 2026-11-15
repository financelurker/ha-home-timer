"""Give the demo's moka pot a week of runs, so its page in the panel has runs to show.

The runs are made from the real moka pot run in the tests (tests/fixtures/moka.py):
more or less water stretches it, the sensor reports every ~10 s with a little
scatter. Each run goes through the integration's own code (``make_run``, and
``first_fire`` for when the moka pot's done rule rings), so it looks exactly like
a stored run. Runs on the same machine as the instance: it writes the area's run
store while the area is disabled.

    python seed_runs.py
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp

from client import Instance, token

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "fixtures")]

import moka  # noqa: E402

from custom_components.home_timer.power import DoneRule, Trace  # noqa: E402
from custom_components.home_timer.runs import first_fire, make_run, runs_key  # noqa: E402

DAYS = [6, 5, 5, 4, 3, 2, 1, 1]  # days ago, one run each (two on some days)


def moka_at(t: float) -> float:
    """The fixture's curve, linearly between its readings (t in seconds after switching on)."""
    pts = [(s - moka.START, w) for s, w in moka.SAMPLES[:-1]]
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, w0), (t1, w1) in zip(pts, pts[1:], strict=False):
        if t0 <= t <= t1:
            return w0 + (w1 - w0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def one_run(rng: random.Random, start: datetime, rule: DoneRule) -> dict:
    water = rng.uniform(0.8, 1.15)  # more water: everything after the heat-up takes longer
    off = (moka.MARK_OFF - moka.START) * water
    trace = Trace()
    t = 0.0
    while t < off:
        base = moka_at(t if t < 30 else 30 + (t - 30) / water)
        trace.add(round(t, 1), round(base + rng.gauss(0, 0.8), 1))
        t += rng.uniform(8, 12)
    trace.add(round(off, 1), 0.0)
    rang = first_fire(rule, trace, off)
    return make_run(start, off, trace, on_above=10, rang=rang if rang is not None else off,
                    trigger="rule" if rang is not None else "off", sensor="sensor.moka_pot_plug_power")


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        ha = Instance(s, token())
        await ha.connect()
        config_dir = Path((await ha.rest("GET", "/api/config"))["config_dir"])
        cfg = await ha.call({"type": "home_timer/config/get"})
        area = next(a for a in cfg["areas"] if a["name"] == "Kitchen")
        station = next(st for st in area["stations"] if st["name"] == "Moka pot")
        rule = DoneRule.from_data(station["power"]["done"])
        rng = random.Random(7)
        today = datetime.now(UTC).replace(hour=6, minute=40, second=0, microsecond=0)
        runs = [one_run(rng, today - timedelta(days=d, minutes=-rng.randint(0, 80) - (600 if i % 3 == 2 else 0)), rule)
                for i, d in enumerate(DAYS)]
        entry_id = area["entry_id"]
        await ha.call({"type": "config_entries/disable", "entry_id": entry_id, "disabled_by": "user"})
        await asyncio.sleep(3)
        path = config_dir / ".storage" / runs_key(entry_id)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "version": 1, "minor_version": 1, "key": runs_key(entry_id), "data": {"runs": {}, "imported": []}}
        data["data"]["runs"][station["id"]] = sorted(runs, key=lambda r: r["start"])
        data["data"]["imported"] = sorted({*data["data"].get("imported", []), station["id"]})
        path.write_text(json.dumps(data), encoding="utf-8")
        await ha.call({"type": "config_entries/disable", "entry_id": entry_id, "disabled_by": None})
        await asyncio.sleep(5)
        print(f"seeded {len(runs)} moka pot runs")
        await ha.close()


if __name__ == "__main__":
    asyncio.run(main())
