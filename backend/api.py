"""HTTP + WebSocket API. Every handler that touches the world holds state.lock.

Rules that keep the server responsive (see CLAUDE.md "Never block"):
  * Handlers that do blocking work (the lock, OSRM) are plain `def` (FastAPI runs them in its thread pool).
  * Network I/O (road snapping, route prefetch) never happens while holding state.lock.
  * The async /ws handler never takes the lock on the event loop: it builds each message in a worker thread.
"""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import control_tower
import disruptions
import routing
import simulation
import state
from models import (
    DecisionResolveRequest, Hub, HubCreateRequest, HubDemandUpdate, HubPriorityUpdate, HubWindowUpdate,
    InstructionRequest, RainImpact, RainImpactCreate, RoadblockImpact, RoadblockImpactCreate, Settings,
    SimPhase, SnapResult, SpeedRequest, TrafficImpact, TrafficImpactCreate, TruckCreate, TruckStatus,
    TruckUpdate, WorldState,
)
from planner import plan_world
from simulation import simulator
from timing import update_all_etas


log = logging.getLogger("dairyflow.api")
STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _reset_to(state.empty_world())  # the plant and the fleet; the user picks "Start empty" or a scenario
    yield
    simulator.stop()


app = FastAPI(title="DairyFlow", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.UI_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Bad input is a 400 with a readable message, e.g. "demand_l: Input should be greater than or equal to 0"."""
    problems = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err["loc"] if x != "body")
        problems.append(f"{field}: {err['msg']}" if field else err["msg"])
    return JSONResponse(status_code=400, content={"detail": "; ".join(problems)})


def _control(fn, *args):
    """Run a control-tower / disruption action, turning its errors into HTTP errors."""
    try:
        return fn(*args)
    except (control_tower.ControlError, disruptions.ActionError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


def _get_hub(hub_id: str) -> Hub:
    hub = state.world.hub(hub_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")
    return hub


def _get_truck(truck_id: str):
    truck = state.world.truck(truck_id)
    if truck is None:
        raise HTTPException(status_code=404, detail="Truck not found")
    return truck


def _require_not_running():
    if state.world.running:
        raise HTTPException(status_code=409, detail="Stop the simulation first")


def _require_planning():
    if state.world.phase != SimPhase.PLANNING:
        raise HTTPException(status_code=409, detail="Only allowed before the simulation starts; reset first")


def _reset_to(world: WorldState):
    """Replace the world (stops the simulation thread first). Idempotent."""
    with state.lock:
        state.world.running = False
    simulator.stop()
    with state.lock:
        disruptions.cancel_all()
        state.world = world


def _status(message: str) -> dict:
    w = state.world
    return {"message": message, "phase": w.phase.value, "running": w.running, "paused": w.paused,
            "sim_time": w.sim_time, "sim_speed": w.sim_speed}


def _fresh_hubs(hubs):
    return [Hub(id=h.id, name=h.name, lat=h.lat, lon=h.lon, road_lat=h.road_lat, road_lon=h.road_lon,
                snap_distance_m=h.snap_distance_m, demand_l=h.demand_l, window_end=h.window_end,
                priority=h.priority) for h in hubs]


def _fresh_trucks(trucks):
    return [state.make_truck(t.id, t.capacity_l, t.speed_kmph) for t in trucks]


def _prefetch_distances(world: WorldState):
    """Prefetch road distances between plant and all hubs via OSRM /table (network I/O, no lock)."""
    points = [(world.plant.road_lat, world.plant.road_lon)] + [(h.road_lat, h.road_lon) for h in world.hubs]
    routing.prefetch_distances(points)


@app.get("/")
def root():
    return {"name": "DairyFlow", "docs": "/docs"}


@app.get("/config")
def get_config():
    return config.as_dict()


def _lan_ips() -> list:
    """This PC's IPv4 addresses a phone on the same Wi-Fi can use (no network traffic is sent)."""
    import socket
    ips = []
    try:  # the address the default route uses (UDP connect sends nothing)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    seen = []
    for ip in ips:
        if ip not in seen and not ip.startswith("127.") and not ip.startswith("169.254."):
            seen.append(ip)
    return seen


@app.get("/network")
def network():
    """Where phones can reach the app: http://<ip>:<UI_PORT>/driver.html?truckId=..."""
    ips = _lan_ips()
    return {"ips": ips, "ui_port": config.UI_PORT,
            "driver_urls": [f"http://{ip}:{config.UI_PORT}/driver.html" for ip in ips]}


@app.get("/health")
def health():
    """Liveness and simulation-thread health: PID (it must never change during a run), tick durations, the
    longest gap between ticks, tick errors, running route searches."""
    with state.lock:
        w = state.world
        world = {"phase": w.phase.value, "running": w.running, "paused": w.paused, "sim_time": w.sim_time,
                 "sim_speed": w.sim_speed, "route_searches": disruptions.pending_jobs(w),
                 "blocked_trucks": [t.id for t in w.trucks if t.status == TruckStatus.BLOCKED],
                 "open_alerts": sum(1 for a in w.alerts if a.status == "OPEN")}
    return {"ok": True, "pid": os.getpid(), "started_at": STARTED_AT, "uptime_s": round(time.time() - STARTED_AT, 1),
            "wall_time": time.time(), "sim_thread_alive": simulator.is_alive(), "osrm": config.OSRM_BASE_URL,
            "world": world, "ticks": simulator.stats.snapshot()}


@app.get("/state")
def get_state():
    """The whole world. Built under the lock, JSON-encoded outside it, and returned as a ready JSONResponse
    (FastAPI's jsonable_encoder pass over a big dict costs more than the dump itself)."""
    with state.lock:
        update_all_etas(state.world)
        data = world_json(state.world)
    return JSONResponse(content=data)


# Route polylines are the bulk of the world and only change on a new leg or a reroute: serialize each once.
_route_json: "dict[str, tuple]" = {}
_route_json_lock = threading.Lock()


def _truck_route_json(truck) -> list:
    if truck.route_id is None:
        return [{"lat": p.lat, "lon": p.lon} for p in truck.route]
    with _route_json_lock:
        hit = _route_json.get(truck.route_id)
    if hit is not None and hit[0] is truck.route:
        return hit[1]
    data = [{"lat": p.lat, "lon": p.lon} for p in truck.route]
    with _route_json_lock:
        if len(_route_json) > 256:
            _route_json.clear()
        _route_json[truck.route_id] = (truck.route, data)
    return data


def world_json(w: WorldState, max_decisions: int = 0) -> dict:
    """model_dump(mode="json") of the world, with cached route polylines. Caller holds state.lock."""
    data = w.model_dump(mode="json", exclude={"trucks": {"__all__": {"route"}}})
    for truck_data, truck in zip(data["trucks"], w.trucks):
        truck_data["route"] = _truck_route_json(truck)
    if max_decisions:
        data["decisions"] = data["decisions"][-max_decisions:]
    return data


@app.get("/routes/{route_id}")
def get_route_geometry(route_id: str):
    """A route's geometry by the id trucks report: [lon, lat] coordinates, the cumulative distance (m)
    of every point from the first one, and whether it is an approximate straight line."""
    route = routing.route_by_id(route_id)
    if route is None:
        with state.lock:
            truck = next((t for t in state.world.trucks if t.route_id == route_id and t.route), None)
            if truck is not None:
                route = routing.route_from_points(truck.route, truck.route_source or "osrm")
    if route is None:
        raise HTTPException(status_code=404, detail="Unknown route")
    return {"id": route_id, "coordinates": [[p.lon, p.lat] for p in route["coordinates"]],
            "cum_m": route["cum_m"], "length_m": route["length_m"], "approximate": route["approximate"]}


@app.get("/routes/{route_id}/directions")
def get_route_directions(route_id: str):
    """Turn-by-turn manoeuvres along a route (driver view): [{at_m, type, modifier, street, lat, lon}], plus
    the geometry and cumulative metres so the client can place the truck by distance_along_m. Plain def:
    street names need OSRM /nearest (bounded, cached, never under the lock)."""
    route = routing.route_by_id(route_id)
    if route is None:
        with state.lock:
            truck = next((t for t in state.world.trucks if t.route_id == route_id and t.route), None)
            if truck is not None:
                route = routing.route_from_points(truck.route, truck.route_source or "osrm")
    if route is None:
        raise HTTPException(status_code=404, detail="Unknown route")
    return JSONResponse(content=routing.route_directions(route))


# --- WebSocket (/ws) ------------------------------------------------------
# Pushes truck motion at WS_PUSH_HZ (5 Hz); every WS_WORLD_EVERY-th message also carries the full
# world (hubs, plans, decisions), i.e. ~1 Hz. GET /state is kept for tests and non-live consumers.

def ws_message(seq: int) -> dict:
    """One /ws message. Caller holds state.lock."""
    w = state.world
    now = time.time()
    payload = {
        "t": "motion",
        "seq": seq,
        "server_time": simulation.last_tick_wall if w.running and simulation.last_tick_wall else now,
        # Heartbeat: the client shows "Reconnecting…" when these stop arriving.
        "heartbeat": {"wall_time": now, "sim_time": w.sim_time, "pid": os.getpid(),
                      "tick": simulator.stats.ticks},
        "sim_time": w.sim_time,
        "sim_speed": w.sim_speed,
        "running": w.running,
        "paused": w.paused,
        "phase": w.phase.value,
        "trucks": simulation.motion_snapshot(w),
    }
    if seq % config.WS_WORLD_EVERY == 0:
        payload["world"] = world_json(w, config.WS_MAX_DECISIONS)
    return payload


def _ws_message_locked(seq: int) -> dict:
    with state.lock:
        return ws_message(seq)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    closed = asyncio.Event()

    async def watch_for_close():
        try:
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            closed.set()

    watcher = asyncio.create_task(watch_for_close())
    seq = 0
    interval = 1.0 / config.WS_PUSH_HZ
    next_at = time.monotonic()
    try:
        while not closed.is_set():
            # The lock is taken in a worker thread: the event loop (all HTTP + WS traffic) never waits for it.
            payload = await asyncio.to_thread(_ws_message_locked, seq)
            await websocket.send_json(payload)
            seq += 1
            next_at = max(next_at + interval, time.monotonic())  # never burst to catch up after a stall
            try:
                await asyncio.wait_for(closed.wait(), timeout=max(0.0, next_at - time.monotonic()))
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError, ConnectionError):  # the client went away mid-send
        pass
    except Exception:
        log.exception("/ws handler failed; closing this connection (the client reconnects)")
    finally:
        watcher.cancel()


# --- Scenario -------------------------------------------------------------

@app.get("/scenarios")
def list_scenarios():
    return [{"id": sid, "name": sc["name"], "description": sc["description"], "hub_count": len(sc["hubs"]),
             "total_demand_l": sum(h["demand_l"] for h in sc["hubs"])} for sid, sc in config.SCENARIOS.items()]


@app.post("/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str):
    if scenario_id not in config.SCENARIOS:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {scenario_id}")
    with state.lock:
        state.snapshot = None
    _reset_to(state.scenario_world(scenario_id))
    return {"message": f"{config.SCENARIOS[scenario_id]['name']} loaded"}


@app.post("/scenario/empty")
def empty_scenario():
    """The plant and the default fleet, no hubs. Idempotent."""
    with state.lock:
        state.snapshot = None
    _reset_to(state.empty_world())
    return {"message": "Empty scenario: add hubs by clicking the map"}


def _snap_result(lat: float, lon: float) -> SnapResult:
    snap = routing.snap_to_road(lat, lon)  # network I/O: never under the lock
    problem = control_tower.snap_error(snap)
    return SnapResult(lat=lat, lon=lon, road_lat=snap and snap["road_lat"], road_lon=snap and snap["road_lon"],
                      snap_distance_m=snap and snap["snap_distance_m"], road_name=snap and snap["road_name"],
                      ok=problem is None, message=problem or "On the road network")


@app.get("/snap")
def snap(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Nearest road point for a map click, and whether a hub may be placed there."""
    return _snap_result(lat, lon)


@app.post("/upload/hubs")
def upload_hubs(file: UploadFile = File(...)):
    """Replace the hubs. Every row is validated and snapped to the road network; any bad row rejects the file.
    Plain def: snapping is network I/O and must not run on the event loop."""
    text = file.file.read().decode("utf-8")
    try:
        hubs = state.parse_hubs_csv(text)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid hubs CSV: {e}")
    with state.lock:
        _require_not_running()
    errors, names = [], []
    for row, hub in enumerate(hubs, start=2):
        problem = control_tower.hub_fields_error(names, hub.name, hub.demand_l, hub.window_end)
        snap = None if problem else routing.snap_to_road(hub.lat, hub.lon)
        problem = problem or control_tower.snap_error(snap)
        if problem:
            errors.append(f"row {row} ({hub.name}): {problem}")
            continue
        names.append(hub.name)
        hub.road_lat, hub.road_lon, hub.snap_distance_m = snap["road_lat"], snap["road_lon"], snap["snap_distance_m"]
    if errors:
        raise HTTPException(status_code=400, detail="Invalid hubs CSV: " + "; ".join(errors))
    with state.lock:
        _require_not_running()
        trucks = _fresh_trucks(state.world.trucks)
        state.snapshot = None
    _reset_to(state.build_world(hubs, trucks))
    return {"count": len(hubs)}


@app.post("/upload/trucks")
def upload_trucks(file: UploadFile = File(...)):
    text = file.file.read().decode("utf-8")
    try:
        trucks = state.parse_trucks_csv(text)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid trucks CSV: {e}")
    with state.lock:
        _require_not_running()
        hubs = _fresh_hubs(state.world.hubs)
        state.snapshot = None
    _reset_to(state.build_world(hubs, trucks))
    return {"count": len(trucks)}


# --- Planning -------------------------------------------------------------

@app.post("/plan")
def create_plan():
    """Compute and store a dispatch plan. Road distances are prefetched first so the planner
    can reason on road km (not haversine). Trucks do not move until /simulation/start."""
    with state.lock:
        _require_planning()
        # Snapshot hubs/plant for the I/O outside the lock.
        snap_world = state.world.model_copy(deep=False)
    _prefetch_distances(snap_world)          # OSRM /table — network I/O, no lock
    with state.lock:
        _require_planning()
        return plan_world(state.world).model_dump(mode="json")


@app.get("/plan")
def get_plan():
    with state.lock:
        if state.world.plan is None:
            raise HTTPException(status_code=404, detail="No plan yet: POST /plan first")
        return state.world.plan.model_dump(mode="json")


# --- Simulation control ---------------------------------------------------

_start_guard = threading.Lock()


@app.post("/simulation/start")
def start_simulation():
    """Execute the stored plan (creating one if missing). Trucks load, then leave at DISPATCH_START."""
    if not _start_guard.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Simulation is already starting")
    try:
        with state.lock:
            world = state.world
            if world.phase == SimPhase.EXECUTING:
                return _status("Simulation is already running")
            _require_planning()
            if world.plan is None:
                plan_world(world)
            legs = simulation.plan_legs(world)
            if not legs:
                raise HTTPException(status_code=409, detail="The plan has no stops to execute")
            # Snapshot for I/O outside lock.
            snap_world = world.model_copy(deep=False)
        _prefetch_distances(snap_world)      # road distance matrix for accurate planning (idempotent)
        routing.prefetch(legs)               # road geometry for every planned leg
        with state.lock:
            if state.world is not world or world.phase != SimPhase.PLANNING:
                raise HTTPException(status_code=409, detail="The scenario changed while starting")
            state.snapshot = world.model_copy(deep=True)
            simulation.begin_execution(world)
        simulator.start()
        with state.lock:
            return _status("Simulation started")
    finally:
        _start_guard.release()


@app.post("/simulation/stop")
def stop_simulation():
    """Halt execution, keeping the current state. Idempotent."""
    with state.lock:
        world = state.world
        if world.phase == SimPhase.EXECUTING:
            world.running = False
            world.paused = False
            world.phase = SimPhase.STOPPED
            world.log("SYSTEM", "SIMULATION_STOPPED", "Simulation stopped by the manager")
    simulator.stop()
    with state.lock:
        return _status("Simulation stopped")


@app.post("/simulation/pause")
def pause_simulation():
    """Freeze the clock. Idempotent; no effect unless running."""
    with state.lock:
        if state.world.running:
            state.world.paused = True
        return _status("Paused" if state.world.paused else "Not running")


@app.post("/simulation/resume")
def resume_simulation():
    """Unfreeze the clock. Idempotent."""
    with state.lock:
        state.world.paused = False
        return _status("Resumed" if state.world.running else "Not running")


@app.post("/simulation/reset")
def reset_simulation():
    """Back to the moment before the last start: same scenario and plan, clock at the start. Idempotent."""
    with state.lock:
        snapshot = state.snapshot
    if snapshot is not None:
        _reset_to(snapshot.model_copy(deep=True))
    with state.lock:
        return _status("Simulation reset")


@app.post("/simulation/speed")
def set_simulation_speed(request: SpeedRequest):
    """Multiplier on SIM_SECONDS_PER_REAL_SECOND. Capped at MAX_SIM_SPEED."""
    speed = min(request.speed, config.MAX_SIM_SPEED)
    with state.lock:
        state.world.sim_speed = speed
        return _status(f"Speed set to {speed:g}x")


@app.get("/summary")
def get_summary():
    with state.lock:
        if state.world.summary is None:
            raise HTTPException(status_code=404, detail="No summary yet: the simulation has not completed")
        return state.world.summary.model_dump(mode="json")


# --- Hubs -----------------------------------------------------------------

@app.post("/hubs/manual")
def create_hub(request: HubCreateRequest):
    """Add a hub at the nearest road point (rejected if > MAX_SNAP_DISTANCE_M from a road).

    Before start this replans; during the run it is handled as a demand spike.
    """
    snap = routing.snap_to_road(request.lat, request.lon)  # network I/O outside the lock
    with state.lock:
        result = _control(control_tower.add_hub, state.world, request.name, request.lat, request.lon,
                          request.demand_l, request.window_end, request.priority, snap)
        return _change_response(result)


def _change_response(result: dict) -> dict:
    rec = result.get("recommendation")
    decision = result.get("decision")
    return {"hub": result["hub"].model_dump(mode="json"),
            "decision": decision.model_dump(mode="json") if decision else None,
            "recommendation": rec.model_dump(mode="json") if rec else None}


@app.put("/hubs/{hub_id}/demand")
def update_hub_demand(hub_id: str, update: HubDemandUpdate):
    """Live demand change. Before start: replan. During the run: control tower (see control_tower.py)."""
    with state.lock:
        hub = _get_hub(hub_id)
        return _change_response(_control(control_tower.change_demand, state.world, hub, update.demand_l))


@app.put("/hubs/{hub_id}/priority")
def update_hub_priority(hub_id: str, update: HubPriorityUpdate):
    """Priority change. Critical during the run moves the hub next in its truck's queue if no window breaks."""
    with state.lock:
        hub = _get_hub(hub_id)
        return _change_response(_control(control_tower.change_priority, state.world, hub, update.priority))


@app.put("/hubs/{hub_id}/window")
def update_hub_window(hub_id: str, update: HubWindowUpdate):
    """Change a hub's window_end (minutes from midnight). Before start: replan."""
    with state.lock:
        hub = _get_hub(hub_id)
        return _change_response(_control(control_tower.change_window, state.world, hub, update.window_end))


@app.delete("/hubs/{hub_id}")
def delete_hub(hub_id: str):
    """Delete a hub (before start only); replans if a plan exists."""
    with state.lock:
        hub = _get_hub(hub_id)
        return _change_response(_control(control_tower.delete_hub, state.world, hub))


# --- Control tower settings and recommendations ------------------------------

@app.get("/settings")
def get_settings():
    with state.lock:
        return state.settings.model_dump(mode="json")


@app.put("/settings")
def put_settings(settings: Settings):
    """control_mode: "auto" applies the recommended option at once; "approve" waits for a manager."""
    with state.lock:
        old = state.settings.control_mode
        state.settings = settings
        if old != settings.control_mode:
            state.world.log("SYSTEM", "SETTINGS_CHANGED",
                            f"Control mode {old.value} -> {settings.control_mode.value}")
        return state.settings.model_dump(mode="json")


@app.get("/recommendations")
def list_recommendations():
    with state.lock:
        return [r.model_dump(mode="json") for r in state.world.recommendations]


@app.post("/recommendations/{rec_id}/approve")
def approve_recommendation(rec_id: str):
    with state.lock:
        return _control(control_tower.approve, state.world, rec_id).model_dump(mode="json")


@app.post("/recommendations/{rec_id}/reject")
def reject_recommendation(rec_id: str):
    with state.lock:
        return _control(control_tower.reject, state.world, rec_id).model_dump(mode="json")


# --- Trucks ---------------------------------------------------------------

@app.post("/trucks")
def create_truck(request: TruckCreate):
    """Add a truck to the fleet (before start; replans if a plan exists)."""
    with state.lock:
        return _control(control_tower.add_truck, state.world, request.id, request.capacity_l)["decision"]


@app.put("/trucks/{truck_id}")
def update_truck(truck_id: str, request: TruckUpdate):
    with state.lock:
        return _control(control_tower.update_truck, state.world, _get_truck(truck_id), request.capacity_l)["decision"]


@app.delete("/trucks/{truck_id}")
def delete_truck(truck_id: str):
    with state.lock:
        return _control(control_tower.delete_truck, state.world, _get_truck(truck_id))["decision"]


@app.post("/trucks/{truck_id}/instruction")
def send_instruction(truck_id: str, request: InstructionRequest):
    with state.lock:
        truck = _get_truck(truck_id)
        truck.instructions = request.instruction
        truck.instruction_status = "ACTIVE"
        state.world.log(truck_id, "INSTRUCTION_SENT", f"Manager instruction: {request.instruction}")
    return {"message": f"Instruction sent to truck {truck_id}", "instruction": request.instruction}


@app.post("/trucks/{truck_id}/acknowledge-instruction")
def acknowledge_instruction(truck_id: str):
    with state.lock:
        truck = _get_truck(truck_id)
        if truck.instruction_status == "ACTIVE":
            truck.instruction_status = "ACKNOWLEDGED"
            truck.instructions = None
        return {"message": f"Instruction acknowledged for truck {truck_id}", "status": truck.instruction_status}


# --- Impacts (rain zones, traffic jams, and roadblocks) --------------------

def _next_impact_id(world: WorldState, kind: str) -> str:
    world.impact_seq += 1
    return f"{kind}-{world.impact_seq}"


@app.post("/impacts/rain")
def add_rain(request: RainImpactCreate):
    """Rain zone: road segments within radius_km (1–8 km) are driven at RAIN_SPEED_FACTOR x speed."""
    snap = routing.snap_to_road(request.lat, request.lon)   # network I/O: before taking the lock
    with state.lock:
        world = state.world
        road_name = snap.get("road_name") if snap else None
        radius_km = max(config.RAIN_MIN_RADIUS_KM, min(config.RAIN_MAX_RADIUS_KM, request.radius_km))
        impact = RainImpact(id=_next_impact_id(world, "rain"), lat=request.lat, lon=request.lon,
                            radius_km=radius_km, road_name=road_name, created_at=world.sim_time)
        world.impacts.append(impact)
        loc_desc = f" near {road_name}" if road_name else f" ({request.lat:.4f}, {request.lon:.4f})"
        world.log("SYSTEM", "RAIN_ADDED",
                  f"Rain zone ({radius_km:g} km radius){loc_desc}: speed x{config.RAIN_SPEED_FACTOR}",
                  {"impact_id": impact.id})
        update_all_etas(world)
        control_tower.check_and_resequence_high_risk_stops(world)
        return impact.model_dump(mode="json")


@app.post("/impacts/traffic")
def add_traffic(request: TrafficImpactCreate):
    """Traffic jam along ~1 km road stretch on the clicked road (speed x 0.4 on segments within 50 m)."""
    snap = None
    if request.lat is not None and request.lon is not None:
        snap = routing.snap_to_road(request.lat, request.lon)   # network I/O: before taking the lock
    with state.lock:
        world = state.world
        if request.lat is not None and request.lon is not None:
            road_lat = snap["road_lat"] if (snap and snap.get("road_lat") is not None) else request.lat
            road_lon = snap["road_lon"] if (snap and snap.get("road_lon") is not None) else request.lon
            road_name = snap.get("road_name") if snap else None
            impact = TrafficImpact(id=_next_impact_id(world, "traffic"), lat=road_lat, lon=road_lon,
                                   radius_km=config.TRAFFIC_STRETCH_RADIUS_KM, road_name=road_name,
                                   created_at=world.sim_time)
            world.impacts.append(impact)
            loc_desc = f" on {road_name}" if road_name else f" ({road_lat:.4f}, {road_lon:.4f})"
            world.log("SYSTEM", "TRAFFIC_ADDED",
                      f"Traffic jam (~1 km stretch){loc_desc}: speed x{config.TRAFFIC_SPEED_FACTOR}",
                      {"impact_id": impact.id})
            update_all_etas(world)
            control_tower.check_and_resequence_high_risk_stops(world)
            return impact.model_dump(mode="json")

        # Legacy segment match support
        truck = _get_truck(request.truck_id)
        segments = max(0, len(truck.route) - 1)
        if request.segment_index is None or request.segment_index >= segments:
            raise HTTPException(status_code=400,
                                detail=f"{truck.id} has {segments} route segments; "
                                       f"segment_index {request.segment_index} is out of range")
        start, end = truck.route[request.segment_index], truck.route[request.segment_index + 1]
        mid_lat, mid_lon = (start.lat + end.lat) / 2.0, (start.lon + end.lon) / 2.0
        impact = TrafficImpact(id=_next_impact_id(world, "traffic"), lat=mid_lat, lon=mid_lon,
                               radius_km=config.TRAFFIC_STRETCH_RADIUS_KM, truck_id=truck.id,
                               segment_index=request.segment_index, start=start.model_copy(),
                               end=end.model_copy(), created_at=world.sim_time)
        world.impacts.append(impact)
        world.log(truck.id, "TRAFFIC_ADDED",
                  f"Traffic on {truck.id} route segment {request.segment_index}: "
                  f"speed x{config.TRAFFIC_SPEED_FACTOR}", {"impact_id": impact.id})
        update_all_etas(world)
        control_tower.check_and_resequence_high_risk_stops(world)
        return impact.model_dump(mode="json")


@app.post("/impacts/roadblock")
def add_roadblock(request: RoadblockImpactCreate):
    """Roadblock (point closure 150 m or area closure up to 2 km): road is impassable.

    Trucks already within ROADBLOCK_PROXIMITY_DETECT_M of it learn about it at once (others after
    ROADBLOCK_DETECT_DELAY_MIN); detour searches run in the background, so this returns immediately."""
    snap = routing.snap_to_road(request.lat, request.lon)   # network I/O: before taking the lock
    with state.lock:
        world = state.world
        road_lat = snap["road_lat"] if (snap and snap.get("road_lat") is not None) else request.lat
        road_lon = snap["road_lon"] if (snap and snap.get("road_lon") is not None) else request.lon
        road_name = snap.get("road_name") if snap else None

        impact = RoadblockImpact(id=_next_impact_id(world, "roadblock"), lat=road_lat, lon=road_lon,
                                radius_m=request.radius_m, road_name=road_name, active=True,
                                created_at=world.sim_time)
        world.impacts.append(impact)
        loc_desc = f" on {road_name}" if road_name else f" ({road_lat:.4f}, {road_lon:.4f})"
        world.log("SYSTEM", "ROADBLOCK_ADDED",
                  f"Roadblock ({impact.radius_m:.0f} m radius){loc_desc}: road closed",
                  {"impact_id": impact.id, "radius_m": impact.radius_m})

        if world.phase == SimPhase.EXECUTING:
            for truck in world.trucks:
                disruptions.detect(world, truck)   # submits background searches; never waits

        update_all_etas(world)
        return impact.model_dump(mode="json")


@app.delete("/impacts/{impact_id}")
def remove_impact(impact_id: str):
    with state.lock:
        world = state.world
        impact = next((i for i in world.impacts if i.id == impact_id), None)
        if impact is None:
            raise HTTPException(status_code=404, detail="Impact not found")
        world.impacts = [i for i in world.impacts if i.id != impact_id]
        world.log("SYSTEM", "IMPACT_REMOVED", f"Impact {impact_id} removed")
        if impact.type == "roadblock":
            disruptions.on_roadblock_cleared(world, impact)   # BLOCKED trucks resume at once
        update_all_etas(world)
    return {"message": f"Impact {impact_id} removed"}


# --- Decisions & Settings ----------------------------------------------------

@app.post("/decisions/resolve")
def resolve_decision(request: DecisionResolveRequest):
    """Manager's answer to a roadblock alert: keep_detour, skip_stop, reassign_stop, return_to_plant,
    wait_for_clearance, call_driver."""
    with state.lock:
        return _control(disruptions.resolve, state.world, request.truck_id, request.action, request.alert_id)

