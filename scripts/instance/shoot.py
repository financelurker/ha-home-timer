"""Take the README screenshots from the local instance (after setup_demo.py and scene.py).

    python shoot.py ../../docs/images            # all
    python shoot.py ../../docs/images panel,phone  # some
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import aiohttp
from playwright.sync_api import Page, sync_playwright

from client import BASE, PASSWORD, USER, Instance, token

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
CHROME = os.environ.get("CHROME")  # a Chrome to use instead of Playwright's Chromium
OUT.mkdir(parents=True, exist_ok=True)
ERRORS: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" – {detail}"))
    if not ok:
        FAILED.append(name)


async def ids() -> dict:
    async with aiohttp.ClientSession() as s:
        ha = Instance(s, token())
        await ha.connect()
        config = await ha.call({"type": "home_timer/config/get"})
        await ha.close()
    kitchen = next(a for a in config["areas"] if a["name"] == "Kitchen")
    station = {st["name"]: st for st in kitchen["stations"]}
    return {"entry": kitchen["entry_id"], "hob": station["Hob 1"], "moka": station["Moka pot"]}


def login(page: Page) -> None:
    page.goto(BASE + "/")
    page.wait_for_selector("input[name='username']", timeout=60000)
    page.fill("input[name='username']", USER)
    page.fill("input[name='password']", PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_url(lambda u: "auth" not in u, timeout=60000)
    page.wait_for_timeout(3000)


def go(page: Page, path: str, wait: int = 3500) -> None:
    page.goto(BASE + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(wait)


def want(name: str) -> bool:
    return ONLY is None or name in ONLY


def main() -> None:
    ref = asyncio.run(ids())
    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
        ctx = browser.new_context(viewport={"width": 1366, "height": 860}, device_scale_factor=2, color_scheme="light")
        page = ctx.new_page()
        page.on("pageerror", lambda e: ERRORS.append(f"pageerror: {e}"))
        page.on("console", lambda m: m.type == "error" and "home-timer" in (m.location.get("url") or "") and ERRORS.append(m.text))
        login(page)
        if want("panel"):
            go(page, f"/home-timer/area/{ref['entry']}")
            page.screenshot(path=OUT / "panel.png")
        if want("panel-power"):
            page.set_viewport_size({"width": 1366, "height": 1240})  # the whole form, down to the done rule
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            page.screenshot(path=OUT / "panel-power.png")
            page.set_viewport_size({"width": 1366, "height": 860})
        if want("panel-station"):
            go(page, f"/home-timer/station/{ref['entry']}/{ref['hob']['id']}")
            page.screenshot(path=OUT / "panel-station.png")
        if want("dashboard"):
            go(page, "/dashboard-timers/kitchen", 5000)
            page.screenshot(path=OUT / "dashboard.png")
        if want("device"):
            go(page, f"/config/devices/device/{ref['hob']['device_id']}", 4000)
            page.screenshot(path=OUT / "device.png")
        if want("checks"):
            # the ways into the panel, in the real frontend
            go(page, "/config/integrations/integration/home_timer", 4000)
            page.locator(f"ha-config-entry-row[data-entry-id='{ref['entry']}'] ha-icon-button.link, "
                         f"ha-icon-button[href*='config_entry={ref['entry']}']").first.click()
            page.wait_for_timeout(3000)
            check("cog of an area opens the panel", "/home-timer" in page.url and ref["entry"] in page.url, page.url)
            check("panel shows the area", page.locator("home-timer-panel h1").first.inner_text() == "Kitchen", page.url)
            go(page, f"/config/devices/device/{ref['hob']['device_id']}", 4000)
            link = page.locator("a[href*='/home-timer/station/']").first
            check("device page links to the station", link.count() > 0, page.url)
            if link.count():
                link.click()
                page.wait_for_timeout(3000)
                check("the link opens the station", f"/home-timer/station/{ref['entry']}/{ref['hob']['id']}" in page.url, page.url)
        if want("phone"):
            phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
            pp = phone.new_page()
            login(pp)
            go(pp, f"/home-timer/area/{ref['entry']}")
            pp.screenshot(path=OUT / "phone-area.png")
            go(pp, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            pp.screenshot(path=OUT / "phone-power.png")
        browser.close()
    check("no errors of the panel in the browser", not ERRORS, ERRORS)
    sys.exit(1 if FAILED else 0)


main()
