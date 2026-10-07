"""Stress test: the backend never freezes, restarts or stops the clock under a burst of disruptions.

Starts its own backend (main.py --no-reload) on a spare port, loads the default scenario, runs it at 120x and
places 10 roadblocks (on trucks' routes), 3 rain zones and 3 traffic jams within 20 real seconds, while
  * polling GET /state continuously (latency),
  * listening to /ws (heartbeat, sim clock),
  * polling GET /health (PID, tick durations, gaps between ticks).
Then it clears every roadblock and checks that BLOCKED trucks resume.

Asserts: every sim tick < 200 ms, /state p95 < 100 ms, the backend PID never changes, the sim clock never stops
(WS sim_time advances in every 1 s window), no WS gap > 1 s.

    backend/venv/Scripts/python e2e/stress_test.py                # real OSRM (disk cache + network)
    backend/venv/Scripts/python e2e/stress_test.py --dead-osrm    # OSRM unreachable: trucks end up BLOCKED
"""
import argparse
import asyncio
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import threading
import time

import requests
import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
PY = sys.executable


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, base):
        self.base = base
        self.state_ms = []
        self.state_errors = []
        self.pids = set()
        self.ws_msgs = []          # (wall, sim_time, phase, paused)
        self.ws_errors = []
        self.health = []
        self.stop = threading.Event()
        self.log_lines = []

    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        self.log_lines.append(line)

    def get(self, path, timeout=10):
        return requests.get(self.base + path, timeout=timeout)

    def post(self, path, timeout=30, **kw):
        return requests.post(self.base + path, timeout=timeout, **kw)

    # --- background probes ----------------------------------------------------------------------------
    def poll_state(self):
        while not self.stop.is_set():
            t0 = time.perf_counter()
            try:
                r = self.get("/state", timeout=10)
                r.raise_for_status()
                self.state_ms.append((time.perf_counter() - t0) * 1000)
            except Exception as e:
                self.state_errors.append(repr(e))
            time.sleep(0.05)

    def poll_health(self):
        while not self.stop.is_set():
            try:
                h = self.get("/health", timeout=10).json()
                self.pids.add(h["pid"])
                self.health.append(h)
            except Exception as e:
                self.state_errors.append("health " + repr(e))
            time.sleep(0.5)

    def watch_ws(self):
        async def main():
            url = self.base.replace("http", "ws") + "/ws"
            async with websockets.connect(url, max_size=None) as ws:
                while not self.stop.is_set():
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    hb = msg["heartbeat"]
                    self.pids.add(hb["pid"])
                    self.ws_msgs.append((time.time(), hb["sim_time"], msg["phase"], msg["paused"]))
        try:
            asyncio.run(main())
        except Exception as e:
            if not self.stop.is_set():
                self.ws_errors.append(repr(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dead-osrm", action="store_true", help="point the backend at an unreachable OSRM")
    ap.add_argument("--speed", type=float, default=120)
    ap.add_argument("--after", type=float, default=25, help="seconds to keep watching after the burst")
    args = ap.parse_args()
    random.seed(7)

    port = free_port()
    env = dict(os.environ, DAIRYFLOW_API_PORT=str(port), DAIRYFLOW_API_HOST="127.0.0.1")
    if args.dead_osrm:
        env["DAIRYFLOW_OSRM_URL"] = "http://10.255.255.1:5000"   # non-routable: every request hangs to its timeout
    log_path = os.path.join(ROOT, ".cache", "logs", f"stress_backend{'_dead_osrm' if args.dead_osrm else ''}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    out = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([PY, "main.py", "--no-reload"], cwd=BACKEND, env=env, stdout=out, stderr=subprocess.STDOUT)
    run = Run(f"http://127.0.0.1:{port}")
    try:
        for _ in range(60):
            try:
                run.get("/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        h0 = run.get("/health").json()
        run.log(f"backend pid {h0['pid']} on :{port}, OSRM {h0['osrm']}")

        run.post("/scenarios/chennai-default/load").raise_for_status()
        run.post("/simulation/speed", json={"speed": args.speed}).raise_for_status()
        t0 = time.time()
        run.post("/simulation/start", timeout=120).raise_for_status()
        run.log(f"started at {args.speed:g}x ({time.time() - t0:.1f} s incl. route prefetch)")

        threads = [threading.Thread(target=f, daemon=True) for f in (run.poll_state, run.poll_health, run.watch_ws)]
        for t in threads:
            t.start()

        # Wait for trucks on the road.
        while True:
            w = run.get("/state").json()
            moving = [t for t in w["trucks"] if t["status"] == "EN_ROUTE" and len(t["route"]) - t["route_index"] > 40]
            if len(moving) >= 4:
                break
            time.sleep(0.2)
        run.log(f"{len(moving)} trucks en route at sim {w['sim_time']:.1f}")

        # The burst: 16 disruptions within 20 real seconds, each in its own thread (none waits for another).
        jobs = []
        for i in range(10):
            # Aimed when placed (at 120x trucks drive ~1 km per real second): ahead of a truck on the road.
            jobs.append(("roadblock", {"truck_n": i, "radius_m": random.choice([150, 150, 150, 300])}))
        for _ in range(3):
            t = random.choice(moving)
            p = t["route"][t["route_index"] + 20]
            jobs.append(("rain", {"lat": p["lat"], "lon": p["lon"], "radius_km": random.choice([2, 3, 5])}))
        for _ in range(3):
            t = random.choice(moving)
            p = t["route"][t["route_index"] + 25]
            jobs.append(("traffic", {"lat": p["lat"], "lon": p["lon"]}))
        random.shuffle(jobs)
        placed, place_ms = [], []

        def aim(body):
            w_now = run.get("/state").json()
            # A truck on the road that is not already dealing with a closure (else blocks pile up around it)
            drivers = [t for t in w_now["trucks"] if t["status"] in ("EN_ROUTE", "RETURNING")
                       and not t["reroute_pending"] and len(t["route"]) - t["route_index"] > 25] or \
                [t for t in w_now["trucks"] if t["status"] in ("EN_ROUTE", "RETURNING")
                 and len(t["route"]) - t["route_index"] > 25] or moving
            t = drivers[body.pop("truck_n") % len(drivers)]
            p = t["route"][t["route_index"] + random.randint(10, 20)]
            return dict(body, lat=p["lat"], lon=p["lon"])

        def place(kind, body):
            t1 = time.perf_counter()
            try:
                if kind == "roadblock":
                    body = aim(dict(body))
                r = run.post(f"/impacts/{kind}", json=body, timeout=30)
                r.raise_for_status()
                placed.append((kind, r.json()["id"]))
                place_ms.append((time.perf_counter() - t1) * 1000)
            except Exception as e:
                run.state_errors.append(f"place {kind}: {e!r}")

        burst_start = time.time()
        placers = []
        for i, (kind, body) in enumerate(jobs):
            delay = burst_start + i * (19.0 / len(jobs)) - time.time()
            if delay > 0:
                time.sleep(delay)
            th = threading.Thread(target=place, args=(kind, body), daemon=True)
            th.start()
            placers.append(th)
        for th in placers:
            th.join(40)
        run.log(f"placed {len(placed)}/16 impacts in {time.time() - burst_start:.1f} s "
                f"(POST max {max(place_ms):.0f} ms)" if place_ms else "no impacts placed")

        time.sleep(args.after)
        w = run.get("/state").json()
        blocked = [t["id"] for t in w["trucks"] if t["status"] == "BLOCKED"]
        kinds = {}
        for d in w["decisions"]:
            kinds[d["type"]] = kinds.get(d["type"], 0) + 1
        run.log(f"after burst: sim {w['sim_time']:.1f}, phase {w['phase']}, paused {w['paused']}, "
                f"BLOCKED {blocked}, open alerts {sum(a['status'] == 'OPEN' for a in w['alerts'])}")
        run.log(f"impacts active: {len(w['impacts'])}; decisions: "
                + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        for d in [d for d in w["decisions"] if d["type"] in ("AUTO_REROUTE", "NO_ROUTE", "DETOUR_ALERT")][:12]:
            det = d.get("details") or {}
            run.log(f"   {d['type']:<13} {d['summary'][:110]}  [search {det.get('elapsed_sec')} s, "
                    f"{det.get('failed_requests')}/{det.get('requests')} failed]")

        # Clear every roadblock: BLOCKED trucks must resume at once.
        for kind, iid in placed:
            if kind == "roadblock":
                requests.delete(run.base + "/impacts/" + iid, timeout=10)
        w2 = run.get("/state").json()
        still = [t["id"] for t in w2["trucks"] if t["status"] == "BLOCKED"]
        run.log(f"roadblocks cleared: BLOCKED before {blocked} -> now {still}")
        time.sleep(5)
    finally:
        run.stop.set()
        time.sleep(0.5)
        h_end = None
        try:
            h_end = run.get("/health").json()
        except Exception as e:
            run.state_errors.append("final health " + repr(e))
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()

    # --- verdict -----------------------------------------------------------------------------------------
    ticks = h_end["ticks"] if h_end else {}
    lat = sorted(run.state_ms)
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else float("inf")
    exec_msgs = [m for m in run.ws_msgs if m[2] == "EXECUTING" and not m[3]]
    gaps = [b[0] - a[0] for a, b in zip(run.ws_msgs, run.ws_msgs[1:])]
    # Clock check: every 1 s window advances sim_time, and the longest wall-clock time without any advance
    # stays < 0.5 s (two 5 Hz WS messages may fall into one 10 Hz tick and repeat a value).
    stalls, worst_stall = 0, 0.0
    if exec_msgs:
        t_start = exec_msgs[0][0]
        while t_start + 1.0 <= exec_msgs[-1][0]:
            window = [m[1] for m in exec_msgs if t_start <= m[0] < t_start + 1.0]
            if len(window) < 2 or window[-1] <= window[0]:
                stalls += 1
            t_start += 1.0
        last_advance_wall, last_sim = exec_msgs[0][0], exec_msgs[0][1]
        for wall, sim, _, _ in exec_msgs[1:]:
            if sim > last_sim:
                last_sim, last_advance_wall = sim, wall
            worst_stall = max(worst_stall, wall - last_advance_wall)

    results = {
        "tick_ms_max": ticks.get("tick_ms_max"), "tick_ms_p95": ticks.get("tick_ms_p95"),
        "ticks": ticks.get("ticks"), "slow_ticks_over_200ms": ticks.get("slow_ticks"),
        "max_gap_between_ticks_ms": ticks.get("max_gap_between_ticks_ms"), "tick_errors": ticks.get("errors"),
        "state_requests": len(lat), "state_p50_ms": round(statistics.median(lat), 1) if lat else None,
        "state_p95_ms": round(p95, 1), "state_max_ms": round(lat[-1], 1) if lat else None,
        "pids_seen": sorted(run.pids), "ws_messages": len(run.ws_msgs),
        "ws_max_gap_s": round(max(gaps), 3) if gaps else None, "sim_clock_stalled_1s_windows": stalls,
        "sim_clock_longest_no_advance_s": round(worst_stall, 3),
        "errors": run.state_errors[:5] + run.ws_errors[:5],
    }
    run.log("RESULTS " + json.dumps(results, indent=2))
    checks = {
        "every tick < 200 ms": ticks.get("tick_ms_max", 1e9) < 200 and ticks.get("slow_ticks", 1) == 0,
        "/state p95 < 100 ms": p95 < 100,
        "backend PID never changed": len(run.pids) == 1 and h_end is not None and h_end["pid"] in run.pids,
        "sim clock never stopped": stalls == 0 and worst_stall < 0.5 and len(exec_msgs) > 50,
        "no WS gap > 1 s": bool(gaps) and max(gaps) < 1.0,
        "no request/WS errors": not run.state_errors and not run.ws_errors,
    }
    for name, ok in checks.items():
        run.log(f"{'PASS' if ok else 'FAIL'}  {name}")
    report = os.path.join(ROOT, ".cache", "logs", f"stress_result{'_dead_osrm' if args.dead_osrm else ''}.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump({"results": results, "checks": checks, "log": run.log_lines}, f, indent=2)
    run.log(f"report: {report}; backend log: {log_path}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
