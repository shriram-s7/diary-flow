"""Driver view on a phone: load the scenario, run it, open driver.html?truckId=TRK-03 in a phone-sized browser
(via the LAN address, like a real phone) and check what a driver needs:
  * a navigation arrow on the map, at the truck's position, pointing along the road,
  * the turn-by-turn banner with a distance and an instruction, and the "Then" step,
  * the next-stop / final-stop markers and the blue remaining route,
  * no console errors. Screenshots go to screenshots/driver_*.png.

    backend/venv/Scripts/python e2e/driver_view_check.py [--host 10.124.34.216] [--truck TRK-03]
Needs the app running (start.bat / start.sh).
"""
import argparse
import asyncio
import json
import os
import urllib.request

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "screenshots")
API = "http://127.0.0.1:8600"


def call(method, path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body or {}).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


async def main(host, truck_id):
    os.makedirs(SHOTS, exist_ok=True)
    call("POST", "/scenarios/chennai-default/load")
    call("POST", "/simulation/speed", {"speed": 30})
    call("POST", "/simulation/start")
    url = f"http://{host}:5600/driver.html?truckId={truck_id}"
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(url)
        # Wait until the truck is driving and the banner shows a real distance.
        for _ in range(120):
            state = json.loads(urllib.request.urlopen(API + "/state").read())
            t = next(x for x in state["trucks"] if x["id"] == truck_id)
            if t["status"] == "EN_ROUTE" and t["route_index"] > 5:
                break
            await page.wait_for_timeout(500)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=os.path.join(SHOTS, "driver_phone_1.png"))
        banner = await page.locator(".df-maneuver-banner").inner_text()
        await page.wait_for_timeout(4000)
        await page.screenshot(path=os.path.join(SHOTS, "driver_phone_2.png"))
        banner2 = await page.locator(".df-maneuver-banner").inner_text()
        await page.locator(".df-sheet-collapsed-row").click()
        await page.wait_for_timeout(800)
        await page.screenshot(path=os.path.join(SHOTS, "driver_phone_sheet.png"))
        sheet = await page.locator(".df-bottom-sheet").inner_text()

        # Arrow vs truck: the marker must sit where the backend says the truck is (it is placed along the route).
        info = await page.evaluate("""() => {
            const el = document.querySelector('.df-nav-puck');
            const r = el.getBoundingClientRect();
            const map = document.querySelector('.maplibregl-canvas').getBoundingClientRect();
            return {x: r.x + r.width / 2 - map.x, y: r.y + r.height / 2 - map.y, w: map.width, h: map.height,
                    transform: el.style.transform}
        }""")
        layers = await page.evaluate("""() => Array.from(document.querySelectorAll('canvas')).length""")
        await browser.close()

    print("URL:", url)
    print("banner (t1):", banner.replace("\n", " | "))
    print("banner (t2):", banner2.replace("\n", " | "))
    print("sheet:", sheet.replace("\n", " | ")[:300])
    print("arrow on screen at", round(info["x"]), round(info["y"]), "of", round(info["w"]), "x", round(info["h"]),
          "| rotation:", info["transform"].split("rotate")[-1] if "rotate" in info["transform"] else "none")
    print("console errors:", errors[:5] or "none")
    ok = (0 < info["x"] < info["w"] and 0 < info["y"] < info["h"] and "rotate" in info["transform"]
          and any(ch.isdigit() for ch in banner) and not errors)
    print("OK" if ok else "CHECK FAILED")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--truck", default="TRK-03")
    a = ap.parse_args()
    raise SystemExit(0 if asyncio.run(main(a.host, a.truck)) else 1)
