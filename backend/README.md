# DairyFlow — Backend

FastAPI service that plans, simulates and re-plans the plant → hub milk run. Domain spec: `../CLAUDE.md`.
Overview, formulas and demo flow: `../README.md`.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (source venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
python main.py                   # http://127.0.0.1:8600, reloads on *.py changes (API docs at /docs)
python main.py --no-reload       # demo mode: never restarts
python -m pytest                 # tests; offline (OSRM replaced by straight-line routes)
```

Port and host come from `DAIRYFLOW_API_PORT` (default 8600) and `DAIRYFLOW_API_HOST` (default 127.0.0.1);
the UI port for CORS from `DAIRYFLOW_UI_PORT` (default 5600). The server starts empty (the plant and the default fleet, no hubs); load a scenario or add hubs. All state is in memory; restarting the server resets it.

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Every constant and the default scenario. The single source of truth; served by `GET /config`. |
| `models.py` | Pydantic models: Plant, Hub, Truck, Stop, Decision, DispatchPlan, Recommendation, WorldState, … |
| `routing.py` | OSRM road routes with straight-line fallback, the route cache, haversine. |
| `timing.py` | The only ETA / service time / slack / risk / speed implementation. |
| `planner.py` | Dispatch planner (fewest trucks, cheapest insertion, splits, 2-opt) and the load rule. |
| `simulation.py` | Executes the stored plan on the simulated clock (one background thread). |
| `control_tower.py` | Live demand, window and priority changes; options A/B/C; recommendations and approvals. |
| `state.py` | The world, the one lock shared by the API and the simulation thread, scenario and CSV loading. |
| `api.py` | HTTP endpoints. `main.py` is the entrypoint. |

Thread safety: every read or change of the world holds `state.lock`; network calls (OSRM) are made outside it.

## API

| Method & path | Body | What it does |
|---|---|---|
| `GET /config` | | Constants from `config.py` |
| `GET /state` | | The whole world (hubs, trucks with stops, decisions, impacts, plan, recommendations, summary) |
| `GET /scenarios` | | Scenarios for the start screen |
| `POST /scenarios/{id}/load` | | Load a scenario (e.g. `chennai-default`) |
| `POST /scenario/empty` | | Plant + default fleet, no hubs |
| `GET /snap?lat=&lon=` | | Nearest road point for a click; `ok: false` with "No road access here — pick a point on a road" if > 300 m |
| `POST /upload/hubs` | CSV `id,name,lat,lon,demand_l,window_end` | Replace hubs (window as `HH:MM` or minutes); every row validated and snapped to a road |
| `POST /upload/trucks` | CSV `id,capacity_l,speed_kmph` | Replace the fleet |
| `POST /plan`, `GET /plan` | | Compute and store / read the DispatchPlan (nothing moves) |
| `POST /simulation/start` | | Execute the plan (plans first if needed; prefetches road routes) |
| `POST /simulation/{pause,resume,stop}` | | Idempotent controls |
| `POST /simulation/reset` | | Back to just before the last start (same scenario and plan) |
| `POST /simulation/speed` | `{speed}` | Multiplier on 1 simulated minute per real second |
| `GET /summary` | | Results once every truck is back at the plant |
| `POST /hubs/manual` | `{name, lat, lon, demand_l, window_end?, priority?}` | Add a hub at its road point. Name 3–30 chars, unique; demand 100–20,000 L; window 03:30–08:00; must be ≤ 300 m from a road. Replans before start; a demand spike during the run |
| `PUT /hubs/{id}/demand` | `{demand_l}` | Replan before start; control tower during the run |
| `PUT /hubs/{id}/window` | `{window_end}` (minutes from midnight) | Replan before start; reports newly late stops during the run |
| `PUT /hubs/{id}/priority` | `{priority: Normal\|High\|Critical}` | Critical moves the hub next on its truck if no window breaks |
| `DELETE /hubs/{id}` | | Before start only; replans |
| `GET/PUT /settings` | `{control_mode: auto\|approve}` | Apply spike recommendations at once, or wait for approval |
| `GET /recommendations` | | All recommendations |
| `POST /recommendations/{id}/{approve,reject}` | | Approve (re-evaluated with current positions) or reject |
| `POST /trucks` | `{id, capacity_l}` | Add a truck (before start; replans) |
| `PUT /trucks/{id}`, `DELETE /trucks/{id}` | `{capacity_l}` | Resize / remove a truck (before start; replans) |
| `POST /trucks/{id}/instruction` | `{instruction}` | Message to a driver |
| `POST /trucks/{id}/acknowledge-instruction` | | Driver acknowledges |
| `POST /impacts/rain` | `{lat, lon, radius_km}` | Rain zone |
| `POST /impacts/traffic` | `{truck_id, segment_index}` | Traffic on segment *i* → *i+1* of that truck's current road route |
| `DELETE /impacts/{id}` | | Remove an impact |

Bad input returns **400** with a readable `detail`; unknown ids **404**; actions not allowed in the current phase **409**.

Sample CSVs: `sample_data/hubs.csv`, `sample_data/trucks.csv` (the default scenario).
