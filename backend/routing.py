"""Road routes, road distances and road snapping from OSRM, with a disk cache, retries and geometry helpers.

Every OSRM response is cached on disk (config.ROUTE_CACHE_DIR, one JSON file per request keyed by rounded
coordinates + options), so a demo that worked once works again without the network. A failed request is
retried OSRM_RETRIES times with backoff; only then does a route fall back to a straight line, marked
`approximate` (fallbacks are never written to disk, so the next run tries OSRM again).

Network I/O happens here and only here. Never call the fetch/prefetch/search functions while holding the world
lock, in the simulation tick, or on the event loop: the simulation submits them with submit() and applies the
results on a later tick (disruptions.py).
"""
import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import requests

import config
from models import Position

log = logging.getLogger(__name__)


class LatLon(NamedTuple):
    """A light point for detour candidates (thousands per search; pydantic Positions only for the winner)."""
    lat: float
    lon: float

EARTH_RADIUS_KM = 6371.0

# A route: {"id", "coordinates": [Position], "cum_m": [float] (distance from the first point),
#           "length_m", "distance_km", "source": "osrm"|"fallback", "approximate": bool}
Route = Dict
Leg = Tuple[float, float, float, float]  # from lat, from lon, to lat, to lon


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing (degrees, 0 = north) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def point_to_segment_distance(lat: float, lon: float,
                              lat1: float, lon1: float,
                              lat2: float, lon2: float) -> Tuple[float, float, float]:
    """Shortest distance (km) from a point to a segment, and the closest point on it."""
    seg_x, seg_y = lon2 - lon1, lat2 - lat1
    seg_len_sq = seg_x * seg_x + seg_y * seg_y
    if seg_len_sq == 0:
        return haversine(lat, lon, lat1, lon1), lat1, lon1
    t = max(0.0, min(1.0, ((lon - lon1) * seg_x + (lat - lat1) * seg_y) / seg_len_sq))
    closest_lat, closest_lon = lat1 + t * seg_y, lon1 + t * seg_x
    return haversine(lat, lon, closest_lat, closest_lon), closest_lat, closest_lon


def destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> Tuple[float, float]:
    """Destination point given distance and initial bearing from (lat, lon) on a spherical Earth."""
    r = distance_km / EARTH_RADIUS_KM
    b = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(r) + math.cos(lat1) * math.sin(r) * math.cos(b))
    lon2 = lon1 + math.atan2(math.sin(b) * math.sin(r) * math.cos(lat1),
                             math.cos(r) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def block_radius_km(block: Any) -> float:
    return getattr(block, "radius_m", config.ROADBLOCK_DEFAULT_RADIUS_M) / 1000.0


def block_segments(coordinates: Sequence[Position], block: Any, skip_start_inside: bool = False,
                   first_only: bool = False) -> List[int]:
    """Indices i of the polyline segments coordinates[i-1] -> coordinates[i] that pass within the block's radius
    (i = 0: the first point itself is inside).

    skip_start_inside: a polyline that starts inside the block (a truck caught in a closure) may drive out of
    it, so its leading points inside the block and the segment leaving it do not count.
    """
    n = len(coordinates)
    if n == 0:
        return []
    r = block_radius_km(block)
    lat0, lon0 = block.lat, block.lon
    dlat = r / 111.0 * 1.05                                      # bounding-box prefilter (degrees)
    dlon = dlat / max(0.1, math.cos(math.radians(lat0)))

    def inside(p):
        return haversine(lat0, lon0, p.lat, p.lon) <= r

    out: List[int] = []
    begin = 1
    if inside(coordinates[0]):
        if skip_start_inside:
            i = 0
            while i < n and inside(coordinates[i]):
                i += 1
            if i == n:
                return []
            begin = i + 1
        else:
            out.append(0)
            if first_only:
                return out
    for i in range(begin, n):
        a, b = coordinates[i - 1], coordinates[i]
        if ((a.lat < lat0 - dlat and b.lat < lat0 - dlat) or (a.lat > lat0 + dlat and b.lat > lat0 + dlat)
                or (a.lon < lon0 - dlon and b.lon < lon0 - dlon) or (a.lon > lon0 + dlon and b.lon > lon0 + dlon)):
            continue
        if point_to_segment_distance(lat0, lon0, a.lat, a.lon, b.lat, b.lon)[0] <= r:
            out.append(i)
            if first_only:
                return out
    return out


def route_intersects_blocks(coordinates: Sequence[Position], roadblocks: Sequence[Any],
                            skip_start_inside: bool = False) -> bool:
    """True if the polyline passes within the radius of any active roadblock."""
    if not coordinates or not roadblocks:
        return False
    # Whole-polyline bounding box first: most blocks are nowhere near a given route.
    lats = [p.lat for p in coordinates]
    lons = [p.lon for p in coordinates]
    lat_min, lat_max, lon_min, lon_max = min(lats), max(lats), min(lons), max(lons)
    for rb in roadblocks:
        if getattr(rb, "active", True) is False:
            continue
        dlat = block_radius_km(rb) / 111.0 * 1.05
        dlon = dlat / max(0.1, math.cos(math.radians(rb.lat)))
        if rb.lat + dlat < lat_min or rb.lat - dlat > lat_max or rb.lon + dlon < lon_min or rb.lon - dlon > lon_max:
            continue
        if block_segments(coordinates, rb, skip_start_inside, first_only=True):
            return True
    return False


def truck_route_intersects_block(truck: Any, block: Any) -> bool:
    """True if truck's remaining route passes within block.radius_m of the roadblock."""
    if not getattr(truck, "route", None):
        return False
    remaining = [truck.position] + list(truck.route[truck.route_index:])
    return route_intersects_blocks(remaining, [block])


# --- Disk cache + retrying OSRM client ------------------------------------------------

def _round(*values: float) -> Tuple[float, ...]:
    return tuple(round(v, 6) for v in values)


def _cache_path(key: str) -> str:
    return os.path.join(config.ROUTE_CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _disk_get(key: str) -> Optional[dict]:
    try:
        with open(_cache_path(key), encoding="utf-8") as f:
            entry = json.load(f)
        return entry["response"] if entry.get("key") == key else None
    except (OSError, ValueError, KeyError):
        return None


def _disk_put(key: str, response: dict):
    try:
        os.makedirs(config.ROUTE_CACHE_DIR, exist_ok=True)
        tmp = _cache_path(key) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "response": response}, f)
        os.replace(tmp, _cache_path(key))
    except OSError as e:
        log.warning("Could not write route cache (%s)", e)


def _http_get(url: str, params: dict, timeout: float) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _sleep(seconds: float):
    time.sleep(seconds)


def _osrm_get(service: str, coords: str, params: dict, timeout: float,
              deadline: Optional[float] = None) -> Optional[dict]:
    """GET {OSRM}/{service}/v1/driving/{coords}: disk cache, then up to 1 + OSRM_RETRIES attempts with backoff.

    Each attempt waits at most `timeout` s; with a `deadline` (time.monotonic()) no attempt or backoff runs past it.
    Returns the JSON response (code "Ok"), or None if every attempt failed.
    """
    options = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    key = f"{service}|{coords}|{options}"
    cached = _disk_get(key)
    if cached is not None:
        return cached
    url = f"{config.OSRM_BASE_URL}/{service}/v1/driving/{coords}"
    for attempt in range(1 + config.OSRM_RETRIES):
        if attempt:
            backoff = config.OSRM_BACKOFF_SEC * 2 ** (attempt - 1)
            if deadline is not None and time.monotonic() + backoff >= deadline:
                break
            _sleep(backoff)
        wait_sec = timeout
        if deadline is not None:
            wait_sec = min(timeout, deadline - time.monotonic())
            if wait_sec <= 0.05:
                break
        try:
            data = _http_get(url, params, wait_sec)
            if data.get("code") == "Ok":
                _disk_put(key, data)
                return data
            log.warning("OSRM %s answered %s (attempt %d)", service, data.get("code"), attempt + 1)
        except (requests.RequestException, ValueError) as e:
            log.warning("OSRM %s failed (%s, attempt %d)", service, e, attempt + 1)
    return None


# --- Routes ---------------------------------------------------------------------------

def _route_id(coordinates: List[Position], source: str) -> str:
    """Stable id from the geometry itself, so a detour between the same two points gets a new id."""
    digest = hashlib.sha1(source.encode())
    for p in coordinates:
        digest.update(b"%.6f,%.6f;" % (p.lat, p.lon))
    return digest.hexdigest()[:16]


def _make_route(lat1, lon1, lat2, lon2, coordinates: List[Position], source: str) -> Route:
    """A route with cumulative distances; registered by id so the client can fetch its geometry."""
    cum = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        cum.append(cum[-1] + haversine(a.lat, a.lon, b.lat, b.lon) * 1000)
    approximate = source == "fallback"
    route = {"id": _route_id(coordinates, source), "coordinates": coordinates, "cum_m": cum,
             "length_m": cum[-1], "distance_km": cum[-1] / 1000, "source": source, "approximate": approximate}
    remember_route(route)
    return route


def route_from_points(coordinates: Sequence[Position], source: str) -> Route:
    """A route object for a polyline a truck is already driving (e.g. one restored from a snapshot)."""
    first, last = coordinates[0], coordinates[-1]
    return _make_route(first.lat, first.lon, last.lat, last.lon, list(coordinates), source)


def fallback_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Route:
    """Straight line, marked approximate (driven at CITY_SPEED_KMPH like any other)."""
    distance_km = haversine(lat1, lon1, lat2, lon2)
    steps = max(20, int(distance_km * 2))
    coordinates = [Position(lat=lat1 + (lat2 - lat1) * i / steps, lon=lon1 + (lon2 - lon1) * i / steps)
                   for i in range(steps + 1)]
    route = _make_route(lat1, lon1, lat2, lon2, coordinates, "fallback")
    route["cum_m"] = [i / steps * distance_km * 1000.0 for i in range(steps + 1)]
    route["length_m"] = distance_km * 1000.0
    route["distance_km"] = distance_km
    return route


def route_from_osrm(lat1: float, lon1: float, lat2: float, lon2: float) -> Optional[Route]:
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    data = _osrm_get("route", "%.6f,%.6f;%.6f,%.6f" % _round(lon1, lat1, lon2, lat2), params, config.OSRM_TIMEOUT_SEC)
    if not data or not data.get("routes"):
        return None
    coordinates = [Position(lat=c[1], lon=c[0]) for c in data["routes"][0]["geometry"]["coordinates"]]
    return _make_route(lat1, lon1, lat2, lon2, coordinates, "osrm") if len(coordinates) >= 2 else None


def fetch_road_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Route:
    """Road route (disk cache / OSRM with retries), else an approximate straight line."""
    route = route_from_osrm(lat1, lon1, lat2, lon2)
    if route is None:
        log.warning("Using an approximate straight-line route for %s", _round(lat1, lon1, lat2, lon2))
        route = fallback_route(lat1, lon1, lat2, lon2)
    return route


# --- Worker threads ------------------------------------------------------------------------
# Jobs (a leg's route, a detour search) run in _job_pool; the individual OSRM requests a detour search makes
# run in _request_pool, so a job waiting on its requests can never starve them.

_job_pool = ThreadPoolExecutor(max_workers=config.OSRM_JOB_WORKERS, thread_name_prefix="route-job")
_request_pool = ThreadPoolExecutor(max_workers=config.OSRM_REQUEST_WORKERS, thread_name_prefix="osrm")


def submit(fn, *args, **kwargs) -> Future:
    """Run a routing job in a worker thread (tests replace this with an inline call)."""
    return _job_pool.submit(fn, *args, **kwargs)


# --- Detours around roadblocks ---------------------------------------------------------------

def osrm_route_options(points: Sequence[Tuple[float, float]], deadline: Optional[float] = None,
                       alternatives: bool = False) -> Optional[List[List[Position]]]:
    """Road geometries through (lat, lon) `points` from OSRM (several with alternatives); None if OSRM failed."""
    coords = ";".join("%.6f,%.6f" % _round(lon, lat) for lat, lon in points)
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    if alternatives:
        params["alternatives"] = "true"
    if len(points) > 2:
        params["continue_straight"] = "false"   # a via point may be a turn-around point
    data = _osrm_get("route", coords, params, config.OSRM_TIMEOUT_SEC, deadline)
    if not data or "routes" not in data:
        return None
    return [[LatLon(c[1], c[0]) for c in r["geometry"]["coordinates"]] for r in data["routes"]]


def ors_route_options(lat1: float, lon1: float, lat2: float, lon2: float, roadblocks: Sequence[Any],
                      deadline: Optional[float] = None) -> Optional[List[List[Position]]]:
    """OpenRouteService route avoiding the roadblocks (avoid_polygons), if OPENROUTESERVICE_API_KEY is set."""
    if not config.OPENROUTESERVICE_API_KEY:
        return None
    polygons = []
    for rb in roadblocks:
        r_deg = block_radius_km(rb) / 111.0
        polygons.append([[rb.lon - r_deg, rb.lat - r_deg], [rb.lon + r_deg, rb.lat - r_deg],
                         [rb.lon + r_deg, rb.lat + r_deg], [rb.lon - r_deg, rb.lat + r_deg],
                         [rb.lon - r_deg, rb.lat - r_deg]])
    body = {"coordinates": [[lon1, lat1], [lon2, lat2]],
            "options": {"avoid_polygons": {"type": "MultiPolygon", "coordinates": [[p] for p in polygons]}}}
    timeout = config.OSRM_TIMEOUT_SEC
    if deadline is not None:
        timeout = min(timeout, max(0.05, deadline - time.monotonic()))
    try:
        res = requests.post("https://api.openrouteservice.org/v2/directions/driving-car/geojson", json=body,
                            headers={"Authorization": config.OPENROUTESERVICE_API_KEY}, timeout=timeout)
        res.raise_for_status()
        return [[LatLon(c[1], c[0]) for c in f["geometry"]["coordinates"]]
                for f in res.json().get("features", [])]
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("ORS request failed (%s)", e)
        return None


def _gather(requests_: List[Tuple[str, Future]], deadline: float) -> Tuple[List[Tuple[str, List[Position]]], int]:
    """(source, geometry) from the requests that finished by the deadline, and how many failed or were late."""
    done, late = wait([f for _, f in requests_], timeout=max(0.0, deadline - time.monotonic()))
    for f in late:
        f.cancel()
    geometries, failed = [], len(late)
    for source, f in requests_:
        if f not in done:
            continue
        try:
            result = f.result()
        except Exception as e:  # one bad candidate must not sink the search
            log.warning("Detour candidate failed (%r)", e)
            result = None
        if result is None:
            failed += 1
        else:
            geometries += [(source, g) for g in result]
    return geometries, failed


def via_points(lat1: float, lon1: float, blocks: Sequence[Any], limit: int = 48) -> List[Tuple[float, float]]:
    """Points around the blocks (8 bearings x DETOUR_VIA_DISTANCES_KM), nearest blocks first, none inside a block."""
    points = []
    for block in sorted(blocks, key=lambda b: haversine(lat1, lon1, b.lat, b.lon)):
        for d in config.DETOUR_VIA_DISTANCES_KM:
            for b in config.DETOUR_VIA_BEARINGS:
                via = destination_point(block.lat, block.lon, b, max(d, block_radius_km(block) + 0.3))
                if not any(haversine(*via, rb.lat, rb.lon) <= block_radius_km(rb) for rb in blocks):
                    points.append(via)
    return points[:limit]


def _polyline_km(coords: Sequence[Position]) -> float:
    return sum(haversine(a.lat, a.lon, b.lat, b.lon) for a, b in zip(coords, coords[1:]))


def search_detour(lat1: float, lon1: float, lat2: float, lon2: float, roadblocks: Sequence[Any],
                  budget_sec: Optional[float] = None) -> Dict[str, Any]:
    """Shortest road route from (lat1, lon1) to (lat2, lon2) that avoids every active roadblock.

    Runs in a worker thread, never in the simulation tick. Everything shares one budget (DETOUR_BUDGET_SEC):
      1. ORS avoid_polygons (if OPENROUTESERVICE_API_KEY is set) and OSRM alternatives=true, in parallel;
      2. if none avoids every block: via points around the blocks the direct routes run into (8 bearings x
         1.5/3/5 km), one OSRM request start -> via -> destination each, all in parallel.
    Returns {"route": Route or None, "status": "ok" | "no_route" | "timeout", "requests", "failed",
    "candidates", "elapsed_sec"}. "no_route": OSRM answered and nothing avoids the blocks; "timeout": too many
    requests failed or missed the budget to tell.
    """
    t0 = time.monotonic()
    deadline = t0 + (config.DETOUR_BUDGET_SEC if budget_sec is None else budget_sec)
    blocks = [rb for rb in roadblocks if getattr(rb, "active", True)]

    def clear(coords):
        return len(coords) >= 2 and not route_intersects_blocks(coords, blocks, skip_start_inside=True)

    def result(status, route=None, n_requests=0, n_failed=0, n_candidates=0):
        return {"route": route, "status": status, "requests": n_requests, "failed": n_failed,
                "candidates": n_candidates, "elapsed_sec": round(time.monotonic() - t0, 3)}

    if not blocks:
        return result("ok", get_route(lat1, lon1, lat2, lon2))

    first = [("osrm", _request_pool.submit(osrm_route_options, [(lat1, lon1), (lat2, lon2)], deadline, True))]
    if config.OPENROUTESERVICE_API_KEY:
        first.append(("ors", _request_pool.submit(ors_route_options, lat1, lon1, lat2, lon2, blocks, deadline)))
    direct, direct_failed = _gather(first, deadline)
    good = [(src, g) for src, g in direct if clear(g)]
    n_requests, n_failed = len(first), direct_failed

    vias: List[Tuple[float, float]] = []
    if not good:
        # Go around the blocks the direct routes run into (all blocks if OSRM did not answer).
        hit = [b for b in blocks if any(block_segments(g, b, True, first_only=True) for _, g in direct)] or blocks
        vias = via_points(lat1, lon1, hit)
        around_requests = [("osrm", _request_pool.submit(osrm_route_options, [(lat1, lon1), via, (lat2, lon2)],
                                                         deadline)) for via in vias]
        around, via_failed = _gather(around_requests, deadline)
        good = [(src, g) for src, g in around if clear(g)]
        n_requests += len(around_requests)
        n_failed += via_failed

    if good:
        src, best = min(good, key=lambda sg: _polyline_km(sg[1]))
        coords = [p if isinstance(p, Position) else Position(lat=p.lat, lon=p.lon) for p in best]
        return result("ok", _make_route(lat1, lon1, lat2, lon2, coords, src), n_requests, n_failed, len(good))
    conclusive = direct_failed < len(first) and (n_failed - direct_failed) <= len(vias) / 2
    return result("no_route" if conclusive else "timeout", None, n_requests, n_failed)


def find_detour(lat1: float, lon1: float, lat2: float, lon2: float,
                roadblocks: Sequence[Any]) -> Optional[Route]:
    """search_detour's route, or None (no route / timed out). Network I/O: never under the world lock."""
    return search_detour(lat1, lon1, lat2, lon2, roadblocks)["route"]


# In-memory route cache: legs always start and end at a road point, so routes are reused across
# ETA calculations, ticks and runs. Routes are also looked up by id for the client.
_cache: Dict[Leg, Route] = {}
_by_id: Dict[str, Route] = {}
_cache_lock = threading.Lock()


def _key(lat1: float, lon1: float, lat2: float, lon2: float) -> Leg:
    return _round(lat1, lon1, lat2, lon2)


def _remember(leg: Leg, route: Route):
    with _cache_lock:
        _cache[_key(*leg)] = route
        _by_id[route["id"]] = route


def cached_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Optional[Route]:
    with _cache_lock:
        return _cache.get(_key(lat1, lon1, lat2, lon2))


def route_by_id(route_id: str) -> Optional[Route]:
    with _cache_lock:
        return _by_id.get(route_id)


def remember_route(route: Route):
    """Make a route (e.g. one a truck is driving) available by id."""
    with _cache_lock:
        _by_id[route["id"]] = route


def get_route(lat1: float, lon1: float, lat2: float, lon2: float) -> Route:
    """Cached route, fetching it if needed (network I/O; not under the world lock)."""
    route = cached_route(lat1, lon1, lat2, lon2)
    if route is None:
        route = fetch_road_route(lat1, lon1, lat2, lon2)
        _remember((lat1, lon1, lat2, lon2), route)
    return route


def prefetch(legs: Iterable[Leg]) -> List[Route]:
    """Fetch all missing legs in parallel (network I/O; not under the world lock)."""
    missing = sorted({_key(*leg) for leg in legs if cached_route(*leg) is None})
    if not missing:
        return []
    with ThreadPoolExecutor(max_workers=config.OSRM_PREFETCH_WORKERS) as pool:
        return list(pool.map(lambda leg: get_route(*leg), missing))


# --- Road distances (planning) -----------------------------------------------------------

_matrix: Dict[Leg, float] = {}  # road km between road points, from OSRM /table


def fetch_distance_matrix(points: Sequence[Tuple[float, float]]) -> Optional[List[List[Optional[float]]]]:
    """Road distances (km) between all points with one OSRM /table request; None if OSRM failed."""
    coords = ";".join("%.6f,%.6f" % _round(lon, lat) for lat, lon in points)
    data = _osrm_get("table", coords, {"annotations": "distance"}, config.OSRM_TIMEOUT_SEC)
    if not data or "distances" not in data:
        return None
    return [[None if d is None else d / 1000 for d in row] for row in data["distances"]]


def prefetch_distances(points: Sequence[Tuple[float, float]]) -> bool:
    """Road distances between every pair of points (network I/O; not under the world lock)."""
    points = list(dict.fromkeys(_round(lat, lon) for lat, lon in points))
    if len(points) < 2 or all(_key(*a, *b) in _matrix for a in points for b in points if a != b):
        return True
    matrix = fetch_distance_matrix(points)
    if matrix is None:
        log.warning("No road distance matrix; planning with straight-line distances")
        return False
    with _cache_lock:
        for i, a in enumerate(points):
            for j, b in enumerate(points):
                if i != j and matrix[i][j] is not None:
                    _matrix[_key(*a, *b)] = matrix[i][j]
    return True


def road_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Best known road distance: the leg's road route, else the OSRM table, else a straight line."""
    route = cached_route(lat1, lon1, lat2, lon2)
    if route is not None:
        return route["distance_km"]
    with _cache_lock:
        km = _matrix.get(_key(lat1, lon1, lat2, lon2))
    return km if km is not None else haversine(lat1, lon1, lat2, lon2)


def clear_cache():
    """Forget in-memory routes, distances and snaps (the disk cache is kept)."""
    with _cache_lock:
        _cache.clear()
        _by_id.clear()
        _matrix.clear()
        _snap_cache.clear()


# --- Snapping to the road network ------------------------------------------------

_snap_cache: Dict[Tuple[float, float], Optional[Dict]] = {}


def fetch_nearest_road(lat: float, lon: float, deadline: Optional[float] = None) -> Optional[Dict]:
    """OSRM /nearest: {road_lat, road_lon, snap_distance_m, road_name}, or None if OSRM is unreachable."""
    data = _osrm_get("nearest", "%.6f,%.6f" % _round(lon, lat), {"number": 1}, config.NEAREST_TIMEOUT_SEC, deadline)
    if not data or not data.get("waypoints"):
        return None
    wp = data["waypoints"][0]
    road_lon, road_lat = wp["location"]
    return {"road_lat": road_lat, "road_lon": road_lon,
            "snap_distance_m": haversine(lat, lon, road_lat, road_lon) * 1000, "road_name": wp.get("name") or None}


def snap_to_road(lat: float, lon: float, budget_sec: Optional[float] = None) -> Optional[Dict]:
    """Cached nearest road point for (lat, lon); None if it could not be determined within the budget
    (NEAREST_BUDGET_SEC). Network I/O: never under the world lock."""
    key = _round(lat, lon)
    with _cache_lock:
        if key in _snap_cache:
            return _snap_cache[key]
    deadline = time.monotonic() + (config.NEAREST_BUDGET_SEC if budget_sec is None else budget_sec)
    result = fetch_nearest_road(lat, lon, deadline=deadline)
    if result is not None:
        with _cache_lock:
            _snap_cache[key] = result
    return result


# --- Turn-by-turn directions for the driver view -------------------------------------------------------------

TURN_MIN_DEG = 30.0          # a smaller change of bearing is "continue"
TURN_WINDOW_M = 25.0         # bearings are taken over ~25 m either side of a vertex (ignores polyline jitter)
TURN_MERGE_M = 35.0          # bends closer than this are one manoeuvre
_directions_cache: Dict[str, dict] = {}


def _point_at(coords: Sequence[Position], cum: Sequence[float], d: float) -> Tuple[float, float]:
    """(lat, lon) at distance d (m) along the polyline."""
    d = max(0.0, min(d, cum[-1]))
    i = 1
    while i < len(cum) - 1 and cum[i] < d:
        i += 1
    a, b = coords[i - 1], coords[i]
    seg = cum[i] - cum[i - 1]
    f = 0.0 if seg <= 0 else (d - cum[i - 1]) / seg
    return a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f


def _modifier(angle: float) -> str:
    """Turn angle (degrees, + = right) -> OSRM-style modifier."""
    a = abs(angle)
    side = "right" if angle > 0 else "left"
    if a >= 160:
        return "uturn"
    if a >= 120:
        return f"sharp {side}"
    if a >= 55:
        return side
    return f"slight {side}"


def route_directions(route: Route) -> dict:
    """Manoeuvres along a route's own geometry (so they match exactly what the truck drives):
    [{"at_m", "type": depart|turn|arrive, "modifier", "street", "lat", "lon"}]. Street names come from OSRM
    /nearest a little after each turn (parallel, cached, NEAREST_BUDGET_SEC in total; missing -> None).
    Network I/O: never under the world lock. Cached per route id."""
    cached = _directions_cache.get(route["id"])
    if cached is not None:
        return cached
    coords, cum = route["coordinates"], route["cum_m"]
    length = cum[-1] if cum else 0.0
    candidates = []   # (at_m, angle)
    for i in range(1, len(coords) - 1):
        d = cum[i]
        if d < TURN_WINDOW_M or d > length - 5:
            continue
        before = _point_at(coords, cum, d - TURN_WINDOW_M)
        after = _point_at(coords, cum, d + TURN_WINDOW_M)
        b_in = bearing(*before, coords[i].lat, coords[i].lon)
        b_out = bearing(coords[i].lat, coords[i].lon, *after)
        angle = (b_out - b_in + 540) % 360 - 180
        if abs(angle) >= TURN_MIN_DEG:
            candidates.append((d, angle))
    turns: List[Tuple[float, float]] = []
    for d, angle in candidates:   # merge a bend's vertices, keep the strongest
        if turns and d - turns[-1][0] <= TURN_MERGE_M:
            if abs(angle) > abs(turns[-1][1]):
                turns[-1] = (d, angle)
        else:
            turns.append((d, angle))
    # A slight bend one way and then the other within 100 m is a lane shift / jog, not a turn.
    kept: List[Tuple[float, float]] = []
    for d, angle in turns:
        prev = kept[-1] if kept else None
        if (prev and d - prev[0] <= 100 and abs(prev[1]) < 55 and abs(angle) < 55
                and (prev[1] > 0) != (angle > 0)):
            kept.pop()
            continue
        kept.append((d, angle))
    turns = kept

    steps = [{"at_m": 0.0, "type": "depart", "modifier": "straight"}]
    steps += [{"at_m": round(d, 1), "type": "turn", "modifier": _modifier(a)} for d, a in turns]
    steps.append({"at_m": round(length, 1), "type": "arrive", "modifier": "straight"})
    for s in steps:
        s["lat"], s["lon"] = _point_at(coords, cum, s["at_m"]) if coords else (None, None)

    # Street names: the road just after each manoeuvre (all lookups in parallel, one shared budget).
    deadline = time.monotonic() + config.NEAREST_BUDGET_SEC
    probes = [_point_at(coords, cum, min(length, s["at_m"] + 30.0)) for s in steps[:-1]] if coords else []
    futures = [_request_pool.submit(snap_to_road, lat, lon, config.NEAREST_BUDGET_SEC) for lat, lon in probes]
    wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    complete = True
    for s, f in zip(steps, futures):
        name = None
        if f.done() and not f.cancelled() and f.exception() is None and f.result():
            name = f.result().get("road_name")
        else:
            complete = False
        s["street"] = name
    steps[-1]["street"] = None
    out = {"route_id": route["id"], "length_m": length, "approximate": route["approximate"], "steps": steps,
           "coordinates": [[p.lon, p.lat] for p in coords], "cum_m": cum}
    if complete:
        _directions_cache[route["id"]] = out
    return out
