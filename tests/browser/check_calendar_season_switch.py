"""Exercise the Appack template with Chromium touch input and a local Scheduler bundle.

python3 tests/browser/check_calendar_season_switch.py --scheduler /path/to/6.1.15/index.global.js
Requires Python Playwright and Chromium. All browser requests are served locally.
"""
import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

TEMPLATE = Path(__file__).resolve().parents[2] / "appack-platzbelegungsplan-azure.html"
DAY = '[data-calendar-view="resourceTimeGridDay"]'
WEEK = '[data-calendar-view="resourceTimeGridWeek"]'


def live_response(query, season, digest):
    day = date.fromisoformat(query["start"][0])
    until = date.fromisoformat(query["end"][0])
    events = []
    while day < until:
        events.append({
            "id": "fixture-" + day.isoformat(), "source": "training", "title": "Testtraining",
            "resourceId": "kunstrasen", "start": f"{day}T17:00:00+01:00", "end": f"{day}T18:30:00+01:00",
        })
        day += timedelta(days=1)
    return {
        "data_source": "azure", "season": query["season"][0], "events": events,
        "resources": [{"id": "rasen", "title": "Rasen"}, {"id": "kunstrasen", "title": "Kunstrasen"}],
        "training_calendar": {
            "active": True, "mode": "ACTIVE", "calendar_sha256": digest,
            "trainingControl": {"available": True, "season": season},
        },
    }


def frames(page, count=4):
    page.evaluate("""count => new Promise(done => {
      function frame() { if (--count <= 0) done(); else requestAnimationFrame(frame); }
      requestAnimationFrame(frame);
    })""", count)


def assert_event_layout(page, expected_style):
    # Compare every card with the actual slot bounds, including starts at :15 or :45.
    cards = page.locator("#calendar .fc-timegrid-event").evaluate_all(r"""events => {
      const slots = document.querySelectorAll("#calendar .fc-timegrid-slot-lane");
      function position(hours, minutes) {
        const index = (Number(hours) - 7) * 2 + Math.floor(Number(minutes) / 30);
        if (index === slots.length) return slots[index - 1].getBoundingClientRect().bottom;
        const bounds = slots[index].getBoundingClientRect();
        return bounds.top + bounds.height * (minutes % 30) / 30;
      }
      return events.map(event => {
        const [,h1,m1,h2,m2] = event.querySelector(".event-time").textContent.match(/(\d+):(\d+)–(\d+):(\d+)/);
        const rect = event.parentElement.getBoundingClientRect();
        return {style: [getComputedStyle(event).backgroundColor, getComputedStyle(event.querySelector(".fc-event-main")).color],
          error: Math.max(Math.abs(rect.top - position(h1,m1)), Math.abs(rect.bottom - position(h2,m2)))};
      });
    }""")
    assert cards and all(card["style"] == expected_style and card["error"] < 2 for card in cards), cards


def slot_under_fingers(page, y):
    return page.locator("#calendar .fc-timegrid-slots tr").evaluate_all("""(rows, y) => {
      for (const [index,row] of rows.entries()) {
        const r = row.getBoundingClientRect();
        if (y >= r.top && y < r.bottom) return index + (y-r.top)/r.height;
      }
      throw Error("Pinch center is outside the timetable");
    }""", y)


def first_pinch(context, page):
    # Send two real contacts to the visible grid without tapping it to give it focus.
    x, y = page.locator("#calendar").evaluate("""el => {
      const r = el.getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height*0.52];
    }""")
    row = page.locator("#calendar .fc-timegrid-slot-lane").first
    height = row.bounding_box()["height"]
    slot = slot_under_fingers(page, y)
    cdp = context.new_cdp_session(page)
    for step, span in enumerate((53, 62, 74, 85, 97)):
        points = [{"id": i, "x": x, "y": y + offset} for i, offset in enumerate((-span/2, span/2))]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove" if step else "touchStart", "touchPoints": points})
    assert "scaleY" in page.locator("#calendar .fc-timegrid-body").evaluate("el => el.style.transform")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()
    page.wait_for_function("()=>!document.querySelector('.is-pinching,.is-zoom-committing')")
    frames(page)
    assert row.bounding_box()["height"] > height * 1.3
    assert abs(slot_under_fingers(page, y) - slot) < 0.15
    assert page.evaluate("visualViewport.scale") == 1


def desktop_gestures(context, page):
    grid = page.locator("#calendar")
    rect = grid.bounding_box()
    x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
    row = page.locator(".fc-timegrid-slot-lane").first
    height = row.bounding_box()["height"]
    cdp = context.new_cdp_session(page)
    cdp.send("Input.dispatchMouseEvent", {
        "type": "mouseWheel", "modifiers": 2, "x": x, "y": y, "deltaX": 0, "deltaY": -48
    })
    cdp.detach()
    page.wait_for_function("h=>document.querySelector('.fc-timegrid-slot-lane').getBoundingClientRect().height>h+2", arg=height)
    frames(page, 10)
    scroller = page.locator(".fc-scroller").filter(has=page.locator(".fc-timegrid-slots")).last
    top = scroller.evaluate("e => e.scrollTop = Math.round((e.scrollHeight-e.clientHeight)/2)")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 55, steps=5)
    page.mouse.up()
    assert scroller.evaluate("e => e.scrollTop") >= top + 50
    assert not page.locator("#event-dialog-layer").is_visible()


def check(browser, source, scheduler, width, height, active):
    digest = re.search(r'const TRAINING_PLAN_SOURCE = \{\s*sha256: "([0-9a-f]{64})"', source)[1]
    other = "Sommer" if active == "Winter" else "Winter"
    patterns = json.loads(re.search(r"^    " + other + r": (\[.*?^    \])", source, re.M | re.S)[1])
    # Use the same template for a trainer and exercise availability through its normal controls.
    source = re.sub(r"var profileJSON = [^\n;]+;", 'var profileJSON = {"roleKeys":["TR"]};', source, count=1)
    source = re.sub(r'\[#assign [^\n]*?\]', '', source).replace('${hauptfarbe}', '#285ea7').replace('${appId}', 'ssv53')
    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=True,
                                  is_mobile=width < 700, timezone_id="Europe/Berlin", locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(7000)
    page.clock.install(time=datetime.fromisoformat("2026-11-18T12:00:00+01:00"))
    calls, writes, errors = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route_request(route):
        url = urlsplit(route.request.url)
        if route.request.method != "GET":
            writes.append(route.request.method)
            return route.abort()
        if url.hostname == "plan.invalid":
            return route.fulfill(content_type="text/html; charset=utf-8", body=source)
        if url.path.endswith("/dist/index.global.js"):
            return route.fulfill(content_type="application/javascript", path=scheduler)
        if url.path.endswith(".js"):
            return route.fulfill(content_type="application/javascript", body="window.Workbook={load:async()=>[]};")
        if url.path.endswith(".css"):
            return route.fulfill(content_type="text/css", body=":root{--appack-color-main:#285ea7}")
        if url.path == "/api/occupancy":
            query = parse_qs(url.query)
            calls.append(query)
            return route.fulfill(content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                                 body=json.dumps(live_response(query, active, digest)))
        return route.abort()

    context.route("**/*", route_request)
    def switch(season):
        return page.locator(f'[data-plan-season="{season}"]')

    add = page.locator("#trainer-add-occupancy")
    date_label = page.locator("#calendar-date-primary")

    def ready():
        page.wait_for_function("()=>!document.querySelector('[data-plan-season]').disabled")
        frames(page)

    try:
        page.goto("https://plan.invalid/")
        ready()
        if width == 320:
            page.locator(DAY).click()
            ready()
            page.locator("#calendar-date-next").click()
            ready()
            page.locator('[data-calendar-id="kunstrasen"]').click()
            ready()
        date_before = date_label.text_content()
        view_before = page.locator('[data-calendar-view][aria-pressed="true"]').get_attribute("data-calendar-view")
        filter_before = page.locator('[data-calendar-id][aria-pressed="true"]').get_attribute("data-calendar-id")
        live_style = page.locator("#calendar .fc-timegrid-event").first.evaluate("""e => [
          getComputedStyle(e).backgroundColor, getComputedStyle(e.querySelector('.fc-event-main')).color]""")
        assert add.is_visible() and switch(active).get_attribute("aria-pressed") == "true"
        live_calls = len(calls)
        switch(other).click()
        assert switch(active).locator(".valid-badge").is_visible()
        assert not add.is_visible() and switch(other).get_attribute("aria-pressed") == "true"
        assert page.locator(".fc.fc-media-screen").count() == 1
        first_pinch(context, page)
        assert_event_layout(page, live_style)
        page.locator(WEEK).click()
        if other == "Sommer":
            page.locator('[data-calendar-id="all"]').click()
        frames(page)
        assert page.locator("#calendar .fc-timegrid-event").count() == len(patterns)
        assert_event_layout(page, live_style)
        if width >= 700:
            desktop_gestures(context, page)
            assert_event_layout(page, live_style)
        if other == "Sommer":
            next_columns = page.locator("#calendar-date-next")
            scroll = page.locator(".fc-resourceTimeGridWeek-view")
            if next_columns.is_enabled():
                offset = scroll.evaluate("e => e.scrollLeft")
                next_columns.click()
                assert scroll.evaluate("e => e.scrollLeft") > offset
            page.locator('[data-calendar-id="rasen"]').click()
            assert page.locator("#calendar .fc-timegrid-event").count() == sum(p["resource_id"] == "rasen" for p in patterns)
            page.locator('[data-calendar-id="all"]').click()
        page.locator(DAY).click()
        page.locator("#calendar-date-button").click()
        assert date_label.text_content() == "Montag"
        page.locator("#calendar-date-next").click()
        assert date_label.text_content() == "Dienstag"
        page.clock.run_for(750)
        page.locator("#calendar .fc-timegrid-event").first.tap()
        assert page.locator("#event-dialog-layer").is_visible()
        assert not page.locator("#training-cancellation-action").is_visible()
        page.locator("#close-dialog-top").click()
        assert len(calls) == live_calls
        switch(active).click()
        ready()
        assert date_label.text_content() == date_before and add.is_visible()
        assert page.locator(f'[data-calendar-view="{view_before}"]').get_attribute("aria-pressed") == "true"
        assert page.locator(f'[data-calendar-id="{filter_before}"]').get_attribute("aria-pressed") == "true"
        assert "Testtraining" in page.locator("#calendar .fc-timegrid-event").first.text_content()
        assert "Vorschau" not in page.locator("#calendar-date-secondary").text_content()
        # A routine foreground refresh preserves a verified selection. A changed plan invalidates it.
        switch(other).click()
        with page.expect_response(lambda res: urlsplit(res.url).path == "/api/occupancy"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        frames(page)
        assert switch(other).get_attribute("aria-pressed") == "true"
        digest = "changed-plan"
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("()=>!document.body.classList.contains('training-season-preview') && document.querySelector('[data-plan-season]').disabled")
        assert not writes and not errors, (writes, errors)
        assert all(call["season"] == [active] for call in calls[1:])
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler", type=Path, required=True)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, args=["--disable-dev-shm-usage"])
        for width, height, season in [(390, 780, "Winter"), (320, 630, "Winter"), (980, 740, "Sommer")]:
            check(browser, TEMPLATE.read_text(), args.scheduler.resolve(), width, height, season)
            print(f"{width}px, {season} → Vorschau → {season}: OK")
        browser.close()


if __name__ == "__main__":
    main()
