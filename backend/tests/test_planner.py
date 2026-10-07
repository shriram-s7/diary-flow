import pytest
from fastapi.testclient import TestClient

import config
import state
from api import app
from models import Hub, Priority, Risk, TruckStatus
from planner import plan_dispatch
from timing import update_truck_etas


def plan_for(world):
    return plan_dispatch(world.hubs, world.trucks, world.plant, world.sim_time)


def drops_by_hub(plan):
    out = {}
    for tp in plan.truck_plans:
        for s in tp.stops:
            out[s.hub_id] = out.get(s.hub_id, 0) + s.drop_l
    return out


def test_default_scenario():
    world = state.default_world()
    plan = plan_for(world)

    assert plan.total_demand_l == 22400
    assert plan.total_planned_l == pytest.approx(22400)
    assert drops_by_hub(plan) == {h.id: pytest.approx(h.demand_l) for h in world.hubs}
    assert plan.unserved == []
    assert plan.warnings == []
    assert len(plan.reserve_trucks) >= 1
    assert all(r.reason.startswith("not needed: demand covered by") for r in plan.reserve_trucks)
    for tp in plan.truck_plans:
        assert tp.planned_l <= tp.capacity_l
        assert tp.load_l == pytest.approx(min(tp.capacity_l, tp.planned_l * (1 + config.LOAD_BUFFER_PCT / 100)))
        assert tp.utilisation_pct == pytest.approx(tp.planned_l / tp.capacity_l * 100)
        assert tp.fuel_l == pytest.approx(tp.total_km / config.FUEL_KM_PER_L)
        assert tp.cost_inr == pytest.approx(tp.total_km * config.COST_PER_KM)
        for s in tp.stops:
            assert s.eta < config.DEFAULT_WINDOW_END
            assert s.risk != Risk.LATE
    assert plan.windows_met


def test_fewest_trucks_largest_first():
    plan = plan_for(state.default_world())
    # 22,400 L needs 5 of the largest trucks (25,000 L); the 3,000 L truck stays in reserve.
    assert plan.trucks_used == 5
    assert [r.truck_id for r in plan.reserve_trucks] == ["TRK-06"]


def test_plan_etas_match_live_eta_function():
    world = state.default_world()
    plan = plan_for(world)
    for tp in plan.truck_plans:
        truck = world.truck(tp.truck_id)
        truck.plan = [s.model_copy() for s in tp.stops]
        update_truck_etas(world, truck)
        for planned, live in zip(tp.stops, truck.plan):
            assert live.eta == pytest.approx(planned.eta)
            assert live.slack_min == pytest.approx(planned.slack_min)


def test_big_hub_is_split_across_two_trucks():
    world = state.default_world()
    world.hubs = [Hub(id="BIG", name="Big Hub", lat=13.0850, lon=80.2101, demand_l=9000)]
    plan = plan_for(world)

    carrying = [(tp.truck_id, s.drop_l) for tp in plan.truck_plans for s in tp.stops if s.hub_id == "BIG"]
    assert len(carrying) == 2
    assert sum(d for _, d in carrying) == pytest.approx(9000)
    assert plan.trucks_used == 2
    for tp in plan.truck_plans:
        assert tp.planned_l <= tp.capacity_l
    assert plan.unserved == []
    assert any("split" in d for d in plan.decisions)


def test_demand_above_stock_reports_shortfall():
    world = state.default_world()
    world.plant.milk_available_l = 10000
    world.hub("HUB-TAM").priority = Priority.CRITICAL
    world.hub("HUB-ADY").priority = Priority.HIGH
    plan = plan_for(world)

    assert plan.total_planned_l == pytest.approx(10000)
    assert sum(s.unserved_l for s in plan.unserved) == pytest.approx(12400)
    assert all(s.reason == "insufficient plant stock" for s in plan.unserved)
    assert plan.warnings
    served = drops_by_hub(plan)
    assert served["HUB-TAM"] == pytest.approx(3100)   # Critical served first
    assert served["HUB-ADY"] == pytest.approx(2600)   # then High
    assert sum(tp.load_l for tp in plan.truck_plans) <= 10000 + 1e-6  # spare load limited by stock


def test_demand_above_fleet_capacity_reports_shortfall():
    world = state.default_world()
    world.trucks = world.trucks[:2]  # 12,000 L
    plan = plan_for(world)
    assert plan.total_planned_l == pytest.approx(12000)
    assert sum(s.unserved_l for s in plan.unserved) == pytest.approx(10400)
    assert all(s.reason == "insufficient fleet capacity" for s in plan.unserved)


def test_impossible_windows_warn_instead_of_raising():
    world = state.default_world()
    for hub in world.hubs:
        hub.window_end = config.DISPATCH_START + 5  # 03:05
    plan = plan_for(world)
    assert not plan.windows_met
    assert plan.total_planned_l == pytest.approx(22400)
    assert any("after its 03:05 window" in w for w in plan.warnings)


def test_no_trucks_or_no_hubs_does_not_raise():
    world = state.default_world()
    plan = plan_dispatch(world.hubs, [], world.plant, world.sim_time)
    assert plan.total_planned_l == 0 and sum(s.unserved_l for s in plan.unserved) == pytest.approx(22400)

    plan = plan_dispatch([], world.trucks, world.plant, world.sim_time)
    assert plan.trucks_used == 0 and len(plan.reserve_trucks) == 6


def test_deterministic():
    world = state.default_world()
    first = plan_for(world).model_dump()
    assert plan_for(world).model_dump() == first
    assert plan_dispatch(list(reversed(world.hubs)), list(reversed(world.trucks)),
                         world.plant, world.sim_time).model_dump() == first


def test_plan_endpoints():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        assert client.get("/plan").status_code == 404
        plan = client.post("/plan").json()
        assert client.get("/plan").json() == plan
        world = client.get("/state").json()
    statuses = {t["id"]: t["status"] for t in world["trucks"]}
    assert statuses["TRK-06"] == TruckStatus.RESERVE.value
    assert all(statuses[tp["truck_id"]] == TruckStatus.AT_PLANT.value for tp in plan["truck_plans"])
    assert world["sim_time"] == config.DISPATCH_START  # planning does not move anything
    assert world["decisions"][-1]["type"] == "PLAN_CREATED"
