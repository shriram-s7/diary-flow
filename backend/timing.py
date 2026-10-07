"""The single source of ETA, service-time, slack and risk values.

Every ETA, slack and risk shown anywhere comes from these functions; the
frontend displays them and never recomputes them. Times are minutes from midnight.
"""
import math
import threading
from collections import OrderedDict
from typing import List, Optional, Sequence, Tuple

import config
from models import HubDelivery, Position, Risk, SimPhase, StopStatus, TrafficImpact, Truck, TruckStatus, WorldState
from routing import cached_route, haversine, point_to_segment_distance, road_km


def service_time_min(drop_l: float) -> float:
    """Dock time plus unloading time for a drop."""
    return config.DOCK_TIME_MIN + drop_l / config.UNLOAD_RATE_L_PER_MIN


def travel_time_min(distance_km: float, speed_kmph: float = config.CITY_SPEED_KMPH) -> float:
    if speed_kmph <= 0:
        return float("inf")
    return distance_km / speed_kmph * 60


def slack_min(window_end: float, eta: float) -> float:
    return window_end - eta


def risk_from_slack(slack: float) -> Risk:
    if slack >= config.RISK_LOW_MIN_SLACK:
        return Risk.LOW
    if slack >= config.RISK_MEDIUM_MIN_SLACK:
        return Risk.MEDIUM
    if slack >= config.RISK_HIGH_MIN_SLACK:
        return Risk.HIGH
    return Risk.LATE


def route_etas(start_time: float, start_lat: float, start_lon: float,
               stops: Sequence[Tuple[float, float, float]],
               speed_kmph: float = config.CITY_SPEED_KMPH) -> Tuple[List[float], float]:
    """Arrival time at each (lat, lon, drop_l) stop in order, driving each leg's road distance
    (routing.road_km: the leg's road route, else the OSRM distance table, else a straight line) at
    speed_kmph and unloading at each stop; also returns the time the truck is free after the last
    stop. Used for planning, where no live road conditions apply. update_truck_etas gives the same
    results for a truck at the plant with no rain or traffic, so planned ETAs match the drive.
    """
    etas = []
    t = start_time
    lat, lon = start_lat, start_lon
    for stop_lat, stop_lon, drop_l in stops:
        t += travel_time_min(road_km(lat, lon, stop_lat, stop_lon), speed_kmph)
        etas.append(t)
        t += service_time_min(drop_l)
        lat, lon = stop_lat, stop_lon
    return etas, t


def format_clock(minutes: float) -> str:
    """Minutes from midnight -> HH:MM, rounding halves up (same as the frontend's clock())."""
    total = math.floor(minutes + 0.5) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _matches_traffic(impact: TrafficImpact, lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
    # 1. Spatial match: road stretch of ~1 km, speed affected on segments within 50 m of it
    if impact.lat is not None and impact.lon is not None:
        dist_km, _, _ = point_to_segment_distance(impact.lat, impact.lon, lat1, lon1, lat2, lon2)
        if dist_km * 1000.0 <= config.TRAFFIC_INFLUENCE_DISTANCE_M:
            mid_lat, mid_lon = (lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0
            if haversine(impact.lat, impact.lon, mid_lat, mid_lon) <= config.TRAFFIC_STRETCH_RADIUS_KM:
                return True
    # 2. Legacy exact segment match
    if impact.start is not None and impact.end is not None:
        tol = config.SEGMENT_MATCH_TOLERANCE_DEG
        if (abs(impact.start.lat - lat1) < tol and abs(impact.start.lon - lon1) < tol
                and abs(impact.end.lat - lat2) < tol and abs(impact.end.lon - lon2) < tol):
            return True
    return False


def segment_speed_kmph(world: WorldState, truck: Optional[Truck],
                       lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """THE speed function: truck speed on the road segment (lat1, lon1) -> (lat2, lon2)
    after rain zones (x RAIN_SPEED_FACTOR) and traffic segments (x TRAFFIC_SPEED_FACTOR)."""
    speed = truck.speed_kmph if truck and truck.speed_kmph > 0 else config.CITY_SPEED_KMPH
    for impact in world.impacts:
        if impact.type == "rain":
            dist, _, _ = point_to_segment_distance(impact.lat, impact.lon, lat1, lon1, lat2, lon2)
            if dist <= impact.radius_km:
                speed *= config.RAIN_SPEED_FACTOR
        elif impact.type == "traffic" and _matches_traffic(impact, lat1, lon1, lat2, lon2):
            speed *= config.TRAFFIC_SPEED_FACTOR
    return speed


# Cumulative drive times along a polyline, per (polyline, truck speed, rain/traffic impacts). ETAs are
# recomputed every tick and on every GET /state; without this each one re-walks thousands of segments.
_times_cache: "OrderedDict[tuple, tuple]" = OrderedDict()
_times_lock = threading.Lock()
_TIMES_CACHE_MAX = 512


def _cumulative_times(world: WorldState, truck: Optional[Truck], points: Sequence[Position]) -> List[float]:
    """cum[i] = minutes to drive points[0] -> points[i], each segment at segment_speed_kmph."""
    speed = truck.speed_kmph if truck and truck.speed_kmph > 0 else config.CITY_SPEED_KMPH
    impacts = tuple((i.type, i.id, i.lat, i.lon, i.radius_km, getattr(i, "start", None) is not None)
                    for i in world.impacts if i.type in ("rain", "traffic"))
    key = (id(points), len(points), speed, impacts)
    with _times_lock:
        entry = _times_cache.get(key)
        if entry is not None and entry[0] is points:   # the entry keeps `points` alive, so its id is not reused
            _times_cache.move_to_end(key)
            return entry[1]
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        v = segment_speed_kmph(world, truck, a.lat, a.lon, b.lat, b.lon)
        cum.append(cum[-1] + travel_time_min(haversine(a.lat, a.lon, b.lat, b.lon), v))
    with _times_lock:
        _times_cache[key] = (points, cum)
        while len(_times_cache) > _TIMES_CACHE_MAX:
            _times_cache.popitem(last=False)
    return cum


def polyline_time_min(world: WorldState, truck: Optional[Truck], lat: float, lon: float,
                      points: Sequence[Position], start_index: int = 0) -> float:
    """Time to drive from (lat, lon) through points[start_index:].

    (lat, lon) lies on the polyline segment that ends at points[start_index];
    each segment's speed comes from segment_speed_kmph on that road segment.
    """
    if start_index >= len(points):
        return 0.0
    i = start_index
    p = points[i]
    seg_lat, seg_lon = (points[i - 1].lat, points[i - 1].lon) if i > 0 else (lat, lon)
    first = travel_time_min(haversine(lat, lon, p.lat, p.lon),
                            segment_speed_kmph(world, truck, seg_lat, seg_lon, p.lat, p.lon))
    cum = _cumulative_times(world, truck, points)
    return first + cum[-1] - cum[i]


def leg_time_min(world: WorldState, truck: Optional[Truck],
                 lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Driving time for a whole leg: along its road route if known, else a straight line."""
    route = cached_route(lat1, lon1, lat2, lon2)
    if route:
        return polyline_time_min(world, truck, lat1, lon1, route["coordinates"])
    speed = segment_speed_kmph(world, truck, lat1, lon1, lat2, lon2)
    return travel_time_min(haversine(lat1, lon1, lat2, lon2), speed)


def _start_of_next_leg(world: WorldState, truck: Truck) -> Tuple[float, float, float]:
    """(time, lat, lon) at which the truck can start driving to its next pending stop."""
    lat, lon = truck.position.lat, truck.position.lon
    if truck.status in (TruckStatus.EN_ROUTE, TruckStatus.UNLOADING):
        return world.sim_time, lat, lon
    if truck.status == TruckStatus.LOADING:
        return max(world.sim_time + truck.activity_remaining_min, config.DISPATCH_START), lat, lon
    if truck.status == TruckStatus.RETURNING:
        back = polyline_time_min(world, truck, lat, lon, truck.route, truck.route_index)
        return world.sim_time + back + config.LOADING_TIME_MIN, world.plant.road_lat, world.plant.road_lon
    # At the plant. Before execution trucks load ahead of DISPATCH_START and leave on time.
    if world.phase == SimPhase.PLANNING:
        return max(world.sim_time, config.DISPATCH_START), lat, lon
    return max(world.sim_time + config.LOADING_TIME_MIN, config.DISPATCH_START), lat, lon


def update_truck_etas(world: WorldState, truck: Truck) -> None:
    """Recompute and store eta, slack_min and risk for every pending stop of a truck."""
    pending = [s for s in truck.plan if s.status == StopStatus.PENDING]
    if not pending:
        return
    t, lat, lon = _start_of_next_leg(world, truck)
    for i, stop in enumerate(pending):
        hub = world.hub(stop.hub_id)
        if hub is None:
            continue
        if i == 0 and truck.status == TruckStatus.UNLOADING:
            # Already arrived: eta stays the actual arrival time.
            t += truck.activity_remaining_min
        else:
            if i == 0 and truck.status == TruckStatus.EN_ROUTE and truck.route:
                t += polyline_time_min(world, truck, lat, lon, truck.route, truck.route_index)
            else:
                t += leg_time_min(world, truck, lat, lon, hub.road_lat, hub.road_lon)
            stop.eta = t
            t += service_time_min(stop.drop_l)
        stop.window_end = hub.window_end
        stop.slack_min = slack_min(stop.window_end, stop.eta)
        stop.risk = risk_from_slack(stop.slack_min)
        lat, lon = hub.road_lat, hub.road_lon


def update_hub_etas(world: WorldState) -> None:
    """Per hub, from its trucks' stops (the same numbers every view shows):

    deliveries  every truck's stop at the hub, in arrival order;
    eta         latest arrival of a truck still on its way (None once all have arrived: never in the past);
    slack/risk  the worst over all its stops.
    """
    per_hub = {}
    for truck in world.trucks:
        for stop in truck.plan:
            per_hub.setdefault(stop.hub_id, []).append((truck, stop))
    for hub in world.hubs:
        entries = sorted(per_hub.get(hub.id, []),
                         key=lambda ts: (ts[1].eta if ts[1].eta is not None else float("inf"), ts[0].id))
        hub.deliveries = [
            HubDelivery(truck_id=t.id, drop_l=s.drop_l, delivered_l=s.delivered_l, eta=s.eta, arrived_at=s.arrived_at,
                        completed_at=s.completed_at, unload_min_left=s.unload_min_left, slack_min=s.slack_min,
                        risk=s.risk)
            for t, s in entries
        ]
        upcoming = [s.eta for _, s in entries if s.arrived_at is None and s.eta is not None]
        hub.eta = max(upcoming) if upcoming else None
        slacks = [s.slack_min for _, s in entries if s.slack_min is not None]
        hub.slack_min = min(slacks) if slacks else None
        hub.risk = risk_from_slack(hub.slack_min) if slacks else None


def update_all_etas(world: WorldState) -> None:
    for truck in world.trucks:
        update_truck_etas(world, truck)
    update_hub_etas(world)
