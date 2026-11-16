"""Take the README screenshots from the local instance (after setup_demo.py and scene.py).

    python shoot.py ../../docs/images            # all
    python shoot.py ../../docs/images panel,phone  # some
"""

from __future__ import annotations

import asyncio
import os
import re
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
        if want("panel-runs"):
            # the moka pot's page with a draft of its done rule: the live preview on the stored runs
            page.set_viewport_size({"width": 1366, "height": 1500})
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            rows = page.locator("home-timer-panel .runrow")
            check("the page lists the moka pot's runs", rows.count() >= 8, rows.count())
            seeded = rows.filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.first.click()  # a run of seed_runs.py (today's come from scene.py's test runs)
                page.wait_for_timeout(1500)
            check("the page draws the curve", page.locator("home-timer-panel svg.chart path.curve").count() == 1)
            fields = page.locator("home-timer-panel .opt.on .sentence input")
            if fields.count() >= 2:
                fields.nth(0).fill("8")  # at least 8 % …
                fields.nth(1).fill("45")  # … within 45 s
                page.wait_for_timeout(2500)
            check("a draft shows its preview", page.locator("home-timer-panel .preview").count() == 1)
            # show a run the draft changes (its dashed ring)
            rows = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Running"))
            for i in range(rows.count()):
                rows.nth(i).click()
                page.wait_for_timeout(700)
                if page.locator("home-timer-panel svg.chart line.mark.dash").count():
                    break
            check("the curve shows the draft's ring", page.locator("home-timer-panel svg.chart line.mark.dash").count() == 1)
            page.locator("home-timer-panel .pane").first.evaluate("el => el.scrollTo(0, 0)")  # the clicks scrolled it
            page.wait_for_timeout(600)
            page.screenshot(path=OUT / "panel-runs.png")
            page.set_viewport_size({"width": 1366, "height": 860})
        if want("panel-learn"):
            # learning: a marked run, and the suggestions from the marked runs
            page.set_viewport_size({"width": 1366, "height": 1900})
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.first.click()
                page.wait_for_timeout(1500)
            check("a marked run shows its mark", page.locator("home-timer-panel svg.chart line.done").count() == 1)
            # a search shows its progress (the bar from the click on) and can be cancelled …
            page.locator("home-timer-panel .sugrow button").first.click()
            try:
                page.wait_for_selector("home-timer-panel .sprog [role=progressbar]", timeout=3000)
                bar = True
            except Exception:  # noqa: BLE001
                bar = False
            check("a search shows its progress bar", bar)
            cancel = page.locator("home-timer-panel .sprog button")
            if cancel.count():
                try:
                    cancel.click(timeout=1000)
                except Exception:  # noqa: BLE001 – it finished meanwhile
                    pass
            page.wait_for_selector("home-timer-panel .sprog", state="detached", timeout=60000)
            cancelled = page.locator("home-timer-panel .suggest .note").filter(has_text="cancelled").count() == 1
            check("Cancel stops the search (or it finished first)", cancelled or page.locator("home-timer-panel .sug").count() > 0)
            # … and the next one runs to its end
            page.locator("home-timer-panel .sugrow button").first.click()
            page.wait_for_selector("home-timer-panel .sug", timeout=60000)
            check("suggestions from the marked runs", page.locator("home-timer-panel .sug").count() >= 2)
            page.wait_for_timeout(800)
            page.screenshot(path=OUT / "panel-learn.png")
            # "Use" a suggestion after picking rules by hand: the rule's select follows it (a select the
            # user touched ignores the options' selected attribute – the bug this guards against)
            select = page.locator("home-timer-panel select.inlsel").first
            saved = select.input_value()
            sugs = page.locator("home-timer-panel .sug")
            target = next((i for i in range(sugs.count()) if sugs.nth(i).get_attribute("data-type") not in (None, saved, "steps")), None)
            if target is None:
                check("Use: a suggestion of another rule to try", False)
            else:
                rule = sugs.nth(target).get_attribute("data-type")
                select.select_option(rule)  # picked by hand once …
                page.wait_for_timeout(400)
                select.select_option("off")  # … then another
                page.wait_for_timeout(400)
                sugs.nth(target).locator("button").click()
                page.wait_for_timeout(600)
                check("Use: the rule's select shows the suggested rule", select.input_value() == rule, (rule, select.input_value()))
                discard = page.locator("home-timer-panel .savebar button").first
                if discard.count():
                    discard.click()
                    page.wait_for_timeout(600)
                check("Discard: the select shows the saved rule again", select.input_value() == saved, (saved, select.input_value()))
            page.set_viewport_size({"width": 1366, "height": 860})
        if want("panel-steps"):
            # steps: add one, move it up, the pins on the curve, remove it again
            page.set_viewport_size({"width": 1366, "height": 1500})
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.first.click()
                page.wait_for_timeout(1500)
            page.locator("home-timer-panel .addstep button").click()
            page.wait_for_timeout(2500)
            steps = page.locator("home-timer-panel .steps.many .step")
            check("+ Add a step: two steps joined by then", steps.count() == 2 and page.locator("home-timer-panel .then").count() == 1, steps.count())
            # for the docs: "it has run for 2 min, then the power falls by …" – the drop only counts once 2 min are past
            steps.nth(1).locator("select.inlsel").select_option("drop")
            page.wait_for_timeout(400)
            steps.nth(0).locator("select.inlsel").select_option("time")
            page.wait_for_timeout(400)
            steps.nth(0).locator(".sentence input").first.fill("120")
            steps.nth(1).locator(".sentence input").last.fill("0")  # its "ignore" counts from step 1: none needed
            page.wait_for_timeout(2500)
            check("steps: the curve pins where each step was met", page.locator("home-timer-panel svg.chart g.pin").count() == 2)
            page.set_viewport_size({"width": 1366, "height": 1640})
            page.locator("home-timer-panel .pane").first.evaluate("el => el.scrollTo(0, 0)")  # the selects scrolled it
            page.wait_for_timeout(600)
            page.screenshot(path=OUT / "panel-steps.png")
            types = [steps.nth(i).locator("select.inlsel").input_value() for i in range(2)]
            steps.nth(1).locator(".tools button").first.click()  # ▲
            page.wait_for_timeout(600)
            moved = [steps.nth(i).locator("select.inlsel").input_value() for i in range(2)]
            check("▲ moves a step up", moved == types[::-1], moved)
            # dragging the handle onto the other step moves it back
            steps.nth(1).locator(".handle").drag_to(steps.nth(0))
            page.wait_for_timeout(600)
            dragged = [steps.nth(i).locator("select.inlsel").input_value() for i in range(2)]
            check("dragging moves a step", dragged == types, dragged)
            check("a sequence offers no \"switches off\"", steps.nth(1).locator("option[value=off]").count() == 0)
            steps.nth(1).locator(".tools button").nth(2).click()  # ×
            page.wait_for_timeout(600)
            check("× removes a step: one rule again", page.locator("home-timer-panel .steps.one .step").count() == 1)
            discard = page.locator("home-timer-panel .savebar button").first
            if discard.count():
                discard.click()
                page.wait_for_timeout(600)
            page.set_viewport_size({"width": 1366, "height": 860})
        if want("reread"):
            # "read again from the history" on a run that is complete: nothing more (and nothing changes)
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.first.click()
                page.wait_for_timeout(1500)
            page.locator("home-timer-panel .runacts button[title*='history']").first.click()
            page.wait_for_selector("home-timer-panel .curve .note, home-timer-panel .curve .err", timeout=20000)
            notes = page.locator("home-timer-panel .curve .note").all_inner_texts()
            check("read again from the history: a seeded run has nothing more", any("nothing more" in n for n in notes), notes)
        if want("zoom"):
            # zoom into the curve: buttons keep the mark in view, the overview moves along, Ctrl + wheel, the whole run again
            page.set_viewport_size({"width": 1366, "height": 1100})
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.first.click()
                page.wait_for_timeout(1500)
            zoom_in = page.locator("home-timer-panel .zoombar button").nth(1)
            for _ in range(3):
                zoom_in.click()
                page.wait_for_timeout(300)
            box = page.locator("home-timer-panel svg.chart").bounding_box()
            done_x = float(page.locator("home-timer-panel svg.chart line.done").get_attribute("x1"))
            check("zoomed in: the overview shows the part", page.locator("home-timer-panel svg.overview rect.ovwin").count() == 1)
            check("zoomed in: the mark stays in view", 52 <= done_x <= box["width"] - 14, done_x)
            widths = page.locator("home-timer-panel svg.chart rect").evaluate_all("els => els.map((e) => +e.getAttribute('width'))")
            check("zoomed in: no shape of negative width", all(w >= 0 for w in widths), widths)
            page.screenshot(path=OUT / "zoom.png")
            # the power scale fits the part shown – the toggle gives the whole range back
            top_label = lambda: float(page.locator("home-timer-panel svg.chart text.ax.y").last.text_content().split()[0])  # noqa: E731
            fitted = top_label()
            page.locator("home-timer-panel .zoombar button[aria-pressed]").click()
            page.wait_for_timeout(300)
            full = top_label()
            page.locator("home-timer-panel .zoombar button[aria-pressed]").click()
            page.wait_for_timeout(300)
            check("zoomed in: the power scale fits the part shown", fitted < full and top_label() == fitted, (fitted, full))
            label = page.locator("home-timer-panel .zoombar .chs").inner_text()
            # zoomed in, a click sets the mark where it is in the view (to the second)
            secs = lambda txt: sum(int(x) * 60 ** i for i, x in enumerate(reversed(txt.strip().split(":"))))  # noqa: E731
            a, b = (secs(x) for x in label.split("–"))
            page.mouse.click(box["x"] + 52 + (box["width"] - 66) / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(500)
            placed = secs(page.locator("home-timer-panel .mbar b").inner_text().split()[-1])
            check("zoomed in: a click sets the mark there", abs(placed - (a + b) / 2) <= 2, (placed, a, b))
            ov = page.locator("home-timer-panel svg.overview").bounding_box()
            page.mouse.click(ov["x"] + ov["width"] * 0.2, ov["y"] + ov["height"] / 2)  # move to the first fifth
            page.wait_for_timeout(400)
            check("the overview moves the part shown", page.locator("home-timer-panel .zoombar .chs").inner_text() != label)
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.keyboard.down("Control")
            page.mouse.wheel(0, 400)  # out
            page.keyboard.up("Control")
            page.wait_for_timeout(400)
            page.locator("home-timer-panel .zoombar button[aria-label='The whole run']").click()
            page.wait_for_timeout(400)
            check("the whole run again: no overview", page.locator("home-timer-panel svg.overview").count() == 0)
            page.set_viewport_size({"width": 1366, "height": 860})
        if want("phases"):
            # mark phases on the bar under the curve: split twice, rename, move an edge, kept after a reload, remove
            page.set_viewport_size({"width": 1366, "height": 1100})
            go(page, f"/home-timer/station/{ref['entry']}/{ref['moka']['id']}")
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            if seeded.count():
                seeded.nth(1).click()
                page.wait_for_timeout(1500)
            page.locator("home-timer-panel .runacts button[aria-pressed]").click()
            page.wait_for_timeout(300)
            check("phases: an empty bar invites the first split", page.locator("home-timer-panel svg.pbar .pempty").count() == 1)
            # a moka pot: heating up, the coffee coming up (until your mark: done), then the plate keeps it warm
            bar = page.locator("home-timer-panel svg.pbar").bounding_box()
            left, width = bar["x"] + 52, bar["width"] - 66
            done_x = float(page.locator("home-timer-panel svg.chart line.done").get_attribute("x1"))
            page.mouse.click(left + width * 0.25, bar["y"] + 14)
            page.wait_for_timeout(300)
            page.mouse.click(bar["x"] + done_x, bar["y"] + 14)
            page.wait_for_timeout(300)
            check("phases: two clicks make three phases", page.locator("home-timer-panel svg.pbar .pseg").count() == 3)
            secs = lambda txt: sum(int(x) * 60 ** i for i, x in enumerate(reversed(txt.strip().split(":"))))  # noqa: E731
            mark_s = secs(page.locator("home-timer-panel .mbar b").inner_text().split()[-1])
            start_s = secs(page.locator("home-timer-panel .ptool .psel .chs").inner_text().split("–")[0])
            check("phases: a phase starts exactly where you click (at your mark here)", abs(start_s - mark_s) <= 2, (start_s, mark_s))
            name = page.locator("home-timer-panel .ptool input.pname")
            for i, label in enumerate(("Heating up", "Brewing", "Keeping warm")):
                page.locator("home-timer-panel .ptool .phchip").nth(i).click()
                page.wait_for_timeout(200)
                name.fill(label)
                name.press("Enter")
                page.wait_for_timeout(300)
            check("phases: the phases are renamed", all(n in page.locator("home-timer-panel svg.pbar").text_content() for n in ("Heating up", "Brewing", "Keeping warm")))
            before = page.locator("home-timer-panel .ptool .psel .chs").inner_text()
            page.locator("home-timer-panel .pbox").focus()
            page.keyboard.press("Shift+ArrowLeft")
            page.wait_for_timeout(300)
            check("phases: ← moves the selected phase's start", page.locator("home-timer-panel .ptool .psel .chs").inner_text() != before)
            page.screenshot(path=OUT / "phases.png")
            page.locator("home-timer-panel .ptool button.f").click()  # done marking
            page.wait_for_timeout(1200)  # (saved)
            check("phases: the runs list shows them", page.locator("home-timer-panel .runrow.sel svg.strip rect.ph").count() == 3)
            page.reload()
            page.wait_for_timeout(3000)
            seeded = page.locator("home-timer-panel .runrow").filter(has_not_text=re.compile("Today|Running"))
            seeded.nth(1).click()
            page.wait_for_timeout(1500)
            check("phases: kept after a reload", page.locator("home-timer-panel svg.pbar .pseg").count() == 3 and page.locator("home-timer-panel .phlist .phchip").count() == 3)
            page.locator("home-timer-panel .runacts button[aria-pressed]").click()
            page.wait_for_timeout(300)
            page.locator("home-timer-panel .ptool button", has_text="Remove all").click()
            page.wait_for_timeout(1200)
            check("phases: removed again", page.locator("home-timer-panel svg.pbar .pseg").count() == 0)
            page.locator("home-timer-panel .ptool button.f").click()
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
            pp.locator("home-timer-panel .runrow").first.scroll_into_view_if_needed()  # down to the runs
            pp.mouse.move(195, 600)
            pp.mouse.wheel(0, -260)  # … with the end of what the timer should do above them
            pp.wait_for_timeout(800)
            pp.screenshot(path=OUT / "phone-runs.png")
        browser.close()
    check("no errors of the panel in the browser", not ERRORS, ERRORS)
    sys.exit(1 if FAILED else 0)


main()
