"""Sim clock, sub-stepped motion, the /ws push, route geometry by id, and the OSRM disk cache + retries."""
import time

import pytest
import requests
from fastapi.testclient import TestClient

import config
import routing
import simulation
import state
from api import app
from models import SimPhase, TruckStatus
from planner import plan_world
from timing import route_etas, travel_time_min

# Captured before the offline fixture (conftest.py) replaces them.
real_osrm_get = routing._osrm_get
real_fetch_road_route = routing.fetch_road_route


def started_world():
    world = state.default_world()
    plan_world(world)
    routing.prefetch(simulation.plan_legs(world))
    simulation.begin_execution(world)
    return world


# --- Clock -------------------------------------------------------------------------

def test_one_x_is_real_time():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/simulation/speed", json={"speed": 1})
        t0 = client.post("/simulation/start").json()["sim_time"]
        real0 = time.monotonic()
        time.sleep(1.5)
        t1 = client.get("/state").json()["sim_time"]
        real = time.monotonic() - real0
        client.post("/simulation/stop")
    sim_seconds = (t1 - t0) * 60
    assert sim_seconds == pytest.approx(real, abs=0.25)  # 1 sim second per real second


def test_tick_is_split_into_sub_steps(monkeypatch):
    world = started_world()
    calls = []
    real_advance = simulation.advance_truck
    monkeypatch.setattr(simulation, "advance_truck", lambda w, t, now, dt: (calls.append(dt), real_advance(w, t, now, dt)))
    tick_min = config.SIM_UPDATE_INTERVAL_SEC * 600 / 60  # one tick at 600x = 60 sim seconds
    simulation.step(world, tick_min)
    per_truck = [dt for dt in calls[::len(world.trucks)]]
    assert len(per_truck) == 12  # 60 s in sub-steps of at most 5 s
    assert max(per_truck) * 60 <= config.MAX_SUBSTEP_SIM_SEC + 1e-9


def test_arrivals_match_planned_etas_at_120x():
    world = started_world()
    planned = {(t.id, s.hub_id): s.eta for t in world.trucks for s in t.plan}
    tick_min = config.SIM_UPDATE_INTERVAL_SEC * 120 / 60  # dt_sim = 0.1 s x 120
    for _ in range(200_000):
        simulation.step(world, tick_min)
        if world.phase == SimPhase.COMPLETED:
            break
    assert world.phase == SimPhase.COMPLETED
    for truck in world.trucks:
        for stop in truck.plan:
            eta = planned[(truck.id, stop.hub_id)]
            travel = eta - config.DISPATCH_START
            assert abs(stop.arrived_at - eta) <= 0.02 * travel


# --- Planning on road distances --------------------------------------------------------

def test_planning_etas_use_road_distances():
    plant = state.make_plant()
    hub = state.default_hubs()[0]
    straight = routing.haversine(plant.road_lat, plant.road_lon, hub.road_lat, hub.road_lon)
    routing._matrix[routing._key(plant.road_lat, plant.road_lon, hub.road_lat, hub.road_lon)] = straight * 1.4
    etas, _ = route_etas(config.DISPATCH_START, plant.road_lat, plant.road_lon, [(hub.road_lat, hub.road_lon, 1000)])
    assert etas[0] == pytest.approx(config.DISPATCH_START + travel_time_min(straight * 1.4))


# --- /ws and /routes/{id} ------------------------------------------------------------

def test_ws_pushes_truck_motion_and_route_geometry_by_id():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/simulation/speed", json={"speed": 600})
        client.post("/simulation/start")
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            assert "world" in first and first["seq"] == 0
            for _ in range(40):
                msg = ws.receive_json()
                driving = [t for t in msg["trucks"] if t["status"] in ("EN_ROUTE", "RETURNING") and t["route_id"]]
                if driving:
                    break
            assert driving, "no truck started driving"
        client.post("/simulation/stop")
        truck = driving[0]
        assert {"route_id", "distance_along_m", "speed_mps", "heading", "status", "approximate"} <= truck.keys()
        assert msg["server_time"] == pytest.approx(time.time(), abs=5)
        assert truck["speed_mps"] == pytest.approx(config.CITY_SPEED_KMPH / 3.6)
        route = client.get(f"/routes/{truck['route_id']}").json()
        assert route["approximate"] is True  # offline in tests: straight-line fallback
        assert len(route["coordinates"]) == len(route["cum_m"])
        assert route["cum_m"][-1] == pytest.approx(route["length_m"])
        assert 0 <= truck["distance_along_m"] <= route["length_m"] + 1
        assert client.get("/routes/nope").status_code == 404


def test_reroute_gets_a_new_route_id():
    world = started_world()
    while not any(t.status == TruckStatus.EN_ROUTE and t.route for t in world.trucks):
        simulation.step(world, 0.5)
    truck = next(t for t in world.trucks if t.status == TruckStatus.EN_ROUTE and t.route)
    old_id = truck.route_id
    dest = simulation.leg_destination(world, truck)
    via = routing.fallback_route(truck.position.lat, truck.position.lon, dest[0] + 0.01, dest[1])
    back = routing.fallback_route(dest[0] + 0.01, dest[1], *dest)
    detour = routing.route_from_points(via["coordinates"] + back["coordinates"][1:], "fallback")
    simulation.install_route(truck, detour)
    assert truck.route_id != old_id and truck.distance_along_route_m == 0
    assert routing.route_by_id(truck.route_id)["coordinates"] == truck.route


# --- OSRM disk cache, retries, approximate fallback -------------------------------------

@pytest.fixture
def disk_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "_osrm_get", real_osrm_get)
    monkeypatch.setattr(routing, "fetch_road_route", real_fetch_road_route)
    sleeps = []
    monkeypatch.setattr(routing, "_sleep", sleeps.append)
    return tmp_path, sleeps


OSRM_OK = {"code": "Ok", "routes": [{"distance": 1500.0, "geometry": {"coordinates": [[80.20, 13.00], [80.21, 13.01]]}}]}


def test_osrm_response_is_cached_on_disk(disk_cache, monkeypatch):
    tmp_path, _ = disk_cache
    calls = []
    monkeypatch.setattr(routing, "_http_get", lambda url, params, timeout: (calls.append(url), OSRM_OK)[1])
    first = routing.route_from_osrm(13.00, 80.20, 13.01, 80.21)
    routing.clear_cache()
    second = routing.route_from_osrm(13.00, 80.20, 13.01, 80.21)
    assert len(calls) == 1 and len(list(tmp_path.glob("*.json"))) == 1
    assert first["id"] == second["id"] and second["source"] == "osrm" and not second["approximate"]


def test_osrm_failure_retries_with_backoff_then_falls_back(disk_cache, monkeypatch):
    tmp_path, sleeps = disk_cache
    calls = []

    def down(url, params, timeout):
        calls.append(url)
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(routing, "_http_get", down)
    route = routing.get_route(13.00, 80.20, 13.01, 80.21)
    assert len(calls) == 1 + config.OSRM_RETRIES == 3
    assert sleeps == [config.OSRM_BACKOFF_SEC, config.OSRM_BACKOFF_SEC * 2]
    assert route["approximate"] is True and route["source"] == "fallback"
    assert not list(tmp_path.glob("*.json"))  # fallbacks are never cached on disk


def test_osrm_recovers_on_retry(disk_cache, monkeypatch):
    answers = [requests.Timeout("slow"), OSRM_OK]

    def flaky(url, params, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(routing, "_http_get", flaky)
    route = routing.get_route(13.00, 80.20, 13.01, 80.21)
    assert route["source"] == "osrm" and not route["approximate"]
