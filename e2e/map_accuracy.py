"""Headless browser check of map accuracy (Playwright).

Starts the backend (:8600) and frontend (:5600), then in Chromium:
  1. "Start empty", tries a hub in the sea (must be rejected), creates 3 hubs by clicking the map;
  2. checks every pin is drawn at its snapped road point (rendered symbol tip, metres and pixels);
  3. generates the plan, dispatches, runs at high speed and checks that every truck, while unloading,
     stands within 30 m of its hub's pin — both the backend position and the on-screen DOM marker;
  4. checks the unloading / delivered wording appears and waits for the summary screen.

Needs internet (OSRM, map tiles). Run from the repo root:
    backend/venv/Scripts/python e2e/map_accuracy.py          (Windows; bin/python on macOS/Linux)
"""
import math
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PORT = int(os.environ.get("DAIRYFLOW_API_PORT", "8600"))  # same variables the servers read
UI_PORT = int(os.environ.get("DAIRYFLOW_UI_PORT", "5600"))
API = f"http://127.0.0.1:{API_PORT}"
UI = f"http://localhost:{UI_PORT}"
TOLERANCE_M = 30
HUBS = [  # clicked points (land, near roads)
    ("Perambur", 13.1180, 80.2340, 2000),
    ("Kilpauk", 13.0825, 80.2425, 3000),
    ("Guindy", 13.0100, 80.2120, 2500),
]
SEA = (13.0500, 80.3300)  # Bay of Bengal


def dist_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def wait_http(url, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if requests.get(url, timeout=2).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


def port_in_use(port):
    import socket
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family) as sock:
            sock.settimeout(1)
            if sock.connect_ex((host, port)) == 0:
                return True
    return False


def start_servers():
    for port in (API_PORT, UI_PORT):
        if port_in_use(port):
            raise SystemExit(f"Port {port} is already in use; stop the running DairyFlow servers first.")
    python = os.path.join(ROOT, "backend", "venv", "Scripts" if os.name == "nt" else "bin", "python")
    backend = subprocess.Popen([python, "main.py"], cwd=os.path.join(ROOT, "backend"),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frontend = subprocess.Popen("npm run dev", cwd=os.path.join(ROOT, "frontend"), shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_http(API + "/")
    wait_http(UI + "/")
    # Warm up Vite (first start pre-bundles dependencies and would reload the page mid-test).
    for path in ("/src/main.jsx", "/src/App.jsx"):
        requests.get(UI + path, timeout=60)
    time.sleep(3)
    return [backend, frontend]


def stop_servers(procs):
    for p in procs:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.terminate()
    if os.name == "nt":
        # The venv python.exe is a launcher whose interpreter is a separate process: also stop whatever
        # this test started that is still listening on its ports (they were free when it began).
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
        pids = {line.split()[-1] for line in out.splitlines()
                if "LISTENING" in line and (f":{API_PORT} " in line or f":{UI_PORT} " in line)}
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)


JS_PROJECT = "([lon, lat]) => { const p = window.__dairyflow.map.project([lon, lat]); return [p.x, p.y] }"
JS_M_PER_PX = ("([lat]) => 40075016.686 * Math.cos(lat * Math.PI / 180) / "
               "(512 * Math.pow(2, window.__dairyflow.map.getZoom()))")
JS_PIN_AT = """([x, y, layer]) => window.__dairyflow.map.queryRenderedFeatures([x, y], { layers: [layer] })
    .map(f => ({ id: f.properties.id, coords: f.geometry.coordinates }))"""
JS_ZOOM_TO = """async ([lon, lat]) => {
    const map = window.__dairyflow.map
    map.jumpTo({ center: [lon, lat], zoom: 16 })
    await new Promise(r => map.once('idle', r))
}"""
JS_SCREEN_WORLD = "() => window.__dairyflow.world"
# One synchronous snapshot of what is on screen: the page's world, the truck marker and the hub pin position.
JS_SAMPLE = """([truckId, hubId]) => {
    const { map, world, truckMarkers } = window.__dairyflow
    const truck = world.trucks.find(t => t.id === truckId)
    const hub = world.hubs.find(h => h.id === hubId)
    const stop = truck && truck.plan.find(s => s.status === 'PENDING')
    if (!truck || truck.status !== 'UNLOADING' || !stop || stop.hub_id !== hubId) return null
    const pin = map.project([hub.road_lon, hub.road_lat])
    const m = truckMarkers[truckId]
    const canvas = map.getCanvas().getBoundingClientRect()
    let marker = null
    if (m) {
        const box = m.getElement().getBoundingClientRect()
        const ll = m.getLngLat()
        marker = { x: box.left + box.width / 2 - canvas.left, y: box.top + box.height / 2 - canvas.top, lat: ll.lat, lon: ll.lng }
    }
    return { position: truck.position, pin: [pin.x, pin.y], marker,
             mPerPx: 40075016.686 * Math.cos(hub.road_lat * Math.PI / 180) / (512 * Math.pow(2, map.getZoom())) }
}"""
def click_map(page, lat, lon):
    x, y = page.evaluate(JS_PROJECT, [lon, lat])
    canvas = page.locator(".maplibregl-canvas").bounding_box()
    page.mouse.click(canvas["x"] + x, canvas["y"] + y)


def main():
    procs = start_servers()
    results = {"pins": [], "trucks": {}, "errors": [], "checks": []}
    try:
        requests.post(API + "/scenario/empty")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: results["errors"].append(f"page error: {e}"))
            page.on("console", lambda m: results["errors"].append(f"console: {m.text}")
                    if m.type == "error" and "Failed to fetch" not in m.text and "tile" not in m.text else None)
            page.goto(UI)
            start_button = page.get_by_role("button", name="Start empty — add hubs on the map")
            try:
                start_button.wait_for(timeout=20000)
            except Exception:
                page.reload()  # a dependency re-bundle reloaded the page during the first load
                start_button.wait_for(timeout=30000)
            start_button.click()
            page.wait_for_function("() => window.__dairyflow && window.__dairyflow.map.isStyleLoaded()")
            page.wait_for_timeout(500)

            # A click in the sea is rejected and creates nothing.
            click_map(page, *SEA)
            page.get_by_text("No road access here — pick a point on a road").wait_for(timeout=15000)
            results["checks"].append(("Sea click rejected with the no-road message", True))

            for name, lat, lon, demand in HUBS:
                click_map(page, lat, lon)
                page.get_by_label("Hub name").wait_for(timeout=15000)
                page.get_by_label("Hub name").fill(name)
                page.get_by_label("Demand in litres").fill(str(demand))
                page.get_by_role("button", name="Add Hub", exact=True).click()
                page.get_by_label("Hub name").wait_for(state="detached", timeout=15000)
            page.wait_for_timeout(1500)
            world = requests.get(API + "/state").json()
            results["checks"].append((f"3 hubs created ({len(world['hubs'])} in backend)", len(world["hubs"]) == 3))

            # Pins: the rendered symbol tip is the hub's road point.
            for hub in world["hubs"]:
                page.evaluate(JS_ZOOM_TO, [hub["road_lon"], hub["road_lat"]])
                x, y = page.evaluate(JS_PROJECT, [hub["road_lon"], hub["road_lat"]])
                above = page.evaluate(JS_PIN_AT, [x, y - 8, "hubs"])
                below = page.evaluate(JS_PIN_AT, [x, y + 10, "hubs"])
                hit = next((f for f in above if f["id"] == hub["id"]), None)
                pin_m = dist_m(hub["road_lat"], hub["road_lon"], hit["coords"][1], hit["coords"][0]) if hit else None
                results["pins"].append({
                    "name": hub["name"], "click_to_road_m": hub["snap_distance_m"], "pin_to_road_m": pin_m,
                    "pin_body_above_tip": hit is not None, "nothing_below_tip": not any(f["id"] == hub["id"] for f in below),
                    "m_per_px": page.evaluate(JS_M_PER_PX, [hub["road_lat"]]),
                })

            page.get_by_role("button", name="Generate Today's Dispatch Plan").click()
            page.get_by_role("button", name="🚚 Dispatch").click()
            page.get_by_text("Live —").wait_for(timeout=60000)
            requests.post(API + "/simulation/speed", json={"speed": 10})

            hubs = {h["id"]: h for h in world["hubs"]}
            saw_unloading_text = False
            deadline = time.time() + 240
            while time.time() < deadline:
                if requests.get(API + "/state").json()["phase"] == "COMPLETED":
                    break
                # Measure what the page is showing: its own copy of the world and its own markers.
                w = page.evaluate(JS_SCREEN_WORLD)
                for t in w["trucks"]:
                    if t["status"] != "UNLOADING":
                        continue
                    stop = next(s for s in t["plan"] if s["status"] == "PENDING")
                    hub = hubs[stop["hub_id"]]
                    page.evaluate(JS_ZOOM_TO, [hub["road_lon"], hub["road_lat"]])
                    sample = page.evaluate(JS_SAMPLE, [t["id"], hub["id"]])
                    if sample is None:
                        continue  # it finished unloading while the map was zooming
                    rec = results["trucks"].setdefault((t["id"], hub["name"]), {"samples": 0, "backend_m": 0, "marker_m": 0,
                                                                                "marker_px": 0, "marker_screen_m": 0})
                    rec["samples"] += 1
                    pos = sample["position"]
                    rec["backend_m"] = max(rec["backend_m"], dist_m(pos["lat"], pos["lon"], hub["road_lat"], hub["road_lon"]))
                    marker = sample["marker"]
                    if marker is None:
                        rec["marker_m"] = rec["marker_screen_m"] = float("inf")  # unloading truck not drawn
                        continue
                    screen_px = math.hypot(marker["x"] - sample["pin"][0], marker["y"] - sample["pin"][1])
                    rec["marker_m"] = max(rec["marker_m"], dist_m(marker["lat"], marker["lon"], hub["road_lat"], hub["road_lon"]))
                    rec["marker_px"] = max(rec["marker_px"], screen_px)
                    rec["marker_screen_m"] = max(rec["marker_screen_m"], screen_px * sample["mPerPx"])
                if page.get_by_text("Unloading —").count() > 0:
                    saw_unloading_text = True
                page.wait_for_timeout(250)
            page.get_by_text("Dispatch complete").wait_for(timeout=30000)
            results["checks"].append(("'Unloading — x/y L, n min left' shown during the run", saw_unloading_text))
            summary = requests.get(API + "/summary").json()
            results["checks"].append((f"All demand delivered ({summary['total_delivered_l']:,.0f}/{summary['total_demand_l']:,.0f} L)",
                                      summary["total_delivered_l"] == summary["total_demand_l"]))
            page.screenshot(path=os.path.join(ROOT, "e2e", "summary.png"))
            browser.close()
    finally:
        stop_servers(procs)

    ok = True
    print("\nPINS (rendered symbol vs snapped road point)")
    print(f"  {'hub':<10}{'click→road':>11}{'pin→road':>10}  tip anchored  m/px at zoom")
    for p in results["pins"]:
        good = p["pin_to_road_m"] is not None and p["pin_to_road_m"] <= TOLERANCE_M and p["nothing_below_tip"]
        ok &= good
        pin = f"{p['pin_to_road_m']:.2f} m" if p["pin_to_road_m"] is not None else "NOT FOUND"
        print(f"  {p['name']:<10}{p['click_to_road_m']:>9.1f} m{pin:>10}  {'yes' if p['pin_body_above_tip'] and p['nothing_below_tip'] else 'NO':<12}  "
              f"{p['m_per_px']:.1f}   {'PASS' if good else 'FAIL'}")
    print("\nTRUCKS WHILE UNLOADING (max distance from the hub pin)")
    print(f"  {'truck @ hub':<20}{'samples':>8}{'backend':>10}{'marker':>10}{'on screen':>16}")
    for (tid, hub), r in sorted(results["trucks"].items()):
        good = r["backend_m"] <= TOLERANCE_M and r["marker_m"] <= TOLERANCE_M and r["marker_screen_m"] <= TOLERANCE_M
        ok &= good
        print(f"  {tid + ' @ ' + hub:<20}{r['samples']:>8}{r['backend_m']:>8.2f} m{r['marker_m']:>8.2f} m"
              f"{r['marker_px']:>6.1f} px={r['marker_screen_m']:.1f} m  {'PASS' if good else 'FAIL'}")
    if not results["trucks"]:
        ok = False
        print("  no unloading truck was observed")
    print("\nCHECKS")
    for text, good in results["checks"]:
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {text}")
    print("\nBROWSER ERRORS:", results["errors"] or "none")
    ok &= not results["errors"]
    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
