from concurrent.futures import Future

import pytest

import routing
import state
from models import Settings

SEA_LON = 80.32  # tests treat everything east of this as the Bay of Bengal (no road)


def fake_nearest_road(lat, lon, deadline=None):
    """Offline stand-in for OSRM /nearest: a road 20 m north of any land point; none at sea."""
    if lon > SEA_LON:
        return {"road_lat": lat, "road_lon": SEA_LON, "snap_distance_m": 2500.0, "road_name": None}
    road_lat = lat + 20 / 111_320
    return {"road_lat": road_lat, "road_lon": lon,
            "snap_distance_m": routing.haversine(lat, lon, road_lat, lon) * 1000, "road_name": "Test Road"}


def fake_route_options(points, deadline=None, alternatives=False):
    """Offline stand-in for OSRM /route through several points (detour search): one straight polyline."""
    coords = []
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        leg = routing.fallback_route(lat1, lon1, lat2, lon2)["coordinates"]
        coords += leg if not coords else leg[1:]
    return [coords]


def inline_submit(fn, *args, **kwargs):
    """Tests run route/detour searches synchronously; the next simulation step installs the result."""
    future = Future()
    try:
        future.set_result(fn(*args, **kwargs))
    except Exception as e:  # pragma: no cover
        future.set_exception(e)
    return future


@pytest.fixture(autouse=True)
def offline_routes(monkeypatch):
    """No network in tests: road routes are straight lines, road snapping is fake_nearest_road."""
    monkeypatch.setattr(routing, "fetch_road_route", routing.fallback_route)
    monkeypatch.setattr(routing, "fetch_nearest_road", fake_nearest_road)
    monkeypatch.setattr(routing, "_osrm_get", lambda *a, **k: None)
    monkeypatch.setattr(routing, "osrm_route_options", fake_route_options)
    monkeypatch.setattr(routing, "submit", inline_submit)
    routing.clear_cache()
    state.snapshot = None
    state.settings = Settings()
    yield
    routing.clear_cache()
