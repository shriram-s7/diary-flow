# DairyFlow — project spec

## What it is
DairyFlow plans and simulates the PRIMARY milk distribution layer in Chennai: one processing plant dispatching milk trucks to regional distribution hubs during the early-morning delivery window. It responds live to demand changes. Hub→booth and booth→customer are out of scope; the same engine could be reused there later.

## Domain entities (use these names everywhere: code, API, UI)
- **Plant**: the single source. Madhavaram Plant, lat 13.1414, lon 80.2325 (road point 13.141378, 80.232914). Has `milk_available_l`.
- **Hub**: a regional distribution hub with demand in LITRES. Fields: `id`, `name`, `lat`, `lon`, `road_lat`, `road_lon`, `snap_distance_m`, `demand_l`, `delivered_l`, `remaining_l` (= `demand_l - delivered_l`), `window_end` (minutes from midnight), `priority` (Normal / High / Critical), `status`.
- **Truck**: refrigerated milk truck. Fields: `id`, `capacity_l`, `load_l` (milk on board), `speed_kmph`, `fuel_km_per_l`, `cost_per_km`, `status`, `plan` (ordered stops), `position`, `route_id`, `distance_along_route_m`.
- **Stop**: `{hub_id, drop_l, eta, window_end, slack_min, risk, delivered_l, arrived_at, completed_at, unload_min_left}`. One truck can have several stops. A big hub can be split across trucks.
- **Decision**: log entry `{time, truck_id or "SYSTEM", type, summary, details}`.

## Default Chennai scenario
The app opens empty (plant + fleet, no hubs); this is the `chennai-default` scenario on the start screen.
Hubs (demand in litres, all `window_end` 06:00). Lat/lon = area centre checked with Nominatim; road point = OSRM /nearest:

| Hub | Lat | Lon | Road lat | Road lon | Snap (m) | Demand (L) |
|---|---|---|---|---|---|---|
| Ambattur | 13.1056 | 80.1640 | 13.106207 | 80.164089 | 68.2 | 3200 |
| Anna Nagar | 13.0882 | 80.2073 | 13.088003 | 80.207287 | 22.0 | 2800 |
| T. Nagar | 13.0378 | 80.2318 | 13.038006 | 80.231856 | 23.7 | 3500 |
| Adyar | 13.0063 | 80.2574 | 13.006295 | 80.257423 | 2.6 | 2600 |
| Velachery | 12.9802 | 80.2229 | 12.980085 | 80.222902 | 12.8 | 3000 |
| Porur | 13.0325 | 80.1583 | 13.032556 | 80.157992 | 33.9 | 2400 |
| Tambaram | 12.9245 | 80.1151 | 12.924430 | 80.115102 | 7.8 | 3100 |
| Tiruvottiyur | 13.1586 | 80.2962 | 13.158522 | 80.296275 | 11.9 | 1800 |

Fleet: TRK-01 6000 L, TRK-02 6000 L, TRK-03 5000 L, TRK-04 4000 L, TRK-05 4000 L, TRK-06 3000 L (reserve-sized)

Plant milk available: 26,000 L

## Single source of truth for constants
Defined in `backend/config.py`; the frontend reads them from `GET /config`.

- `DISPATCH_START` = 03:00 (180 min from midnight)
- `DEFAULT_WINDOW_END` = 06:00
- `CITY_SPEED_KMPH` = 30 (one speed for planning, ETA and simulation; rain multiplies it by 0.6, traffic segments by 0.5)
- `DOCK_TIME_MIN` = 10, `UNLOAD_RATE_L_PER_MIN` = 100 → `service_min = 10 + drop_l / 100`
- `LOADING_TIME_MIN` = 5 (loading at the plant, finishing at `DISPATCH_START`)
- `LOAD_BUFFER_PCT` = 5 (a truck loads planned drops + 5%, capped at capacity, as spare for spikes)
- `FUEL_KM_PER_L` = 5, `COST_PER_KM` = 18 (₹)
- **Simulation clock** (`SIM_SECONDS_PER_REAL_SECOND = 1.0`): 1× = real time (1 sim second per real second). Speed presets: `SIM_SPEED_PRESETS = [1, 5, 15, 30, 60, 120]` shown as segmented buttons. Backend ticks at `SIM_TICK_HZ = 10` Hz; each tick advances by `SIM_UPDATE_INTERVAL_SEC = 0.1 s × speed`. Motion is integrated in sub-steps of `MAX_SUBSTEP_SIM_SEC = 5.0` simulated seconds so 120× stays accurate. Clock is displayed as HH:MM:SS.
- **Live transport**: `/ws` WebSocket pushes truck motion at `WS_PUSH_HZ = 5` Hz; full world snapshot every `WS_WORLD_EVERY = 5` messages (~1 Hz, last `WS_MAX_DECISIONS = 100` decisions). `GET /state` is kept for tests. Each message has `seq` and `server_time` (wall time of the tick the state is from); each truck sends `route_id`, `distance_along_m`, `speed_mps` (current, rain/traffic included), `heading`, `status`, `approximate`. Route geometry comes from `GET /routes/{route_id}` (`[lon, lat]` coordinates + cumulative metres); the id is derived from the geometry, so a reroute always gets a new id.
- **Client motion easing**: `MOTION.EASE_MS = 300` (position correction), `MOTION.TELEPORT_M = 500` (jump threshold), `MOTION.HEADING_EASE_MS = 250`, `MOTION.REROUTE_FADE_MS = 1000` (old route fade), `MOTION.REROUTE_DRAW_MS = 1500` (new route draw).
- `MAX_SIM_SPEED` = 3600 (API clamps to this; tests run faster than the UI allows)
- Risk from `slack_min = window_end - eta` (ONE function, used everywhere): ≥30 Low, 10–30 Medium, 0–10 High, <0 Late

## Route reliability
- Every OSRM response (route, nearest, distance table) is cached on disk (`.cache/route_cache/` at the repo root — outside `backend/` so the dev reloader never sees it — keyed by rounded coordinates + options, SHA-1 named). A demo that worked once works again without the network.
- Timeouts: `OSRM_TIMEOUT_SEC = 3` per request; a detour search has `DETOUR_BUDGET_SEC = 4` in total; snapping a clicked point `NEAREST_BUDGET_SEC = 3`. `DAIRYFLOW_OSRM_URL` overrides the server (the stress test points it at a dead address).
- Failed requests are retried `OSRM_RETRIES = 2` times with `OSRM_BACKOFF_SEC = 0.5` s exponential backoff.
- Only after all retries fail does it fall back to a straight-line route, marked `approximate = True`. Approximate routes are never written to disk so the next run retries OSRM.
- The UI shows "⚠ approximate route" on trucks with a fallback route.

## Planner uses road distances
Before planning, one OSRM `/table` request fetches road distances between the plant and every hub. `_Planner.km()` and the planning ETAs (`timing.route_etas`) use `routing.road_km()` (the leg's road route if fetched → OSRM table → straight line), so tour-km and ETAs reflect the roads actually driven. Table and route distances agree within ~0.4% for the default scenario.

## Core rules
1. Planning objective, in strict order: (a) cover all demand within plant stock, (b) use the fewest trucks, (c) minimise total km. Unused trucks stay at the plant as RESERVE and the UI says why.
2. Capacity is a hard constraint. `window_end` is a hard constraint where possible; any violation is reported, never hidden.
3. Delivering reduces `remaining_l` by `drop_l`. A hub is COMPLETED only when `remaining_l == 0`.
4. A truck waits at a hub for its service time, then continues to the next stop, then returns to the plant. All driving, including the return, uses fuel.
5. Every ETA, slack and risk value comes from one shared backend function. The frontend displays backend numbers and never recomputes them.
6. Display time as HH:MM clock time (hub windows, ETAs) or HH:MM:SS (running sim clock).

## Accuracy rules
1. **Pins and markers.** The plant and hubs are drawn from a GeoJSON source with a symbol layer (icons registered with `map.addImage`, `icon-anchor: "bottom"` so the pin tip is the coordinate); clicks use layer events. Never set `style.transform` or any positioning style (`position`, `top`, `left`, `margin`) on a MapLibre marker's root element: MapLibre positions markers through that transform. DOM markers are only for moving trucks. The MapLibre stylesheet is imported once in JS (no `<link>` in HTML).
2. **Coordinates.** Every scenario coordinate is checked with Nominatim ("<name>, Chennai") and kept within 300 m of the area's centre. Every hub and the plant is snapped to the road network with OSRM `/nearest` and stores both the raw `(lat, lon)` and the snapped `(road_lat, road_lon)` plus `snap_distance_m`. Routes start and end at road points, trucks stop exactly there, and map pins are drawn there.
3. **Creating hubs.** A clicked point is snapped at once; if `snap_distance_m > 300` (water, marsh, airport, no road) the hub is rejected with "No road access here — pick a point on a road". Name 3–30 characters and unique; demand 100–20,000 L; `window_end` between `DISPATCH_START` + 30 min and 08:00. The backend validates; the UI shows its message.
4. **Live numbers.** Every future stop's ETA/slack/risk is recomputed on every tick by `timing.py`. Once a truck arrives the stop shows "Unloading — 640/1,500 L, 6 min left" (`delivered_l` rises continuously while unloading), then "Delivered 05:12". An ETA in the past is never displayed.
5. **Split hubs.** A hub served by several trucks lists each delivery ("TRK-03 1,500 L @ 04:16 · TRK-05 1,600 L @ 04:56"); hub-level risk is the worst of its stops. The manager table, hub popup and driver view show the same numbers for the same stop (all from `hub.deliveries` / the truck's `Stop`).
6. **Stop order.** Each truck's stops are ordered for the shortest closed tour plant → stops → plant (every order for ≤ 6 stops, else 2-opt); equal-length orders (e.g. a tour and its reverse) go to the one that delivers earlier; windows are checked on the final order.
7. **Start empty.** The app opens with only the plant and the fleet editor; the start screen offers "Start empty" or a scenario.
8. **Smooth client motion.** A truck's drawn position is a distance along its route polyline (never a free lat/lon). Between WS messages it advances with `requestAnimationFrame` at `speed_mps × sim_speed`; a message's distance (extrapolated from `server_time`) is compared with the client's and the difference eased in along the road over 300 ms; teleport only if the error is > 500 m. The truck icon rotates to the route bearing, eased over 250 ms. The leg's travelled part is drawn faded and the remaining part solid (one line, `line-gradient` over `line-progress`). Reroute: the old remaining route turns red-dashed and fades out over 1 s while the new one draws from the truck over 1.5 s; if the truck is followed the camera eases to fit the new route.

## Live demand change ("control tower")
When a hub's demand changes during execution:
- **Increase**: shortage = new remaining − milk already committed to that hub. Evaluate options: (A) add a stop on a truck already out with spare load, inserted after its current stop; (B) dispatch a reserve truck from the plant; (C) a combination. Pick by: covers most litres → meets window → least extra km. Log a Decision listing every option evaluated and why the winner won. A setting chooses auto-apply or manager approval.
- **Decrease**: reduce the planned drop; extra milk stays on the truck and the UI shows it as returned/available.
- **Priority Critical**: the owning truck serves that hub next, if its current stop is complete and the windows of the others still hold; otherwise explain why not.

## Disruptions
Three impact types placed by clicking the map, snapped to the nearest road, shown with distinct icons and a map legend:
- **Rain zone**: circle (radius 1–8 km), speed × 0.6 inside.
- **Traffic jam**: road stretch of ~1 km on clicked road, speed × 0.4 on segments within 50 m of it.
- **Roadblock**: point closure (150 m radius) or area closure (radius up to 2 km); road is impassable.
Clearing an impact removes it; clearing a roadblock immediately lets BLOCKED trucks resume (on their own route if it is clear now, else a fresh one).

### Roadblock detection
- A truck learns about a roadblock `ROADBLOCK_DETECT_DELAY_MIN` = 5 sim minutes after placement (sim time, so the same at 1× and 120×), or earlier once it is within `ROADBLOCK_PROXIMITY_DETECT_M` = 300 m of it.
- Only trucks whose remaining route passes within the roadblock radius are affected. Future legs and new plans strictly avoid active blocks (a cached leg that crosses one is replaced by a detour search).
- On detection the truck keeps driving on its current route while the detour is searched in the background, up to a hold point (where it will be after `DETOUR_BUDGET_SEC` of real time at the current speed, never past the block and, when possible, 300 m short of it). If it reaches the hold point or the block boundary first, it stops there and waits for the result. It never drives into a block.

### Finding a detour (OSRM + ORS) — `routing.search_detour`, in worker threads
1. In parallel: ORS `avoid_polygons` (if `OPENROUTESERVICE_API_KEY` is set) and OSRM `alternatives=true`; take the shortest that avoids every active block.
2. If none, via points around the blocks the direct routes hit: 8 bearings × 1.5 / 3 / 5 km, one OSRM request start → via → destination each, all in parallel; take the shortest that avoids every active block.
3. Everything shares the 4 s budget. Result: `ok`, `no_route` (OSRM answered, nothing avoids the blocks) or `timeout` (too many requests failed or were late to tell). Every response is cached.

### Decision rule (replaces the old NEEDS_APPROVAL / HOLDING flow)
- **Detour found → reroute immediately, always** (`AUTO_REROUTE` decision with extra km / min / % and the search stats). Driver: "Road closed ahead — rerouting (+X min)". Manager: log entry + toast "TRK-XX rerouted around closure on <road>: +X.X km, +X min, all windows OK".
- **Costly detour** (> `DETOUR_MAX_EXTRA_MIN` = 15 min, or > `DETOUR_MAX_EXTRA_KM_PCT` = 30 % extra km, or it makes a stop late): the truck still takes it; the manager additionally gets a `DETOUR_COSTLY` alert card with the numbers and options: hand the late stop to another truck/reserve (control tower, the truck itself excluded), skip the stop and notify the hub, or keep the detour (default — nothing happens if the card is ignored).
- **No detour** (`no_route`), or the search times out `DETOUR_MAX_ATTEMPTS` = 2 times → the truck stops safely (`status = BLOCKED`) and the manager gets a red `NO_ROUTE` alert: "TRK-XX cannot reach <Hub> — all routes closed" (or "— the route search timed out 2×") with Call driver (driver name/phone) / Return to plant / Reassign stop / Wait for clearance. Driver: "Stop safely — no route available. Manager notified."
- **Unreachable hubs**: after a `no_route`, a background search from the plant decides whether the hub is `UNREACHABLE` (its remaining litres flagged as at risk); clearing a block resets it.
- Alerts live in `world.alerts` (`POST /decisions/resolve {truck_id, action, alert_id?}`). **The simulation never pauses by itself — only the Pause button pauses it.** There is no "pause on alerts" setting.

### Never block (process and threads)
- The sim tick never does network I/O and never waits: routes and detours are `routing.submit()`-ed to worker threads (`disruptions.py`) and installed by a later tick. Each tick is wrapped in try/except: a failing tick is logged with its traceback and a SYSTEM `SIM_ERROR` decision, and the clock keeps running. `GET /health` reports the PID, tick durations (p95/max/slow ticks), the longest gap between ticks and errors.
- No network I/O under `state.lock`; endpoints that block are plain `def`; the async `/ws` handler builds messages in a worker thread. Every `/ws` message carries `heartbeat: {wall_time, sim_time, pid, tick}`.
- Frontend: `/ws` reconnects with backoff (0.5 → 8 s); after 3 s without a message a small "Reconnecting…" banner shows and the last state stays on screen.

### Rain and traffic live updates
Rain and traffic dynamically adjust speeds, recalculating ETAs and risks live on each tick. If any stop's risk escalates to High or Late, the control tower re-evaluates permutations of that truck's remaining stops and proposes/applies a resequence according to `control_mode`.

## Tech
FastAPI backend (`backend/`), React + Vite + MapLibre frontend (`frontend/`), OSRM public server for road routes with disk-cached responses and straight-line fallback (fallback marked `approximate`). WebSocket (`/ws`) for live truck-motion push at 5 Hz.

**Running:** `start.bat` (Windows) / `./start.sh` (macOS/Linux) start the backend on `127.0.0.1:8600`, wait for `/health`, then start Vite on `:5600` (all interfaces) with a proxy for `/api` and `/ws` (`ws: true`) to that same address — browsers and phones only ever talk to Vite. Dev mode reloads the backend on `backend/*.py` only (venv/ and tests/ excluded); `--no-reload` is the demo mode. Logs: `.cache/logs/backend.log`.

**Driver view (phone):** `http://<pc-lan-ip>:5600/driver.html?truckId=TRK-03`. Heading-up follow map with a navigation arrow placed by distance along the truck's route, the remaining route in blue (driven part grey), the next stop and the final stop marked, and a turn-by-turn banner (distance + "Turn right onto <street>" + "Then …") from `GET /routes/{id}/directions` (manoeuvres computed on the route the truck actually drives, street names from OSRM /nearest). ETAs come from the backend `Stop`s.

## Test
Run the default scenario at 1× for 60 s and at 120× to completion. Confirm:
- Truck arrival times in sim match the planned ETAs within 2% of the planned travel time from `DISPATCH_START` (both use road distances). At 1× for 60 s the trucks are still loading (02:55–03:00), so that run checks the clock rate (60 sim s ± 2%) and marker smoothness only.
- No marker jumps: max per-frame position delta < 150 m at 120× (logged in the browser as `[motion] …`, read from `window.__dairyflow.motion`). Run: `backend/venv/Scripts/python e2e/motion_smoothness.py` (set `DAIRYFLOW_API_PORT` / `DAIRYFLOW_UI_PORT` to use other ports).
- Unit/integration tests pass for disruption engine: AUTO_REROUTE, costly detour + alert card (keep / reassign / skip), NO_ROUTE, timeout twice → BLOCKED, truck keeps driving while a detour is searched, detection delay at 1× and 120×, clearing roadblocks, rain ETA impacts, a failing tick does not stop the clock.
- Stress (`backend/venv/Scripts/python e2e/stress_test.py`, and again with `--dead-osrm`): at 120×, 10 roadblocks + 3 rain + 3 traffic within 20 s; every tick < 200 ms, `/state` p95 < 100 ms, backend PID unchanged, sim clock never stops. With OSRM dead, trucks hit the budget and stop as BLOCKED with alerts; nothing freezes.
- Driver view: `backend/venv/Scripts/python e2e/driver_view_check.py --host <lan-ip>` (phone-sized screenshots in `screenshots/driver_*.png`).

