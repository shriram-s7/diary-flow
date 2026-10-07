"""Executes the stored DispatchPlan against the simulated clock.

Clock: minutes from midnight.  At 1× speed, 1 real second = SIM_SECONDS_PER_REAL_SECOND
simulated seconds = 1/60 simulated minutes.  The speed setting multiplies the rate.
Execution starts LOADING_TIME_MIN before DISPATCH_START so trucks leave at DISPATCH_START.

Simulation accuracy:
  Each real tick (1 / SIM_TICK_HZ seconds) is split into sub-steps of at most
  MAX_SUBSTEP_SIM_SEC simulated seconds so that at 120× the physics remain accurate.
  The measured elapsed real time (not the nominal interval) drives the clock so that
  jitter in the thread scheduler does not accumulate into drift.

Truck lifecycle:
    RESERVE                      (no stops; stays at the plant)
    LOADING -> EN_ROUTE -> UNLOADING -> (EN_ROUTE -> UNLOADING ...) -> RETURNING -> AT_PLANT
    EN_ROUTE / RETURNING -> BLOCKED (no way around a roadblock) -> EN_ROUTE / RETURNING (cleared / manager)

The tick never does network I/O and never waits: a leg without a cached road route, and every detour, is
searched in a worker thread (disruptions.py / routing.submit) and installed by a later tick.
"""
import logging
import math
import time
import threading
import traceback
from collections import deque
from typing import List, Optional, Tuple

import config
import routing
import state
from models import (HubResult, HubStatus, Position, SimPhase, SimulationSummary, Stop, StopStatus, Truck,
                    TruckResult, TruckStatus, WorldState)
from planner import buffered_load_l
from routing import haversine
from timing import (format_clock, risk_from_slack, segment_speed_kmph, service_time_min, slack_min,
                    travel_time_min, update_all_etas)

log = logging.getLogger(__name__)

EPS = 1e-6

# --- Plan helpers -----------------------------------------------------------

def pending_stops(truck: Truck):
    return [s for s in truck.plan if s.status == StopStatus.PENDING]


def plan_legs(world: WorldState) -> List[routing.Leg]:
    """Every leg the current truck plans will drive: plant -> stops -> plant."""
    legs = []
    for truck in world.trucks:
        points = [(world.plant.road_lat, world.plant.road_lon)]
        for stop in pending_stops(truck):
            hub = world.hub(stop.hub_id)
            if hub:
                points.append((hub.road_lat, hub.road_lon))
        if len(points) > 1:
            points.append((world.plant.road_lat, world.plant.road_lon))
            legs += [(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:])]
    return legs


def planned_load_l(world: WorldState, truck: Truck) -> float:
    """For a truck's first trip, the load the dispatch plan chose; otherwise (later trips, reserves
    dispatched by the control tower) pending drops + LOAD_BUFFER_PCT, capped at capacity."""
    first_trip = truck.km_driven == 0 and not any(s.status == StopStatus.DONE for s in truck.plan)
    if world.plan and first_trip:
        truck_plan = next((p for p in world.plan.truck_plans if p.truck_id == truck.id), None)
        if truck_plan:
            return truck_plan.load_l
    planned = sum(s.drop_l for s in pending_stops(truck))
    return buffered_load_l(planned, truck.capacity_l)


def begin_execution(world: WorldState):
    """Switch a planned world to EXECUTING. Caller holds state.lock."""
    if world.sim_time <= config.DISPATCH_START:
        world.sim_time = config.DISPATCH_START - config.LOADING_TIME_MIN
    for truck in world.trucks:
        if pending_stops(truck):
            _start_loading(world, truck, world.sim_time)
        else:
            truck.status = TruckStatus.RESERVE
    world.phase = SimPhase.EXECUTING
    world.running = True
    world.paused = False
    world.summary = None
    world.log("SYSTEM", "SIMULATION_STARTED",
              f"Loading from {format_clock(world.sim_time)}; trucks leave at {format_clock(config.DISPATCH_START)}")
    update_all_etas(world)


# --- Truck events -----------------------------------------------------------

def _start_loading(world: WorldState, truck: Truck, now: float):
    load = max(0.0, min(planned_load_l(world, truck) - truck.load_l, world.plant.milk_available_l))
    world.plant.milk_available_l -= load
    truck.load_l += load
    truck.status = TruckStatus.LOADING
    truck.activity_remaining_min = config.LOADING_TIME_MIN
    world.log(truck.id, "LOADING", f"{truck.id} loading {load:,.0f} L at the plant",
              {"load_l": truck.load_l}, time=now)


def leg_destination(world: WorldState, truck: Truck) -> Optional[Tuple[float, float]]:
    if truck.status == TruckStatus.RETURNING:
        return world.plant.road_lat, world.plant.road_lon
    stops = pending_stops(truck)
    hub = world.hub(stops[0].hub_id) if stops else None
    return (hub.road_lat, hub.road_lon) if hub else None


def install_route(truck: Truck, route: routing.Route):
    """Put a truck on a new leg geometry from its current position (a new route id tells the client)."""
    truck.route = list(route["coordinates"])
    truck.route_index = 0
    truck.route_source = route["source"]
    truck.route_id = route["id"]
    truck.distance_along_route_m = 0.0
    truck.reroute_pending = False
    truck.hold_index = None


def start_leg(world: WorldState, truck: Truck, status: TruckStatus):
    """Begin driving: on the cached road route if it avoids every active roadblock, else the truck waits where
    it is (clock running) while a worker thread finds a route (disruptions.request_leg). Never blocks."""
    truck.status = status
    truck.route = []
    truck.route_index = 0
    truck.route_source = None
    truck.route_id = None
    truck.distance_along_route_m = 0.0
    truck.reroute_pending = False
    truck.hold_index = None
    disruptions.cancel(world, truck)
    dest = leg_destination(world, truck)
    if not dest:
        return
    route = routing.cached_route(truck.position.lat, truck.position.lon, *dest)
    blocks = disruptions.active_blocks(world)
    if route and route["coordinates"] and not (
            blocks and routing.route_intersects_blocks(route["coordinates"], blocks, skip_start_inside=True)):
        install_route(truck, route)
        return
    disruptions.request_leg(world, truck)


def _account(truck: Truck, km: float):
    """Every metre driven costs fuel, including the return trip."""
    fuel = km / truck.fuel_km_per_l
    truck.km_driven += km
    truck.fuel_used_l += fuel
    if truck.status == TruckStatus.RETURNING:
        truck.return_km += km
        truck.return_fuel_l += fuel


def _drive(world: WorldState, truck: Truck, dt: float) -> float:
    """Drive along the route for up to dt minutes, never past the hold point while a detour is searched and
    never into a roadblock (the truck stops at it and waits for a detour). Returns unused minutes."""
    end = len(truck.route) if truck.hold_index is None else min(len(truck.route), truck.hold_index + 1)
    while dt > EPS and truck.route_index < end:
        i = truck.route_index
        p = truck.route[i]
        lat, lon = truck.position.lat, truck.position.lon
        seg = truck.route[i - 1] if i > 0 else truck.position
        speed = segment_speed_kmph(world, truck, seg.lat, seg.lon, p.lat, p.lon)
        dist = haversine(lat, lon, p.lat, p.lon)
        seg_t = travel_time_min(dist, speed)

        candidate_lat, candidate_lon = ((p.lat, p.lon) if seg_t <= dt else
                                        (lat + (p.lat - lat) * (dt / seg_t), lon + (p.lon - lon) * (dt / seg_t)))
        block = disruptions.block_hit(world, truck, i, candidate_lat, candidate_lon)
        if block is not None:
            disruptions.on_block_reached(world, truck, block)
            return dt

        if seg_t <= dt:
            truck.position = Position(lat=p.lat, lon=p.lon)
            truck.route_index += 1
            _account(truck, dist)
            truck.distance_along_route_m += dist * 1000.0
            dt -= seg_t
        else:
            frac = dt / seg_t
            truck.position = Position(lat=lat + (p.lat - lat) * frac, lon=lon + (p.lon - lon) * frac)
            _account(truck, dist * frac)
            truck.distance_along_route_m += dist * frac * 1000.0
            dt = 0.0
    return dt


def _arrive_at_hub(world: WorldState, truck: Truck, now: float):
    stop = pending_stops(truck)[0]
    hub = world.hub(stop.hub_id)
    truck.position = Position(lat=hub.road_lat, lon=hub.road_lon)
    truck.route = []
    truck.route_id = None
    truck.status = TruckStatus.UNLOADING
    truck.activity_remaining_min = service_time_min(stop.drop_l)
    stop.eta = now
    stop.arrived_at = now
    stop.unload_min_left = truck.activity_remaining_min
    stop.slack_min = slack_min(stop.window_end, now)
    stop.risk = risk_from_slack(stop.slack_min)


def unload_progress(world: WorldState, truck: Truck, stop: Stop):
    """Move milk from truck to hub as unloading proceeds.

    Service = DOCK_TIME_MIN then drop_l / UNLOAD_RATE_L_PER_MIN, so with r minutes of service left the stop
    has delivered drop_l - UNLOAD_RATE_L_PER_MIN * r (clamped to 0..drop_l). Never more than the truck
    carries or the hub still needs.
    """
    hub = world.hub(stop.hub_id)
    target = min(stop.drop_l, max(0.0, stop.drop_l - config.UNLOAD_RATE_L_PER_MIN * truck.activity_remaining_min))
    delta = min(target - stop.delivered_l, truck.load_l, hub.remaining_l)
    if delta > 0:
        stop.delivered_l += delta
        hub.delivered_l = min(hub.demand_l, hub.delivered_l + delta)
        truck.load_l = max(0.0, truck.load_l - delta)
    if hub.remaining_l <= EPS:
        hub.delivered_l = hub.demand_l
        hub.status = HubStatus.COMPLETED
    stop.unload_min_left = truck.activity_remaining_min


def _finish_unloading(world: WorldState, truck: Truck, now: float):
    stop = pending_stops(truck)[0]
    hub = world.hub(stop.hub_id)
    unload_progress(world, truck, stop)
    delivered = stop.delivered_l
    stop.status = StopStatus.DONE
    stop.completed_at = now
    stop.unload_min_left = None
    summary = (f"Delivered {delivered:,.0f} L to {hub.name} "
               f"(arrived {format_clock(stop.eta)}, window {format_clock(stop.window_end)})")
    if delivered + EPS < stop.drop_l:
        summary += f"; {stop.drop_l - delivered:,.0f} L short of the planned drop"
    world.log(truck.id, "DELIVERED", summary,
              {"hub_id": hub.id, "drop_l": stop.drop_l, "delivered_l": delivered, "arrival": stop.eta,
               "slack_min": stop.slack_min, "hub_remaining_l": hub.remaining_l, "truck_load_l": truck.load_l},
              time=now)
    start_leg(world, truck, TruckStatus.EN_ROUTE if pending_stops(truck) else TruckStatus.RETURNING)


def _arrive_at_plant(world: WorldState, truck: Truck, now: float):
    truck.position = Position(lat=world.plant.road_lat, lon=world.plant.road_lon)
    truck.route = []
    truck.route_id = None
    truck.status = TruckStatus.AT_PLANT
    truck.returned_at = now
    returned = truck.load_l
    world.plant.milk_available_l += returned
    truck.load_l = 0.0
    world.log(truck.id, "RETURNED",
              f"{truck.id} back at the plant; {returned:,.0f} L returned to stock",
              {"returned_l": returned, "km_driven": truck.km_driven, "fuel_used_l": truck.fuel_used_l},
              time=now)


def advance_truck(world: WorldState, truck: Truck, now: float, dt: float):
    """Advance one truck from time `now` by dt minutes, handling every event at its exact time."""
    while True:
        status = truck.status
        if status in (TruckStatus.RESERVE, TruckStatus.AT_PLANT):
            if world.phase == SimPhase.EXECUTING and pending_stops(truck):
                _start_loading(world, truck, now)
                continue
            return
        if status == TruckStatus.LOADING:
            leave_at = max(now + truck.activity_remaining_min, config.DISPATCH_START)
            if leave_at > now + dt + EPS:
                truck.activity_remaining_min = max(0.0, truck.activity_remaining_min - dt)
                return
            dt -= leave_at - now
            now = leave_at
            truck.activity_remaining_min = 0.0
            world.log(truck.id, "DISPATCHED",
                      f"{truck.id} left the plant at {format_clock(now)} with {truck.load_l:,.0f} L",
                      {"load_l": truck.load_l, "stops": [s.hub_id for s in pending_stops(truck)]}, time=now)
            start_leg(world, truck, TruckStatus.EN_ROUTE)
            continue
        if status == TruckStatus.BLOCKED:
            return
        if status in (TruckStatus.EN_ROUTE, TruckStatus.RETURNING):
            if not truck.route:
                return  # waiting for the road route (a worker thread is fetching it)
            disruptions.detect(world, truck)
            left = _drive(world, truck, dt)
            now += dt - left
            dt = left
            if truck.route_index < len(truck.route):
                return  # still driving, or holding while a detour is searched
            if status == TruckStatus.EN_ROUTE:
                _arrive_at_hub(world, truck, now)
            else:
                _arrive_at_plant(world, truck, now)
            continue
        if status == TruckStatus.UNLOADING:
            if truck.activity_remaining_min > dt + EPS:
                truck.activity_remaining_min -= dt
                unload_progress(world, truck, pending_stops(truck)[0])
                return
            dt -= truck.activity_remaining_min
            now += truck.activity_remaining_min
            truck.activity_remaining_min = 0.0
            _finish_unloading(world, truck, now)
            continue
        return


def _all_done(world: WorldState) -> bool:
    return all(t.status in (TruckStatus.AT_PLANT, TruckStatus.RESERVE) and not pending_stops(t)
               for t in world.trucks)


def build_summary(world: WorldState) -> SimulationSummary:
    done = [(t, s) for t in world.trucks for s in t.plan if s.status == StopStatus.DONE]
    on_time = [s for _, s in done if s.eta <= s.window_end + EPS]
    hubs = []
    for hub in world.hubs:
        stops = [s for _, s in done if s.hub_id == hub.id]
        hubs.append(HubResult(
            hub_id=hub.id, name=hub.name, demand_l=hub.demand_l, delivered_l=hub.delivered_l,
            unserved_l=hub.remaining_l, window_end=hub.window_end,
            last_arrival=max((s.eta for s in stops), default=None),
            on_time=hub.remaining_l <= EPS and all(s.eta <= s.window_end + EPS for s in stops),
        ))
    trucks = []
    for t in world.trucks:
        trucks.append(TruckResult(
            truck_id=t.id, used=t.km_driven > 0,
            delivered_l=sum(s.delivered_l for tt, s in done if tt is t),
            km=t.km_driven, fuel_l=t.fuel_used_l, cost_inr=t.km_driven * t.cost_per_km,
            return_km=t.return_km, return_fuel_l=t.return_fuel_l,
        ))
    return SimulationSummary(
        completed_at=max((t.returned_at for t in world.trucks if t.returned_at is not None),
                         default=world.sim_time),
        total_demand_l=sum(h.demand_l for h in world.hubs),
        total_delivered_l=sum(h.delivered_l for h in world.hubs),
        stops_total=len(done),
        stops_on_time=len(on_time),
        on_time_pct=100.0 * len(on_time) / len(done) if done else 100.0,
        total_km=sum(r.km for r in trucks),
        total_fuel_l=sum(r.fuel_l for r in trucks),
        total_cost_inr=sum(r.cost_inr for r in trucks),
        trucks_used=[r.truck_id for r in trucks if r.used],
        trucks_reserve=[r.truck_id for r in trucks if not r.used],
        hubs=hubs,
        trucks=trucks,
    )


def _complete(world: WorldState):
    world.phase = SimPhase.COMPLETED
    world.running = False
    world.paused = False
    world.summary = build_summary(world)
    s = world.summary
    world.log("SYSTEM", "SIMULATION_COMPLETED",
              f"All trucks back by {format_clock(s.completed_at)}: {s.total_delivered_l:,.0f} of "
              f"{s.total_demand_l:,.0f} L delivered, {s.on_time_pct:.0f}% of stops on time, "
              f"{s.total_km:.1f} km, {s.total_fuel_l:.1f} L fuel, Rs {s.total_cost_inr:,.0f}",
              time=s.completed_at)


def step(world: WorldState, dt: float):
    """Advance the world by dt simulated minutes. Caller holds state.lock. Never does network I/O.

    First installs the routes and detours that worker threads finished since the last tick. Motion is integrated
    in sub-steps of at most MAX_SUBSTEP_SIM_SEC simulated seconds so that at high speed factors the physics
    remain accurate. update_all_etas() is called once per real tick.
    """
    if world.phase != SimPhase.EXECUTING:
        return
    disruptions.poll(world)
    max_sub_min = config.MAX_SUBSTEP_SIM_SEC / 60.0
    n_steps = max(1, math.ceil(dt / max_sub_min))
    sub = dt / n_steps   # equal sub-steps so no floating-point remainder drift
    for _ in range(n_steps):
        start = world.sim_time
        world.sim_time += sub
        for truck in world.trucks:
            advance_truck(world, truck, start, sub)
    update_all_etas(world)
    import control_tower
    control_tower.check_and_resequence_high_risk_stops(world)
    if _all_done(world):
        _complete(world)


# --- WebSocket motion snapshot -------------------------------------------------------

def motion_snapshot(world: WorldState) -> list:
    """Per-truck motion record for the /ws push. Caller holds state.lock.

    route_id names the leg's geometry (GET /routes/{id}); distance_along_m is how far along it the truck
    is; speed_mps is its current simulated speed on this road segment (rain/traffic included), which the
    client multiplies by the sim speed to advance the truck between pushes; heading is the bearing of the
    segment being driven.
    """
    result = []
    for truck in world.trucks:
        heading = None
        speed_mps = 0.0
        driving = truck.status in (TruckStatus.EN_ROUTE, TruckStatus.RETURNING)
        holding = truck.hold_index is not None and truck.route_index > truck.hold_index
        if driving and not holding and truck.route and truck.route_index < len(truck.route):
            i = truck.route_index
            prev = truck.route[i - 1] if i > 0 else truck.position
            nxt = next((p for p in truck.route[i:]
                        if haversine(truck.position.lat, truck.position.lon, p.lat, p.lon) > 1e-4), None)
            if nxt is not None:
                heading = routing.bearing(truck.position.lat, truck.position.lon, nxt.lat, nxt.lon)
            speed_mps = segment_speed_kmph(world, truck, prev.lat, prev.lon, truck.route[i].lat, truck.route[i].lon) / 3.6
        result.append({
            "id": truck.id,
            "lat": truck.position.lat,
            "lon": truck.position.lon,
            "heading": heading,
            "speed_mps": speed_mps,
            "route_id": truck.route_id,
            "distance_along_m": truck.distance_along_route_m,
            "status": truck.status.value,
            "approximate": truck.route_source == "fallback",
            "reroute_pending": truck.reroute_pending,
        })
    return result


# --- Thread -------------------------------------------------------------------

# Wall-clock time (time.time()) of the last tick: the moment the world's motion state refers to. /ws sends it
# as server_time so the client extrapolates from when the state was true, not from when it was sent.
last_tick_wall: Optional[float] = None


class TickStats:
    """Duration of every simulation tick (lock wait included) and the gaps between ticks, for GET /health."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.ticks = 0
            self.durations_ms = deque(maxlen=20_000)
            self.max_ms = 0.0
            self.slow = 0                 # ticks slower than SIM_TICK_BUDGET_MS
            self.max_gap_ms = 0.0         # longest time between the starts of two ticks
            self.max_lock_wait_ms = 0.0   # part of a tick spent waiting for state.lock
            self.max_step_ms = 0.0        # part of a tick spent in step()
            self.errors = 0
            self.last_error: Optional[str] = None
            self._last_start: Optional[float] = None
            self.last_tick_wall: Optional[float] = None

    def parts(self, lock_wait_ms: float, step_ms: float):
        with self._lock:
            self.max_lock_wait_ms = max(self.max_lock_wait_ms, lock_wait_ms)
            self.max_step_ms = max(self.max_step_ms, step_ms)

    def record(self, start: float, end: float):
        ms = (end - start) * 1000
        with self._lock:
            self.ticks += 1
            self.durations_ms.append(ms)
            self.max_ms = max(self.max_ms, ms)
            if ms > config.SIM_TICK_BUDGET_MS:
                self.slow += 1
            if self._last_start is not None:
                self.max_gap_ms = max(self.max_gap_ms, (start - self._last_start) * 1000)
            self._last_start = start
            self.last_tick_wall = time.time()

    def error(self, message: str):
        with self._lock:
            self.errors += 1
            self.last_error = message

    def snapshot(self) -> dict:
        with self._lock:
            d = sorted(self.durations_ms)
            pct = (lambda q: round(d[min(len(d) - 1, int(q * len(d)))], 2)) if d else (lambda q: None)
            return {"ticks": self.ticks, "tick_ms_p50": pct(0.5), "tick_ms_p95": pct(0.95),
                    "tick_ms_max": round(self.max_ms, 2), "slow_ticks": self.slow,
                    "tick_budget_ms": config.SIM_TICK_BUDGET_MS,
                    "max_lock_wait_ms": round(self.max_lock_wait_ms, 1), "max_step_ms": round(self.max_step_ms, 1), "max_gap_between_ticks_ms": round(self.max_gap_ms, 1),
                    "errors": self.errors, "last_error": self.last_error, "last_tick_wall": self.last_tick_wall}


class Simulator:
    """Owns the simulation thread. start()/stop() must be called WITHOUT holding state.lock.

    A tick that raises is logged (traceback) and recorded as a SYSTEM SIM_ERROR decision; the clock keeps going.
    """

    ERROR_DECISION_EVERY_SEC = 10.0   # at most one SIM_ERROR decision per distinct error per 10 s

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.stats = TickStats()
        self._error_logged_at: dict = {}

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_alive():
            return
        self.stats.reset()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True, name="simulation")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self, stop_event: threading.Event):
        """Tick at SIM_TICK_HZ; use measured elapsed real time so jitter doesn't accumulate."""
        global last_tick_wall
        interval = config.SIM_UPDATE_INTERVAL_SEC
        last_real = time.monotonic()
        while not stop_event.wait(interval):
            now_real = time.monotonic()
            tick_start = time.perf_counter()   # monotonic() has ~16 ms resolution on Windows
            elapsed = now_real - last_real
            last_real = now_real
            try:
                with state.lock:
                    world = state.world
                    if not world.running:
                        break
                    if world.paused:
                        continue
                    # dt in simulated MINUTES: elapsed_s × speed × (1 sim-sec / 60 sim-min)
                    dt = elapsed * config.SIM_SECONDS_PER_REAL_SECOND * world.sim_speed / 60.0
                    locked = time.perf_counter()
                    step(world, dt)
                    last_tick_wall = time.time()
                    self.stats.parts((locked - tick_start) * 1000, (time.perf_counter() - locked) * 1000)
            except Exception as e:  # never let one bad tick stop the clock
                self._report(e)
            finally:
                self.stats.record(tick_start, time.perf_counter())

    def _report(self, e: Exception):
        tb = traceback.format_exc()
        log.error("Simulation tick failed; the simulation keeps running\n%s", tb)
        message = f"{type(e).__name__}: {e}"
        self.stats.error(message)
        now = time.monotonic()
        if now - self._error_logged_at.get(message, -1e9) < self.ERROR_DECISION_EVERY_SEC:
            return
        self._error_logged_at[message] = now
        try:
            with state.lock:
                state.world.log("SYSTEM", "SIM_ERROR",
                                f"Simulation tick failed ({message}); the simulation keeps running",
                                {"error": message, "traceback": tb[-4000:]})
        except Exception:
            log.exception("Could not record the simulation error")


simulator = Simulator()

import disruptions  # noqa: E402  (disruptions imports this module)
