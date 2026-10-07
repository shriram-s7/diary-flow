import time

import pytest
from fastapi.testclient import TestClient

import config
import routing
import simulation
import state
from api import app
from models import HubStatus, RainImpact, SimPhase, StopStatus, TruckStatus
from planner import plan_world
from timing import update_all_etas


def wait_for(client, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        world = client.get("/state").json()
        if predicate(world):
            return world
        time.sleep(0.05)
    raise AssertionError("timed out waiting for the simulation")


def planned_world():
    """Default scenario, planned, routes prefetched, execution begun (no thread)."""
    world = state.default_world()
    plan_world(world)
    routing.prefetch(simulation.plan_legs(world))
    simulation.begin_execution(world)
    return world


def run_headless(world, dt):
    for _ in range(int(24 * 60 / dt)):
        simulation.step(world, dt)
        if world.phase == SimPhase.COMPLETED:
            return world
    raise AssertionError("simulation did not complete")


def test_default_scenario_runs_to_completion_at_high_speed():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/simulation/speed", json={"speed": 600})  # 600 sim minutes per real second
        started = client.post("/simulation/start").json()
        assert started["phase"] == "EXECUTING"
        world = wait_for(client, lambda w: w["phase"] == "COMPLETED")
        plan = client.get("/plan").json()
        summary = client.get("/summary").json()

    plant = world["plant"]
    # All demand delivered.
    for hub in world["hubs"]:
        assert hub["delivered_l"] == hub["demand_l"]
        assert hub["remaining_l"] == 0
        assert hub["status"] == HubStatus.COMPLETED.value
    assert summary["total_delivered_l"] == summary["total_demand_l"] == 22400
    assert all(h["on_time"] for h in summary["hubs"])
    assert summary["on_time_pct"] == 100

    # Every truck ends at the plant; reserves never moved.
    reserve_ids = {r["truck_id"] for r in plan["reserve_trucks"]}
    for truck in world["trucks"]:
        assert (truck["position"]["lat"], truck["position"]["lon"]) == (plant["road_lat"], plant["road_lon"])
        assert truck["load_l"] == 0
        if truck["id"] in reserve_ids:
            assert truck["status"] == TruckStatus.RESERVE.value
            assert truck["km_driven"] == 0
        else:
            assert truck["status"] == TruckStatus.AT_PLANT.value
            # Fuel is burnt on the way back too, and nobody is refuelled.
            assert truck["return_km"] > 0
            assert truck["return_fuel_l"] > 0
            assert truck["fuel_used_l"] == pytest.approx(truck["km_driven"] / config.FUEL_KM_PER_L)
    assert summary["trucks_reserve"] == sorted(reserve_ids)
    assert summary["total_fuel_l"] == pytest.approx(sum(t["fuel_used_l"] for t in world["trucks"]))
    assert summary["total_cost_inr"] == pytest.approx(summary["total_km"] * config.COST_PER_KM)

    # Unused 5% buffer comes back to stock.
    assert plant["milk_available_l"] == pytest.approx(26000 - 22400)

    # Trucks left at DISPATCH_START, and actual arrivals match the planned ETAs
    # (offline routes are straight lines, like the planner's).
    dispatched = [d for d in world["decisions"] if d["type"] == "DISPATCHED"]
    assert len(dispatched) == len(plan["truck_plans"])
    assert all(d["time"] == pytest.approx(config.DISPATCH_START) for d in dispatched)
    planned = {(tp["truck_id"], s["hub_id"]): s["eta"] for tp in plan["truck_plans"] for s in tp["stops"]}
    for truck in world["trucks"]:
        for stop in truck["plan"]:
            assert stop["status"] == StopStatus.DONE.value
            assert stop["eta"] == pytest.approx(planned[(truck["id"], stop["hub_id"])], abs=0.01)
    assert world["decisions"][-1]["type"] == "SIMULATION_COMPLETED"


def test_results_do_not_depend_on_tick_size():
    fine = run_headless(planned_world(), dt=0.25)
    coarse = run_headless(planned_world(), dt=45)
    for a, b in zip(fine.trucks, coarse.trucks):
        assert a.km_driven == pytest.approx(b.km_driven)
        assert [s.eta for s in a.plan] == pytest.approx([s.eta for s in b.plan])
    assert fine.summary.completed_at == pytest.approx(coarse.summary.completed_at)


def test_delivery_updates_hub_and_truck_and_logs_litres():
    world = planned_world()
    truck = world.truck("TRK-01")
    first = truck.plan[0]
    load_before = truck.load_l
    while first.status != StopStatus.DONE:
        simulation.step(world, 1)
    hub = world.hub(first.hub_id)
    assert hub.delivered_l == first.drop_l
    assert truck.load_l == pytest.approx(load_before - first.drop_l)
    log = next(d for d in world.decisions if d.type == "DELIVERED" and d.truck_id == "TRK-01")
    assert log.details["delivered_l"] == first.drop_l
    assert f"{first.drop_l:,.0f} L" in log.summary


def test_truck_states_follow_lifecycle():
    world = planned_world()
    seen = {"TRK-01": [], "TRK-06": []}
    while world.phase == SimPhase.EXECUTING:
        for tid in seen:
            status = world.truck(tid).status
            if not seen[tid] or seen[tid][-1] != status:
                seen[tid].append(status)
        simulation.step(world, 1)
    for tid in seen:
        status = world.truck(tid).status
        if seen[tid][-1] != status:
            seen[tid].append(status)
    T = TruckStatus
    assert seen["TRK-01"] == [T.LOADING, T.EN_ROUTE, T.UNLOADING, T.EN_ROUTE, T.UNLOADING, T.RETURNING,
                              T.AT_PLANT]
    assert seen["TRK-06"] == [T.RESERVE]


def test_future_etas_stored_on_stops_and_hubs_every_tick():
    world = planned_world()
    simulation.step(world, 20)
    for truck in world.trucks:
        for stop in truck.plan:
            hub = world.hub(stop.hub_id)
            assert stop.eta is not None and stop.risk is not None
            assert stop.slack_min == pytest.approx(stop.window_end - stop.eta)
            assert hub.risk is not None
            upcoming = [s.eta for t in world.trucks for s in t.plan if s.hub_id == hub.id and s.arrived_at is None]
            assert hub.eta == (max(upcoming) if upcoming else None)    # never a past arrival
            if stop.arrived_at is None:
                assert stop.eta >= world.sim_time                       # future ETAs are in the future


def test_rain_and_traffic_slow_trucks():
    world = planned_world()
    while world.truck("TRK-05").status != TruckStatus.EN_ROUTE or world.truck("TRK-05").route_index < 3:
        simulation.step(world, 0.5)
    truck = world.truck("TRK-05")
    stop = truck.plan[0]
    base_eta = stop.eta

    world.impacts.append(RainImpact(id="rain-x", lat=truck.position.lat, lon=truck.position.lon,
                                    radius_km=50, created_at=world.sim_time))
    update_all_etas(world)
    rain_eta = stop.eta
    # Whole remaining leg is in the rain: driving time grows by 1/0.6.
    assert rain_eta - world.sim_time == pytest.approx((base_eta - world.sim_time) / config.RAIN_SPEED_FACTOR)
    world.impacts.clear()

    with TestClient(app) as client:
        state.world = world
        seg = truck.route_index + 2
        r = client.post("/impacts/traffic", json={"truck_id": "TRK-05", "segment_index": seg})
        assert r.status_code == 200, r.text
        impact_id = r.json()["id"]
        assert stop.eta > base_eta
        assert client.delete(f"/impacts/{impact_id}").status_code == 200
        assert stop.eta == pytest.approx(base_eta)
        assert client.post("/impacts/traffic",
                           json={"truck_id": "TRK-05", "segment_index": 10_000}).status_code == 400
        assert client.post("/impacts/traffic",
                           json={"truck_id": "TRK-06", "segment_index": 0}).status_code == 400
        assert client.delete("/impacts/nope").status_code == 404
        rain = client.post("/impacts/rain", json={"lat": 13.0, "lon": 80.2, "radius_km": 3}).json()
        assert rain["type"] == "rain" and rain["id"].startswith("rain-")


def test_controls_are_idempotent_and_reset_restores_the_start():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        # Before start: pause/resume/stop/reset are harmless no-ops.
        for path in ("pause", "resume", "stop", "reset"):
            assert client.post(f"/simulation/{path}").status_code == 200
        assert client.get("/state").json()["phase"] == "PLANNING"

        assert client.get("/plan").status_code == 404
        client.post("/simulation/speed", json={"speed": 5})
        assert client.post("/simulation/start").json()["phase"] == "EXECUTING"
        plan = client.get("/plan").json()  # created automatically by start
        assert client.post("/simulation/start").json()["message"] == "Simulation is already running"

        for _ in range(2):
            assert client.post("/simulation/pause").json()["paused"] is True
        frozen = client.get("/state").json()["sim_time"]
        time.sleep(0.4)
        assert client.get("/state").json()["sim_time"] == frozen
        for _ in range(2):
            assert client.post("/simulation/resume").json()["paused"] is False
        wait_for(client, lambda w: w["sim_time"] > frozen)

        for _ in range(2):
            assert client.post("/simulation/stop").json()["phase"] == "STOPPED"
        stopped_at = client.get("/state").json()["sim_time"]
        time.sleep(0.3)
        assert client.get("/state").json()["sim_time"] == stopped_at
        assert client.post("/simulation/start").status_code == 409  # reset first
        assert client.post("/plan").status_code == 409

        for _ in range(2):
            assert client.post("/simulation/reset").json()["phase"] == "PLANNING"
        world = client.get("/state").json()
        assert world["sim_time"] == config.DISPATCH_START
        assert all(h["delivered_l"] == 0 for h in world["hubs"])
        assert world["plant"]["milk_available_l"] == 26000
        assert client.get("/plan").json() == plan
        assert client.post("/simulation/start").json()["phase"] == "EXECUTING"
        client.post("/simulation/stop")
