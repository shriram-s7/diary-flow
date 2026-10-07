"""Headless browser check of the sim clock and smooth truck motion (Playwright).

Starts the backend (:8600) and frontend (:5600), loads the default Chennai scenario, then in Chromium:
  1. runs at 1x for 60 real seconds: the sim clock must advance 60 s (+-2 %), the on-screen clock must read
     HH:MM:SS, and no truck marker may jump;
  2. switches to 120x and runs to completion: every stop's actual arrival must match its planned ETA within
     2 % of the planned travel time from DISPATCH_START, and the largest distance any truck marker moved in
     one animation frame (logged by the page as [motion] ...) must stay under 150 m.

Needs internet the first time (OSRM, map tiles); routes come from backend/route_cache/ after that. Run from
the repo root:
    backend/venv/Scripts/python e2e/motion_smoothness.py          (Windows; bin/python on macOS/Linux)
"""
import json
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

from map_accuracy import API, UI, start_servers, stop_servers

DISPATCH_START = 180
ONE_X_SECONDS = 60
MAX_JUMP_M = 150
ETA_TOLERANCE = 0.02
COMPLETE_TIMEOUT_S = 600

JS_MOTION = "() => { const m = window.__dairyflow.motion; return { maxJumpM: m.maxJumpM, truck: m.maxJumpTruck, at: m.maxJumpAt, frames: m.frames, perTruck: m.perTruck } }"
JS_RESET = "() => window.__dairyflow.resetMotion()"
JS_FPS = """() => new Promise(resolve => { let n = 0; const t0 = performance.now();
    const f = () => { n++; if (performance.now() - t0 < 2000) requestAnimationFrame(f); else resolve(n / 2) }
    requestAnimationFrame(f) })"""


def state():
    return requests.get(API + "/state", timeout=10).json()


def main():
    procs = start_servers()
    report = {"checks": [], "errors": []}

    def check(name, ok, detail):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + ": " + detail, flush=True)

    try:
        requests.post(API + "/scenarios/chennai-default/load").raise_for_status()
        plan = requests.post(API + "/plan", timeout=120).json()
        planned = {(tp["truck_id"], s["hub_id"]): s["eta"] for tp in plan["truck_plans"] for s in tp["stops"]}
        requests.post(API + "/simulation/speed", json={"speed": 1}).raise_for_status()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-background-timer-throttling",
                                                              "--disable-renderer-backgrounding"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("console", lambda m: m.text.startswith("[motion]") and print("  page:", m.text, flush=True))
            page.on("pageerror", lambda e: report["errors"].append(str(e)))
            page.goto(UI)
            page.wait_for_function("() => window.__dairyflow && window.__dairyflow.map.loaded()", timeout=60000)
            print(f"  browser animation rate: {page.evaluate(JS_FPS):.0f} fps", flush=True)

            # 1. Real time at 1x.
            requests.post(API + "/simulation/start", timeout=120).raise_for_status()
            page.evaluate(JS_RESET)
            sim0, real0 = state()["sim_time"], time.monotonic()
            time.sleep(ONE_X_SECONDS)
            sim1, real1 = state()["sim_time"], time.monotonic()
            sim_s, real_s = (sim1 - sim0) * 60, real1 - real0
            check("1x clock is real time", abs(sim_s - real_s) <= 0.02 * real_s,
                  f"{sim_s:.2f} sim s in {real_s:.2f} real s")
            shown = page.get_by_test_id("sim-clock").inner_text()
            check("clock shows HH:MM:SS", re.fullmatch(r"\d\d:\d\d:\d\d", shown), shown)
            motion = page.evaluate(JS_MOTION)
            check("1x: no marker jumps", motion["maxJumpM"] < MAX_JUMP_M,
                  f"max per-frame jump {motion['maxJumpM']:.1f} m over {motion['frames']} truck-frames")

            # 2. 120x to completion.
            requests.post(API + "/simulation/speed", json={"speed": 120}).raise_for_status()
            page.evaluate(JS_RESET)
            deadline = time.monotonic() + COMPLETE_TIMEOUT_S
            world = state()
            while world["phase"] != "COMPLETED" and time.monotonic() < deadline:
                time.sleep(1)
                world = state()
            check("120x run completes", world["phase"] == "COMPLETED", f"phase {world['phase']}")

            worst = (0.0, None)
            for truck in world["trucks"]:
                for stop in truck["plan"]:
                    eta = planned[(truck["id"], stop["hub_id"])]
                    rel = abs(stop["arrived_at"] - eta) / (eta - DISPATCH_START)
                    worst = max(worst, (rel, f"{truck['id']} at {stop['hub_id']}: planned {eta:.2f}, "
                                              f"arrived {stop['arrived_at']:.2f} min"))
            check("arrivals match planned ETAs within 2 %", worst[0] <= ETA_TOLERANCE,
                  f"worst {worst[0] * 100:.3f} % ({worst[1]})")

            motion = page.evaluate(JS_MOTION)
            check(f"120x: max per-frame marker jump < {MAX_JUMP_M} m", motion["maxJumpM"] < MAX_JUMP_M,
                  f"{motion['maxJumpM']:.1f} m ({motion['truck']}, {motion['at']}) over {motion['frames']} truck-frames; "
                  f"per truck {json.dumps({k: round(v, 1) for k, v in motion['perTruck'].items()})}")
            check("no page errors", not report["errors"], "; ".join(report["errors"][:3]) or "none")
            browser.close()
    finally:
        stop_servers(procs)

    failed = [c for c in report["checks"] if not c["ok"]]
    print(f"\n{len(report['checks']) - len(failed)}/{len(report['checks'])} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
