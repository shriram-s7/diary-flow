"""Roadblocks: detection, background route searches, rerouting, manager alerts, clearing (CLAUDE.md "Disruptions").

Nothing here does network I/O and nothing here waits. Route and detour searches run in routing's worker threads
(routing.submit); poll() applies the finished ones on the next simulation tick. While a detour is being searched
the truck keeps driving on its current route up to its hold point (never into the block) and waits there only
if the search is still running when it arrives.

The rule:
  detour found   -> reroute at once, always. Driver: "Road closed ahead — rerouting (+X min)"; manager: a
                    decision and a toast. If the detour costs > DETOUR_MAX_EXTRA_MIN, > DETOUR_MAX_EXTRA_KM_PCT
                    or breaks a stop's window, the manager also gets a DETOUR_COSTLY alert: keep the detour
                    (default; nothing happens if the card is ignored), hand the stop to another truck/reserve,
                    or skip the stop and notify the hub.
  no detour, or the search timed out DETOUR_MAX_ATTEMPTS times
                 -> the truck stops safely (BLOCKED) and the manager gets a red NO_ROUTE alert: call driver /
                    return to plant / reassign stop / wait for clearance.
  block cleared  -> BLOCKED trucks resume at once; searches that no longer matter are dropped.

Every function expects the caller to hold state.lock.
"""
import logging
import threading
import time
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import config
import routing
import simulation
from models import Alert, AlertOption, HubStatus, StopStatus, Truck, TruckStatus, WorldState
from routing import haversine
from timing import format_clock, polyline_time_min, update_all_etas, update_truck_etas

log = logging.getLogger(__name__)

DRIVING = (TruckStatus.EN_ROUTE, TruckStatus.RETURNING)
EPS = 1e-6


class ActionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def active_blocks(world: WorldState) -> list:
    return [i for i in world.impacts if i.type == "roadblock" and i.active]


def _road(block: Any) -> str:
    return f" on {block.road_name}" if getattr(block, "road_name", None) else ""


# --- Which part of a truck's route runs into a block (cached per route geometry and block) -----------------

_segments: Dict[tuple, List[int]] = {}
_segments_lock = threading.Lock()


def route_block_segments(truck: Truck, block: Any) -> List[int]:
    """Segments i (route[i-1] -> route[i]) of the truck's current route inside the block. A route that starts
    inside the block may leave it (skip_start_inside)."""
    if truck.route_id is None:
        return routing.block_segments(truck.route, block, skip_start_inside=True)
    key = (truck.route_id, block.id, block.lat, block.lon, block.radius_m)
    with _segments_lock:
        hit = _segments.get(key)
    if hit is None:
        hit = routing.block_segments(truck.route, block, skip_start_inside=True)
        with _segments_lock:
            if len(_segments) > 20_000:
                _segments.clear()
            _segments[key] = hit
    return hit


def next_entry(truck: Truck, block: Any) -> Optional[int]:
    """First segment at or after the truck's position that runs into the block, or None."""
    return next((i for i in route_block_segments(truck, block) if i >= truck.route_index), None)


def remaining_route_blocked(truck: Truck, blocks: list) -> bool:
    return any(next_entry(truck, b) is not None for b in blocks)


def block_hit(world: WorldState, truck: Truck, seg: int, lat: float, lon: float) -> Optional[Any]:
    """The block the move from the truck's position to (lat, lon) on segment `seg` would enter, if any."""
    for block in active_blocks(world):
        if seg not in route_block_segments(truck, block):
            continue
        r = routing.block_radius_km(block)
        if haversine(truck.position.lat, truck.position.lon, block.lat, block.lon) <= r:
            continue  # already inside (caught by the closure): it may drive out
        if routing.point_to_segment_distance(block.lat, block.lon, truck.position.lat, truck.position.lon,
                                             lat, lon)[0] <= r:
            return block
    return None


# --- Background jobs -------------------------------------------------------------------------------------

@dataclass
class _Job:
    world: WorldState
    kind: str                         # "detour" (around a block from the hold point), "leg" (a new leg), "reach"
    future: Future
    origin: Tuple[float, float]
    dest: Tuple[float, float]
    truck_id: Optional[str] = None
    hub_id: Optional[str] = None
    route_id: Optional[str] = None    # detour: the route the truck was driving when the search started
    hold_index: Optional[int] = None
    block_id: Optional[str] = None
    attempt: int = 1
    submitted: float = 0.0


_jobs: Dict[Tuple[int, str], _Job] = {}


def _key(world: WorldState, name: str) -> Tuple[int, str]:
    return id(world), name


def _plain_leg(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    return {"route": routing.get_route(lat1, lon1, lat2, lon2), "status": "ok", "requests": 1, "failed": 0}


def _submit(world: WorldState, name: str, job: _Job, blocks: list):
    """Start a search in a worker thread (no I/O here); a newer job for the same truck replaces the old one."""
    copies = [b.model_copy() for b in blocks]
    if copies:
        job.future = routing.submit(routing.search_detour, *job.origin, *job.dest, copies)
    else:
        job.future = routing.submit(_plain_leg, *job.origin, *job.dest)
    job.submitted = time.monotonic()
    old = _jobs.get(_key(world, name))
    if old is not None:
        old.future.cancel()
    _jobs[_key(world, name)] = job


def cancel(world: WorldState, truck: Truck):
    job = _jobs.pop(_key(world, truck.id), None)
    if job is not None:
        job.future.cancel()


def cancel_all():
    """Forget every search (the world was replaced)."""
    for job in _jobs.values():
        job.future.cancel()
    _jobs.clear()


def pending_jobs(world: WorldState) -> int:
    return sum(1 for j in _jobs.values() if j.world is world)


def _point(p) -> Tuple[float, float]:
    return round(p[0], 6), round(p[1], 6)


def request_leg(world: WorldState, truck: Truck):
    """The truck needs a route for its new leg from where it stands; it waits there (clock running) until
    poll() installs it. With roadblocks active the route is a detour search."""
    dest = simulation.leg_destination(world, truck)
    if dest is None:
        return
    blocks = active_blocks(world)
    truck.reroute_pending = bool(blocks)
    origin = (truck.position.lat, truck.position.lon)
    _submit(world, truck.id, _Job(world=world, kind="leg", future=Future(), origin=origin, dest=dest,
                                  truck_id=truck.id), blocks)


def _hold_point(world: WorldState, truck: Truck, entry: int, block: Any = None) -> Tuple[int, Tuple[float, float]]:
    """Where the detour starts: the route vertex the truck will reach while the search runs (DETOUR_BUDGET_SEC
    of real time at the current sim speed), but never past the last vertex before the block, and if possible
    ROADBLOCK_PROXIMITY_DETECT_M short of it (right at the closure, e.g. on a one-way carriageway, there is
    often no turn-off left)."""
    last_safe = entry - 1
    if last_safe < truck.route_index:          # already on the segment into the block: stop where it is
        return truck.route_index - 1, (truck.position.lat, truck.position.lon)
    if block is not None:
        r_km = routing.block_radius_km(block)
        margin = last_safe
        while margin > truck.route_index and (haversine(truck.route[margin].lat, truck.route[margin].lon,
                                                        block.lat, block.lon) - r_km) * 1000 \
                < config.ROADBLOCK_PROXIMITY_DETECT_M:
            margin -= 1
        if margin >= truck.route_index:
            last_safe = margin
    lookahead_m = (truck.speed_kmph / 3.6 * world.sim_speed * config.SIM_SECONDS_PER_REAL_SECOND
                   * config.DETOUR_BUDGET_SEC)
    hold = truck.route_index
    p = truck.route[hold]
    ahead_m = haversine(truck.position.lat, truck.position.lon, p.lat, p.lon) * 1000
    while hold < last_safe and ahead_m < lookahead_m:
        a, b = truck.route[hold], truck.route[hold + 1]
        ahead_m += haversine(a.lat, a.lon, b.lat, b.lon) * 1000
        hold += 1
    p = truck.route[hold]
    return hold, (p.lat, p.lon)


def request_detour(world: WorldState, truck: Truck, block: Any, entry: Optional[int], why: str):
    """Search a detour in the background; the truck drives on to its hold point meanwhile."""
    dest = simulation.leg_destination(world, truck)
    if dest is None:
        return
    if entry is None:
        entry = truck.route_index
    hold, origin = _hold_point(world, truck, entry, block)
    truck.reroute_pending = True
    truck.hold_index = hold
    truck.blocked_by = block.id
    truck.instructions = "Road closed ahead — finding a detour"
    truck.instruction_status = "SENT"
    gap_m = max(0.0, (haversine(truck.position.lat, truck.position.lon, block.lat, block.lon)
                      - routing.block_radius_km(block)) * 1000)
    world.log(truck.id, "ROADBLOCK_DETECTED",
              f"{truck.id} learned of the closure{_road(block)} ({why}, {gap_m:,.0f} m ahead); "
              f"searching for a detour while it keeps driving",
              {"roadblock_id": block.id, "detected_by": why, "distance_to_block_m": round(gap_m),
               "hold_at": {"lat": origin[0], "lon": origin[1]}})
    _submit(world, truck.id, _Job(world=world, kind="detour", future=Future(), origin=origin, dest=dest,
                                  truck_id=truck.id, route_id=truck.route_id, hold_index=hold,
                                  block_id=block.id), active_blocks(world))


def detect(world: WorldState, truck: Truck):
    """A driving truck learns about a block on its remaining route ROADBLOCK_DETECT_DELAY_MIN after it was
    placed, or once it is within ROADBLOCK_PROXIMITY_DETECT_M of it."""
    if truck.reroute_pending or not truck.route or truck.status not in DRIVING:
        return
    for block in active_blocks(world):
        entry = next_entry(truck, block)
        if entry is None:
            continue
        gap_m = (haversine(truck.position.lat, truck.position.lon, block.lat, block.lon)
                 - routing.block_radius_km(block)) * 1000
        if world.sim_time - block.created_at >= config.ROADBLOCK_DETECT_DELAY_MIN - EPS:
            request_detour(world, truck, block, entry, f"reported {config.ROADBLOCK_DETECT_DELAY_MIN:g} min ago")
            return
        if gap_m <= config.ROADBLOCK_PROXIMITY_DETECT_M:
            request_detour(world, truck, block, entry, "seen on approach")
            return


def on_block_reached(world: WorldState, truck: Truck, block: Any):
    """The truck reached a block it had not detected yet: it stops there and waits for a detour."""
    if truck.reroute_pending:
        truck.hold_index = truck.route_index - 1   # stop here until the running search answers
        return
    request_detour(world, truck, block, truck.route_index, "reached the closure")


# --- Applying results (each tick) -------------------------------------------------------------------------

def poll(world: WorldState):
    """Apply every finished search. Called at the start of each simulation tick (cheap when nothing finished)."""
    for key, job in list(_jobs.items()):
        if job.world is not world or not job.future.done():
            continue
        del _jobs[key]
        try:
            result = job.future.result()
        except CancelledError:
            continue
        except Exception as e:  # a failed search is treated like a timed-out one
            log.exception("Route search failed")
            result = {"route": None, "status": "timeout", "error": repr(e), "requests": 0, "failed": 0}
        if job.kind == "reach":
            _apply_reach(world, job, result)
            continue
        truck = world.truck(job.truck_id)
        if truck is not None and _still_wanted(world, truck, job):
            _apply(world, truck, job, result)


def _still_wanted(world: WorldState, truck: Truck, job: _Job) -> bool:
    if truck.status not in DRIVING:
        return False
    dest = simulation.leg_destination(world, truck)
    if dest is None or _point(dest) != _point(job.dest):
        return False
    if job.kind == "detour":
        return truck.route_id == job.route_id
    return not truck.route and _point((truck.position.lat, truck.position.lon)) == _point(job.origin)


def _apply(world: WorldState, truck: Truck, job: _Job, result: dict):
    route = result.get("route")
    if route is not None and route["coordinates"]:
        if job.kind == "leg":
            coords = list(route["coordinates"])
        else:
            lead = truck.route[truck.route_index:job.hold_index + 1] if job.hold_index >= truck.route_index else []
            coords = [truck.position] + list(lead) + list(route["coordinates"])
        blocks = active_blocks(world)
        if blocks and routing.route_intersects_blocks(coords, blocks, skip_start_inside=True):
            _retry(world, truck, job, "a new closure appeared during the search")  # same attempt count
            return
        _reroute(world, truck, job, route, coords, result)
        return
    if result.get("status") == "timeout" and job.attempt < config.DETOUR_MAX_ATTEMPTS:
        world.log(truck.id, "DETOUR_SEARCH_RETRY",
                  f"{truck.id}: no detour found within {config.DETOUR_BUDGET_SEC:g} s "
                  f"({result.get('failed', 0)} of {result.get('requests', 0)} route requests failed or were late); "
                  f"searching again", _search_details(result, job))
        job.attempt += 1
        _retry(world, truck, job, None)
        return
    _block(world, truck, job, result)


def _retry(world: WorldState, truck: Truck, job: _Job, note: Optional[str]):
    if note:
        world.log(truck.id, "DETOUR_SEARCH_RETRY", f"{truck.id}: {note}; searching again", {"block_id": job.block_id})
    _submit(world, truck.id, _Job(world=world, kind=job.kind, future=Future(), origin=job.origin, dest=job.dest,
                                  truck_id=truck.id, route_id=job.route_id, hold_index=job.hold_index,
                                  block_id=job.block_id, attempt=job.attempt), active_blocks(world))


def _search_details(result: dict, job: _Job) -> dict:
    return {"search_status": result.get("status"), "requests": result.get("requests"),
            "failed_requests": result.get("failed"), "candidates": result.get("candidates"),
            "elapsed_sec": result.get("elapsed_sec"), "attempt": job.attempt, "roadblock_id": job.block_id,
            "budget_sec": config.DETOUR_BUDGET_SEC}


def _polyline_km(lat: float, lon: float, points) -> float:
    km = 0.0
    for p in points:
        km += haversine(lat, lon, p.lat, p.lon)
        lat, lon = p.lat, p.lon
    return km


def _reroute(world: WorldState, truck: Truck, job: _Job, route: dict, coords: list, result: dict):
    """Install the new route at once and report what it costs."""
    pos = truck.position
    # Baseline: carrying on along the closed road (or, for a new leg, its usual road route).
    if job.kind == "detour":
        old_pts, old_start = truck.route, truck.route_index
    else:
        usual = routing.cached_route(pos.lat, pos.lon, *job.dest)
        old_pts, old_start = (usual["coordinates"], 0) if usual else (None, 0)
    blocks = active_blocks(world)
    detour = job.kind == "detour" or (old_pts is not None and routing.route_intersects_blocks(
        old_pts, blocks, skip_start_inside=True))
    before = {s.hub_id: s.slack_min for s in simulation.pending_stops(truck)}
    old_km = _polyline_km(pos.lat, pos.lon, old_pts[old_start:]) if old_pts else None
    old_min = polyline_time_min(world, truck, pos.lat, pos.lon, old_pts, old_start) if old_pts else None

    new_route = route if job.kind == "leg" else routing.route_from_points(coords, route["source"])
    simulation.install_route(truck, new_route)
    truck.reroute_pending = False
    truck.hold_index = None
    update_truck_etas(world, truck)
    if not detour or old_km is None:
        truck.blocked_by = None
        return

    new_km = new_route["distance_km"]
    new_min = polyline_time_min(world, truck, pos.lat, pos.lon, truck.route, 0)
    extra_km = max(0.0, new_km - old_km)
    extra_min = max(0.0, new_min - old_min)
    extra_pct = extra_km / old_km * 100.0 if old_km > 0.001 else 0.0
    broken = []
    for s in simulation.pending_stops(truck):
        if s.slack_min is not None and s.slack_min < 0 and (before.get(s.hub_id) is None or before[s.hub_id] >= 0):
            broken.append(f"{world.hub(s.hub_id).name} (+{-s.slack_min:.0f} min late)")
    block = next((b for b in world.impacts if b.id == job.block_id), None)
    road = _road(block) if block else ""
    truck.instructions = f"Road closed ahead — rerouting (+{extra_min:.0f} min)"
    truck.instruction_status = "SENT"
    windows = "all windows OK" if not broken else "late: " + ", ".join(broken)
    numbers = {"extra_km": round(extra_km, 2), "extra_min": round(extra_min, 1), "extra_km_pct": round(extra_pct, 1),
               "detour_km": round(new_km, 2), "windows_broken": broken, "detour_route_id": new_route["id"]}
    world.log(truck.id, "AUTO_REROUTE",
              f"{truck.id} rerouted around closure{road}: +{extra_km:.1f} km, +{extra_min:.0f} min, {windows}",
              dict(numbers, decision_type="AUTO_REROUTE", **_search_details(result, job)))

    costly = (extra_min > config.DETOUR_MAX_EXTRA_MIN or extra_pct > config.DETOUR_MAX_EXTRA_KM_PCT or broken)
    if not costly:
        return
    stop = truck.current_stop()
    hub = world.hub(stop.hub_id) if stop else None
    reasons = []
    if extra_min > config.DETOUR_MAX_EXTRA_MIN:
        reasons.append(f"+{extra_min:.0f} min (limit {config.DETOUR_MAX_EXTRA_MIN:g})")
    if extra_pct > config.DETOUR_MAX_EXTRA_KM_PCT:
        reasons.append(f"+{extra_pct:.0f}% km (limit {config.DETOUR_MAX_EXTRA_KM_PCT:g}%)")
    if broken:
        reasons.append("breaks " + ", ".join(broken))
    summary = f"{truck.id} took a costly detour{road}: " + "; ".join(reasons)
    options = [AlertOption(id="keep_detour", label="Keep the detour",
                           description=f"Default — nothing changes (+{extra_km:.1f} km, +{extra_min:.0f} min)")]
    if hub:
        options += [
            AlertOption(id="reassign_stop", label="Hand stop to another truck / reserve",
                        description=f"{truck.id} drops {hub.name} ({stop.drop_l:,.0f} L); the control tower covers it"),
            AlertOption(id="skip_stop", label="Skip stop and notify hub",
                        description=f"{hub.name} is told {stop.drop_l:,.0f} L will not arrive from {truck.id}"),
        ]
    _alert(world, "DETOUR_COSTLY", truck, summary, hub, job.block_id, options, "keep_detour",
           extra_km=round(extra_km, 2), extra_min=round(extra_min, 1), extra_km_pct=round(extra_pct, 1),
           windows_broken=broken)
    world.log(truck.id, "DETOUR_ALERT", summary + " — kept unless the manager decides otherwise",
              dict(numbers, decision_type="DETOUR_ALERT", options=[o.id for o in options]))


def _block(world: WorldState, truck: Truck, job: _Job, result: dict):
    """No detour (or the search timed out twice): stop safely and ask the manager."""
    truck.status = TruckStatus.BLOCKED
    truck.reroute_pending = False
    truck.hold_index = None
    truck.blocked_by = job.block_id
    truck.instructions = "Stop safely — no route available. Manager notified."
    truck.instruction_status = "SENT"
    stop = truck.current_stop()
    hub = world.hub(stop.hub_id) if stop else None
    target = hub.name if hub else "the plant"
    if result.get("status") == "no_route":
        why = "all routes closed"
    else:
        why = (f"no detour found — the route search timed out {job.attempt}× "
               f"({config.DETOUR_BUDGET_SEC:g} s budget each)")
    summary = f"{truck.id} cannot reach {target} — {why}"
    options = [
        AlertOption(id="call_driver", label="Call driver",
                    description=f"{truck.driver_name or 'Driver'} · {truck.driver_phone or 'no phone on file'}"),
        AlertOption(id="return_to_plant", label="Return to plant", description="Drop the remaining stops and drive back"),
        AlertOption(id="reassign_stop", label="Reassign stop",
                    description=f"Hand {target} to another truck or the reserve" if hub else "No stop to reassign"),
        AlertOption(id="wait_for_clearance", label="Wait for clearance",
                    description="Stay stopped; resume automatically when the road reopens"),
    ]
    _alert(world, "NO_ROUTE", truck, summary, hub, job.block_id, options, None)
    world.log(truck.id, "NO_ROUTE", summary,
              dict(_search_details(result, job), decision_type="NO_ROUTE", truck_id=truck.id,
                   hub_id=hub.id if hub else None, hub_name=target, driver_name=truck.driver_name or "Driver",
                   driver_phone=truck.driver_phone, options=[o.id for o in options]))
    if hub is not None and result.get("status") == "no_route":
        # Can anything reach the hub? Reserves leave from the plant (checked in the background too).
        p = world.plant
        _submit(world, f"reach:{hub.id}", _Job(world=world, kind="reach", future=Future(),
                                                origin=(p.road_lat, p.road_lon), dest=(hub.road_lat, hub.road_lon),
                                                hub_id=hub.id), active_blocks(world))


def _apply_reach(world: WorldState, job: _Job, result: dict):
    hub = world.hub(job.hub_id)
    if hub is None or hub.status != HubStatus.PENDING or result.get("status") != "no_route":
        return
    if not any(t.status == TruckStatus.BLOCKED and (s := t.current_stop()) and s.hub_id == hub.id
               for t in world.trucks):
        return
    hub.status = HubStatus.UNREACHABLE
    world.log("SYSTEM", "HUB_UNREACHABLE",
              f"{hub.name} cannot be reached by any truck — {hub.remaining_l:,.0f} L at risk until a road reopens",
              {"hub_id": hub.id, "at_risk_l": hub.remaining_l})


def _alert(world: WorldState, kind: str, truck: Truck, summary: str, hub, block_id: Optional[str],
           options: List[AlertOption], default: Optional[str], **numbers) -> Alert:
    for old in world.alerts:  # a newer alert for the same truck supersedes an open one
        if old.status == "OPEN" and old.truck_id == truck.id:
            old.status, old.resolution, old.resolved_at = "RESOLVED", "superseded", world.sim_time
    world.alert_seq += 1
    alert = Alert(id=f"ALERT-{world.alert_seq}", kind=kind, truck_id=truck.id, hub_id=hub.id if hub else None,
                  hub_name=hub.name if hub else None, roadblock_id=block_id, created_at=world.sim_time,
                  summary=summary, driver_name=truck.driver_name, driver_phone=truck.driver_phone,
                  options=options, default_option=default, **numbers)
    world.alerts.append(alert)
    return alert


def _resolve_alerts(world: WorldState, truck_id: str, resolution: str, alert_id: Optional[str] = None):
    for alert in world.alerts:
        if alert.status == "OPEN" and alert.truck_id == truck_id and (alert_id is None or alert.id == alert_id):
            alert.status, alert.resolution, alert.resolved_at = "RESOLVED", resolution, world.sim_time


# --- Clearing ---------------------------------------------------------------------------------------------

def resume(world: WorldState, truck: Truck, reason: str):
    """A BLOCKED truck drives on: on its own route if that is clear now, else on a fresh one."""
    status = TruckStatus.EN_ROUTE if simulation.pending_stops(truck) else TruckStatus.RETURNING
    truck.blocked_by = None
    blocks = active_blocks(world)
    if truck.route and truck.route_index < len(truck.route) and not remaining_route_blocked(truck, blocks):
        truck.status = status
    else:
        simulation.start_leg(world, truck, status)
    truck.instructions = "Road cleared — resuming delivery route."
    truck.instruction_status = "SENT"
    world.log(truck.id, "ROADBLOCK_CLEARED", f"{truck.id} resumed: {reason}", {})


def on_roadblock_cleared(world: WorldState, block: Any):
    """Called after the block was removed from world.impacts."""
    blocks = active_blocks(world)
    reason = f"closure{_road(block)} cleared"
    for truck in world.trucks:
        if truck.status == TruckStatus.BLOCKED:
            resume(world, truck, reason)
            _resolve_alerts(world, truck.id, "road reopened")
        elif truck.reroute_pending and truck.route and not remaining_route_blocked(truck, blocks):
            cancel(world, truck)  # the planned road is open again: no detour needed
            truck.reroute_pending = False
            truck.hold_index = None
            truck.blocked_by = None
            truck.instructions = "Road reopened — continuing on the planned route."
            truck.instruction_status = "SENT"
            world.log(truck.id, "DETOUR_CANCELLED", f"{truck.id} continues on its planned route: {reason}", {})
    for hub in world.hubs:
        if hub.status == HubStatus.UNREACHABLE:
            hub.status = HubStatus.COMPLETED if hub.remaining_l <= EPS else HubStatus.PENDING
    update_all_etas(world)


# --- Manager actions on an alert ---------------------------------------------------------------------------

ACTIONS = {"keep_detour", "skip_stop", "reassign_stop", "return_to_plant", "wait_for_clearance", "call_driver"}
ALIASES = {"keep": "keep_detour", "take_detour": "keep_detour", "detour_anyway": "keep_detour",
           "skip": "skip_stop", "reassign": "reassign_stop", "wait": "wait_for_clearance",
           "return": "return_to_plant", "call": "call_driver"}


def _drop_stop(world: WorldState, truck: Truck, hub_id: Optional[str]):
    """Take a pending stop off the truck; the truck drives on to its next destination. Returns the stop."""
    stops = simulation.pending_stops(truck)
    stop = next((s for s in stops if s.hub_id == hub_id), None) if hub_id else (stops[0] if stops else None)
    if stop is None:
        raise ActionError(409, f"{truck.id} has no pending stop to hand over")
    if truck.status == TruckStatus.UNLOADING and stops and stops[0] is stop:
        raise ActionError(409, f"{truck.id} is already unloading at {world.hub(stop.hub_id).name}")
    was_current = stops[0] is stop
    truck.plan.remove(stop)
    if was_current and truck.status in DRIVING + (TruckStatus.BLOCKED,):
        simulation.start_leg(world, truck, TruckStatus.EN_ROUTE if simulation.pending_stops(truck)
                             else TruckStatus.RETURNING)
    return stop


def resolve(world: WorldState, truck_id: str, action: str, alert_id: Optional[str] = None) -> dict:
    import control_tower  # control_tower imports simulation, which imports this module lazily

    action = ALIASES.get(action, action)
    if action not in ACTIONS:
        raise ActionError(400, f"Unknown action {action}; use one of {', '.join(sorted(ACTIONS))}")
    truck = world.truck(truck_id)
    if truck is None:
        raise ActionError(404, f"Truck {truck_id} not found")
    alert = next((a for a in reversed(world.alerts) if a.truck_id == truck_id and a.status == "OPEN"
                  and (alert_id is None or a.id == alert_id)), None)
    if alert_id is not None and alert is None:
        raise ActionError(404, f"No open alert {alert_id} for {truck_id}")
    hub_id = alert.hub_id if alert else None
    out: Dict[str, Any] = {"status": "ok", "action": action}

    if action == "call_driver":
        world.log(truck.id, "DRIVER_CALLED", f"Manager called {truck.driver_name or 'the driver'} of {truck.id}", {})
        return dict(out, truck_status=truck.status.value)
    if action == "keep_detour":
        _resolve_alerts(world, truck.id, action, alert.id if alert else None)
        world.log(truck.id, "DETOUR_KEPT", f"Manager kept {truck.id}'s detour", {})
    elif action == "wait_for_clearance":
        if truck.status != TruckStatus.BLOCKED:
            raise ActionError(409, f"{truck.id} is not stopped at a closure")
        truck.instructions = "Holding position — waiting for the road to reopen."
        truck.instruction_status = "SENT"
        _resolve_alerts(world, truck.id, action, alert.id if alert else None)
        world.log(truck.id, "WAITING_FOR_CLEARANCE",
                  f"{truck.id} waits for the road to reopen; it resumes automatically", {})
    elif action == "return_to_plant":
        if truck.status == TruckStatus.UNLOADING:
            raise ActionError(409, f"{truck.id} is unloading; it returns after this stop")
        dropped = simulation.pending_stops(truck)
        truck.plan = [s for s in truck.plan if s.status != StopStatus.PENDING]
        simulation.start_leg(world, truck, TruckStatus.RETURNING)
        truck.blocked_by = None
        truck.instructions = "Return to plant."
        truck.instruction_status = "SENT"
        _resolve_alerts(world, truck.id, action, alert.id if alert else None)
        names = ", ".join(f"{world.hub(s.hub_id).name} {s.undelivered_l:,.0f} L" for s in dropped) or "nothing"
        world.log(truck.id, "RETURN_TO_PLANT",
                  f"{truck.id} returns to the plant; not delivered: {names} (use Reassign to cover it)",
                  {"dropped": [{"hub_id": s.hub_id, "litres": s.undelivered_l} for s in dropped]})
    else:  # skip_stop / reassign_stop
        stop = _drop_stop(world, truck, hub_id)
        hub = world.hub(stop.hub_id)
        truck.blocked_by = None if truck.status != TruckStatus.BLOCKED else truck.blocked_by
        _resolve_alerts(world, truck.id, action, alert.id if alert else None)
        if action == "skip_stop":
            world.log(truck.id, "STOP_SKIPPED",
                      f"{hub.name} notified: {stop.undelivered_l:,.0f} L from {truck.id} will not arrive; "
                      f"{truck.id} drives on", {"hub_id": hub.id, "litres": stop.undelivered_l})
        else:
            result = control_tower.cover_shortage(world, hub, f"{hub.name} handed over by {truck.id}.",
                                                  exclude={truck.id})
            out["decision"] = result["decision"].model_dump(mode="json") if result.get("decision") else None
    update_all_etas(world)
    return dict(out, truck_status=truck.status.value)
