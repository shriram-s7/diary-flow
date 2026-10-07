"""Accuracy rules: road snapping, hub validation, live unloading, split hubs, stop order."""
import itertools

import pytest
from fastapi.testclient import TestClient

import config
import control_tower
import routing
import simulation
import state
from api import app
from models import Hub, Priority, SimPhase, StopStatus, TruckStatus
from planner import _Planner, plan_dispatch, plan_world
from timing import risk_from_slack
try:
    from conftest import SEA_LON
except ImportError:
    from tests.conftest import SEA_LON


def live_world(world=None):
    world = world or state.default_world()
    plan_world(world)
    routing.prefetch(simulation.plan_legs(world))
    simulation.begin_execution(world)
    return world


def advance(world, until, dt=0.25):
    while world.phase == SimPhase.EXECUTING and not until(world):
        simulation.step(world, dt)
    return world


def client_with(world):
    client = TestClient(app)
    client.__enter__()
    state.world = world
    return client


# --- Stop order --------------------------------------------------------------------

def test_no_route_longer_than_its_best_reordering():
    world = state.default_world()
    plan = plan_dispatch(world.hubs, world.trucks, world.plant, world.sim_time)
    planner = _Planner({h.id: h for h in world.hubs}, world.plant, plan.start_time)
    for tp in plan.truck_plans:
        assert len(tp.stops) <= 6
        truck = world.truck(tp.truck_id)
        route = [(s.hub_id, s.drop_l) for s in tp.stops]
        feasible = [list(p) for p in itertools.permutations(route) if planner.lateness(truck, list(p)) == 0]
        best = min(planner.order_cost(truck, p) for p in feasible)
        assert planner.order_cost(truck, route) <= best, f"{tp.truck_id} is not in its best order"


def test_equal_length_tours_serve_the_nearer_stop_first():
    world = state.default_world()
    plan = plan_dispatch(world.hubs, world.trucks, world.plant, world.sim_time)
    for tp in plan.truck_plans:
        if len(tp.stops) == 2:
            first, last = (world.hub(s.hub_id) for s in tp.stops)
            d_first = routing.haversine(*world.plant.road, *first.road)
            d_last = routing.haversine(*world.plant.road, *last.road)
            assert d_first <= d_last, f"{tp.truck_id} visits {first.name} before the nearer {last.name}"


def test_tambaram_is_never_served_before_t_nagar_on_one_truck():
    """The reported bug: from the northern plant a truck went to Tambaram before T. Nagar."""
    world = state.default_world()
    world.hubs = [h for h in world.hubs if h.id in ("HUB-TAM", "HUB-TNG")]
    for hub in world.hubs:
        hub.demand_l = 2000                     # both fit on one truck
    plan = plan_dispatch(world.hubs, world.trucks, world.plant, world.sim_time)
    assert [s.hub_id for s in plan.truck_plans[0].stops] == ["HUB-TNG", "HUB-TAM"]


# --- Road snapping and hub creation ------------------------------------------------------

def test_routes_and_arrivals_use_road_points():
    world = advance(live_world(), lambda w: w.truck("TRK-01").status == TruckStatus.UNLOADING)
    truck = world.truck("TRK-01")
    hub = world.hub(truck.plan[0].hub_id)
    assert (truck.position.lat, truck.position.lon) == hub.road
    assert hub.road != (hub.lat, hub.lon)                       # really the snapped point, not the raw one
    for lat1, lon1, lat2, lon2 in simulation.plan_legs(world):  # every leg runs road point -> road point
        ends = {world.plant.road} | {h.road for h in world.hubs}
        assert (lat1, lon1) in ends and (lat2, lon2) in ends


def test_snap_endpoint():
    with TestClient(app) as client:
        land = client.get("/snap", params={"lat": 13.08, "lon": 80.20}).json()
        assert land["ok"] and land["snap_distance_m"] == pytest.approx(20, abs=0.5)
        assert land["road_lat"] > land["lat"]
        sea = client.get("/snap", params={"lat": 13.08, "lon": SEA_LON + 0.05}).json()
        assert not sea["ok"] and sea["message"] == "No road access here — pick a point on a road"


@pytest.mark.parametrize("body, message", [
    ({"name": "Ab"}, "Hub name must be 3–30 characters."),
    ({"name": "x" * 31}, "Hub name must be 3–30 characters."),
    ({"name": "anna nagar"}, "A hub named anna nagar already exists."),
    ({"demand_l": 99}, "Demand must be between 100 L and 20,000 L."),
    ({"demand_l": 20_001}, "Demand must be between 100 L and 20,000 L."),
    ({"window_end": 3 * 60 + 29}, "The window must end between 03:30 and 08:00."),
    ({"window_end": 8 * 60 + 1}, "The window must end between 03:30 and 08:00."),
    ({"lon": SEA_LON + 0.05}, "No road access here — pick a point on a road"),
])
def test_hub_creation_is_validated(body, message):
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        payload = {"name": "Perambur", "lat": 13.1210, "lon": 80.2330, "demand_l": 1000, "window_end": 330, **body}
        r = client.post("/hubs/manual", json=payload)
        assert r.status_code == 400 and r.json()["detail"] == message
        assert len(client.get("/state").json()["hubs"]) == 8    # nothing was created


def test_hub_creation_rejected_when_road_service_is_down(monkeypatch):
    monkeypatch.setattr(routing, "fetch_nearest_road", lambda lat, lon, deadline=None: None)
    with TestClient(app) as client:
        r = client.post("/hubs/manual", json={"name": "Perambur", "lat": 13.12, "lon": 80.23, "demand_l": 1000})
        assert r.status_code == 400 and "road-routing service" in r.json()["detail"]


def test_created_hub_stores_raw_and_road_point():
    with TestClient(app) as client:
        hub = client.post("/hubs/manual", json={"name": " Perambur ", "lat": 13.1210, "lon": 80.2330,
                                                "demand_l": 1000, "window_end": 330}).json()["hub"]
        assert hub["name"] == "Perambur"
        assert (hub["lat"], hub["lon"]) == (13.1210, 80.2330)
        assert hub["road_lat"] == pytest.approx(13.1210 + 20 / 111_320) and hub["road_lon"] == 80.2330
        assert hub["snap_distance_m"] == pytest.approx(20, abs=0.5)


def test_csv_rows_are_snapped_and_validated():
    good = "id,name,lat,lon,demand_l,window_end\nH1,Perambur,13.12,80.23,1000,05:30\n"
    bad = good + "H2,Sea Hub,13.10,80.40,1000,05:30\nH3,Ok,13.0,80.2,50,05:30\n"
    with TestClient(app) as client:
        r = client.post("/upload/hubs", files={"file": ("hubs.csv", bad)})
        assert r.status_code == 400
        assert "row 3 (Sea Hub): No road access here" in r.json()["detail"]
        assert "row 4 (Ok): Hub name must be 3–30 characters." in r.json()["detail"]
        assert client.post("/upload/hubs", files={"file": ("hubs.csv", good)}).status_code == 200
        hub = client.get("/state").json()["hubs"][0]
        assert hub["snap_distance_m"] == pytest.approx(20, abs=0.5)


def test_fleet_editing_before_start_only():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/plan")
        assert client.post("/trucks", json={"id": "TRK-07", "capacity_l": 2000}).status_code == 200
        assert client.post("/trucks", json={"id": "TRK-07", "capacity_l": 2000}).status_code == 400
        assert client.put("/trucks/TRK-06", json={"capacity_l": 3500}).json()["type"] == "FLEET_CHANGED"
        assert client.delete("/trucks/TRK-07").status_code == 200
        assert "Replanned" in client.get("/state").json()["decisions"][-1]["summary"]
        client.post("/simulation/start")
        assert client.post("/trucks", json={"id": "TRK-08", "capacity_l": 2000}).status_code == 409
        client.post("/simulation/stop")


# --- Live unloading and split hubs -------------------------------------------------

def test_unloading_is_continuous_and_conserves_milk():
    world = advance(live_world(), lambda w: w.truck("TRK-01").status == TruckStatus.UNLOADING)
    truck = world.truck("TRK-01")
    stop = truck.plan[0]
    hub = world.hub(stop.hub_id)
    on_board_at_arrival = truck.load_l
    seen = []
    while stop.status == StopStatus.PENDING:
        seen.append(stop.delivered_l)
        assert truck.load_l + stop.delivered_l == pytest.approx(on_board_at_arrival)
        assert hub.delivered_l == pytest.approx(stop.delivered_l)
        assert stop.unload_min_left == pytest.approx(truck.activity_remaining_min)
        simulation.step(world, 1)
    assert seen[0] == 0                                     # docking first
    assert seen == sorted(seen) and 0 < seen[len(seen) // 2] < stop.drop_l
    assert stop.delivered_l == stop.drop_l and stop.completed_at is not None
    assert stop.completed_at - stop.arrived_at == pytest.approx(
        config.DOCK_TIME_MIN + stop.drop_l / config.UNLOAD_RATE_L_PER_MIN, abs=1e-6)


def test_split_hub_lists_every_delivery_and_worst_risk():
    world = state.default_world()
    world.hubs = [Hub(id="BIG", name="Big Hub", lat=13.0850, lon=80.2101, demand_l=9000)]
    world = advance(live_world(world), lambda w: w.sim_time >= 200)
    hub = world.hub("BIG")
    stops = [(t.id, s) for t in world.trucks for s in t.plan if s.hub_id == "BIG"]
    assert len(hub.deliveries) == len(stops) == 2
    for d in hub.deliveries:                                   # same numbers as the trucks' own stops
        stop = next(s for tid, s in stops if tid == d.truck_id)
        assert (d.drop_l, d.eta, d.slack_min, d.risk, d.delivered_l) == \
               (stop.drop_l, stop.eta, stop.slack_min, stop.risk, stop.delivered_l)
    assert hub.slack_min == min(s.slack_min for _, s in stops)
    assert hub.risk == risk_from_slack(min(s.slack_min for _, s in stops))   # worst of its stops


def test_hub_risk_is_the_worst_of_its_stops():
    world = live_world()
    world.hub("HUB-AMB").window_end = 200                # one stop would now be late
    simulation.step(world, 0.1)
    for hub in world.hubs:
        risks = [d.risk for d in hub.deliveries]
        order = ["Low", "Medium", "High", "Late"]
        assert hub.risk == max(risks, key=lambda r: order.index(r.value))
    assert world.hub("HUB-AMB").risk.value == "Late"


def test_hub_eta_is_never_in_the_past():
    world = live_world()
    while world.phase == SimPhase.EXECUTING:
        simulation.step(world, 2)
        for hub in world.hubs:
            assert hub.eta is None or hub.eta >= world.sim_time - 1e-9
        for truck in world.trucks:
            for s in truck.plan:
                if s.status == StopStatus.PENDING and s.arrived_at is None:
                    assert s.eta >= world.sim_time - 1e-9


def test_decrease_during_unloading_never_takes_back_delivered_milk():
    world = advance(live_world(), lambda w: w.truck("TRK-01").status == TruckStatus.UNLOADING)
    advance(world, lambda w: w.truck("TRK-01").plan[0].delivered_l > 1000)
    stop = world.truck("TRK-01").plan[0]
    hub = world.hub(stop.hub_id)
    delivered = stop.delivered_l
    control_tower.change_demand(world, hub, hub.delivered_l)  # nothing more needed
    assert stop.drop_l == pytest.approx(delivered)
    run = advance(world, lambda w: stop.status == StopStatus.DONE)
    assert stop.delivered_l == pytest.approx(delivered) and hub.remaining_l == 0
    assert run.truck("TRK-01").spare_l > 0


def test_priority_change_validates_value():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        assert client.put("/hubs/HUB-TAM/priority", json={"priority": Priority.CRITICAL.value}).status_code == 200
