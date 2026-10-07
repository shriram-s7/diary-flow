import pytest
from fastapi.testclient import TestClient

import config
import control_tower
import routing
import simulation
import state
from api import app
from models import (HubStatus, Priority, RecommendationStatus, SimPhase,
                    TruckStatus)
from planner import plan_world


def live_world(**hub_windows):
    """Default scenario, planned and executing (no thread); optional window overrides by hub id."""
    world = state.default_world()
    for hub_id, window_end in hub_windows.items():
        world.hub(hub_id).window_end = window_end
    plan_world(world)
    routing.prefetch(simulation.plan_legs(world))
    simulation.begin_execution(world)
    return world


def advance(world, until, dt=0.5):
    """Step the clock (route searches run inline in tests and are installed on the next step)."""
    while world.phase == SimPhase.EXECUTING and not until(world):
        simulation.step(world, dt)
    return world


def run_to_end(world):
    advance(world, lambda w: False)
    assert world.phase == SimPhase.COMPLETED
    return world


def routes(world):
    return {t.id: [(s.hub_id, s.drop_l, s.status) for s in t.plan] for t in world.trucks}


def api_client(world):
    client = TestClient(app)
    client.__enter__()
    state.world = world
    return client


# --- Increase -------------------------------------------------------------------

def test_tambaram_spike_covered_while_trucks_are_out():
    world = advance(live_world(), lambda w: w.sim_time >= 210)  # 03:30, trucks on the road
    tam = world.hub("HUB-TAM")
    client = api_client(world)
    try:
        r = client.put("/hubs/HUB-TAM/demand", json={"demand_l": tam.demand_l + 2500})
    finally:
        client.__exit__(None, None, None)
    assert r.status_code == 200, r.text
    body = r.json()
    rec = body["recommendation"]

    assert rec["spike"]["shortage_l"] == pytest.approx(2500)
    assert rec["status"] == RecommendationStatus.APPLIED.value
    kinds = {o["kind"] for o in rec["options"]}
    assert {"A", "B"} <= kinds  # trucks out with spare milk, and the reserve, were both evaluated
    chosen = next(o for o in rec["options"] if o["id"] == rec["chosen"])
    assert chosen["litres_covered"] == pytest.approx(2500)
    assert chosen["windows_broken"] == []
    assert chosen["kind"] in ("B", "C")  # no truck out has 2,500 L spare
    assert "Not chosen" in rec["reason"]

    decision = body["decision"]
    assert decision["type"] == "DEMAND_INCREASE"
    assert decision["details"]["reason"] == rec["reason"]
    assert len(decision["details"]["options"]) == len(rec["options"])
    assert all("etas" in row and "added_km" in row for row in decision["details"]["options"])

    run_to_end(world)
    tam = world.hub("HUB-TAM")
    assert tam.delivered_l == pytest.approx(5600)
    assert tam.status == HubStatus.COMPLETED
    assert all(h.status == HubStatus.COMPLETED for h in world.hubs)
    reserve = world.truck("TRK-06")
    assert reserve.km_driven > 0 and reserve.return_fuel_l > 0
    assert world.summary.on_time_pct == 100


def test_small_spike_tops_up_the_truck_already_going_there():
    world = advance(live_world(), lambda w: w.sim_time >= 190)
    owner = next(t for t in world.trucks if any(s.hub_id == "HUB-TAM" for s in t.plan))
    assert owner.spare_l >= 100
    result = control_tower.change_demand(world, world.hub("HUB-TAM"), 3200)
    rec = result["recommendation"]
    assert rec.chosen == f"A:{owner.id}"
    assert rec.options[0].parts[0].action == "top_up"
    assert rec.options[0].added_km == 0
    assert next(s for s in owner.plan if s.hub_id == "HUB-TAM").drop_l == pytest.approx(3200)
    run_to_end(world)
    assert world.hub("HUB-TAM").delivered_l == pytest.approx(3200)


def test_spike_with_no_way_to_cover_is_logged_not_raised():
    world = advance(live_world(), lambda w: w.sim_time >= 190)
    world.plant.milk_available_l = 0  # reserve cannot load anything
    for t in world.trucks:
        t.load_l = t.committed_l       # nobody out has spare milk
    result = control_tower.change_demand(world, world.hub("HUB-ADY"), 5000)
    assert result["recommendation"].chosen is None
    assert "cannot be covered" in result["decision"].summary


# --- Decrease -------------------------------------------------------------------

def test_decrease_cuts_drop_and_returns_milk_to_plant():
    world = advance(live_world(), lambda w: w.sim_time >= 185)
    truck = next(t for t in world.trucks if any(s.hub_id == "HUB-TAM" for s in t.plan))
    load = truck.load_l
    result = control_tower.change_demand(world, world.hub("HUB-TAM"), 1100)

    stop = next(s for s in truck.plan if s.hub_id == "HUB-TAM")
    assert stop.drop_l == pytest.approx(1100)
    assert truck.load_l == pytest.approx(load)            # milk stays on the truck...
    assert truck.spare_l == pytest.approx(load - 1100)    # ...shown as spare / returning
    assert result["decision"].type == "DEMAND_DECREASE"
    assert result["decision"].details["freed_l"] == pytest.approx(2000)
    assert "returns to the plant" in result["decision"].summary

    run_to_end(world)
    assert world.hub("HUB-TAM").delivered_l == pytest.approx(1100)
    assert world.hub("HUB-TAM").status == HubStatus.COMPLETED
    assert world.plant.milk_available_l == pytest.approx(26000 - 22400 + 2000)


def test_decrease_to_zero_sends_the_truck_home():
    world = advance(live_world(), lambda w: w.truck("TRK-04").status == TruckStatus.EN_ROUTE
                    and w.truck("TRK-04").route_index > 5)
    truck = world.truck("TRK-04")
    hub_id = truck.plan[0].hub_id
    assert len(truck.plan) == 1
    control_tower.change_demand(world, world.hub(hub_id), 0)
    assert truck.plan == []
    assert truck.status == TruckStatus.RETURNING
    run_to_end(world)
    assert truck.status == TruckStatus.AT_PLANT
    assert world.hub(hub_id).status == HubStatus.COMPLETED


# --- Approve mode ---------------------------------------------------------------

def test_approve_mode_changes_nothing_until_approved():
    world = advance(live_world(), lambda w: w.sim_time >= 210)
    client = api_client(world)
    try:
        assert client.put("/settings", json={"control_mode": "approve"}).json()["control_mode"] == "approve"
        before = routes(world)
        r = client.put("/hubs/HUB-TAM/demand", json={"demand_l": 5600}).json()
        rec_id = r["recommendation"]["id"]
        assert r["recommendation"]["status"] == "PENDING"
        assert r["decision"]["type"] == "RECOMMENDATION_PENDING"
        assert routes(world) == before
        assert [x["id"] for x in client.get("/recommendations").json()] == [rec_id]

        advance(world, lambda w: w.sim_time >= 215)  # time passes, still nothing changes
        assert {k: [s[:2] for s in v] for k, v in routes(world).items()} == \
               {k: [s[:2] for s in v] for k, v in before.items()}

        approved = client.post(f"/recommendations/{rec_id}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPLIED"
        assert routes(world) != before
        assert world.decisions[-1].type == "RECOMMENDATION_APPROVED"
        assert client.post(f"/recommendations/{rec_id}/approve").status_code == 409

        r2 = client.put("/hubs/HUB-ADY/demand", json={"demand_l": 2700}).json()
        before = routes(world)
        assert client.post(f"/recommendations/{r2['recommendation']['id']}/reject").json()["status"] == "REJECTED"
        assert routes(world) == before
        assert client.post("/recommendations/REC-999/approve").status_code == 404
    finally:
        client.__exit__(None, None, None)
    run_to_end(world)
    assert world.hub("HUB-TAM").delivered_l == pytest.approx(5600)
    assert world.hub("HUB-ADY").delivered_l == pytest.approx(2600)


# --- Priority -------------------------------------------------------------------

def test_critical_hub_is_served_next():
    world = live_world()  # trucks are loading
    truck = world.truck("TRK-01")
    first, second = [s.hub_id for s in truck.plan]
    result = control_tower.change_priority(world, world.hub(second), Priority.CRITICAL)
    assert [s.hub_id for s in truck.plan] == [second, first]
    assert result["decision"].type == "PRIORITY_CRITICAL"
    assert result["decision"].details["options"][0]["moved"] is True


def test_critical_not_moved_when_it_breaks_another_window():
    probe = live_world()
    first, second = [s.hub_id for s in probe.truck("TRK-01").plan]
    first_eta = probe.truck("TRK-01").plan[0].eta
    world = live_world(**{first: first_eta + 15})  # first stop has only 15 min slack
    truck = world.truck("TRK-01")
    assert [s.hub_id for s in truck.plan] == [first, second]
    result = control_tower.change_priority(world, world.hub(second), Priority.CRITICAL)
    assert [s.hub_id for s in truck.plan] == [first, second]
    row = result["decision"].details["options"][0]
    assert row["moved"] is False and row["windows_broken"]
    assert "keeps its order" in result["decision"].summary and "late" in result["decision"].summary


def test_critical_keeps_the_stop_in_progress():
    world = advance(live_world(), lambda w: w.truck("TRK-01").status == TruckStatus.EN_ROUTE)
    truck = world.truck("TRK-01")
    order = [s.hub_id for s in truck.plan]
    result = control_tower.change_priority(world, world.hub(order[1]), Priority.CRITICAL)
    assert [s.hub_id for s in truck.plan] == order  # already right after the current stop
    assert "already serves" in result["decision"].summary


# --- Before start and validation -------------------------------------------------

def test_before_start_changes_replan():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/plan")
        r = client.put("/hubs/HUB-TAM/demand", json={"demand_l": 5000}).json()
        assert r["decision"]["type"] == "DEMAND_CHANGED" and "Replanned" in r["decision"]["summary"]
        assert client.get("/plan").json()["total_planned_l"] == pytest.approx(22400 + 1900)
        r = client.put("/hubs/HUB-ADY/priority", json={"priority": "Critical"}).json()
        assert r["decision"]["type"] == "PRIORITY_CHANGED"


def test_bad_input_is_a_clear_400():
    world = advance(live_world(), lambda w: any(h.delivered_l > 0 for h in w.hubs))
    served = next(h for h in world.hubs if h.delivered_l > 0)
    client = api_client(world)
    try:
        r = client.put("/hubs/HUB-TAM/demand", json={"demand_l": -5})
        assert r.status_code == 400 and "demand_l" in r.json()["detail"]
        assert client.put("/hubs/HUB-TAM/demand", json={"demand_l": "lots"}).status_code == 400
        assert client.put("/hubs/HUB-TAM/demand", json={}).status_code == 400
        r = client.put("/hubs/HUB-TAM/priority", json={"priority": "Urgent"})
        assert r.status_code == 400 and "Critical" in r.json()["detail"]
        r = client.put(f"/hubs/{served.id}/demand", json={"demand_l": served.delivered_l - 1})
        assert r.status_code == 400 and "already received" in r.json()["detail"]
        assert client.put("/hubs/NOPE/demand", json={"demand_l": 10}).status_code == 404
        assert client.put("/settings", json={"control_mode": "maybe"}).status_code == 400
    finally:
        client.__exit__(None, None, None)


# --- Windows, adding and deleting hubs -------------------------------------------

def test_window_change_live_reports_late_stops():
    world = advance(live_world(), lambda w: w.sim_time >= 190)
    stop = next(s for t in world.trucks for s in t.plan if s.hub_id == "HUB-TAM")
    client = api_client(world)
    try:
        r = client.put("/hubs/HUB-TAM/window", json={"window_end": stop.eta - 10})
        assert r.status_code == 200, r.text
        assert r.json()["decision"]["type"] == "WINDOW_CHANGED"
        assert "Now late" in r.json()["decision"]["summary"]
        assert world.hub("HUB-TAM").risk.value == "Late"
        r = client.put("/hubs/HUB-TAM/window", json={"window_end": config.DISPATCH_START - 1})
        assert r.status_code == 400 and r.json()["detail"] == "The window must end between 03:30 and 08:00."
        assert client.put("/hubs/HUB-TAM/window", json={"window_end": 2000}).status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_hub_add_and_delete_replan_before_start():
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/plan")
        r = client.post("/hubs/manual", json={"name": "Perambur", "lat": 13.1210, "lon": 80.2330,
                                              "demand_l": 1000, "window_end": 330}).json()
        assert r["decision"]["type"] == "HUB_ADDED" and "Replanned" in r["decision"]["summary"]
        assert client.get("/plan").json()["total_planned_l"] == pytest.approx(23400)
        hub_id = r["hub"]["id"]
        assert client.delete(f"/hubs/{hub_id}").json()["decision"]["type"] == "HUB_DELETED"
        assert client.get("/plan").json()["total_planned_l"] == pytest.approx(22400)
        bad = client.post("/hubs/manual", json={"name": "", "lat": 13.1, "lon": 80.2, "demand_l": 0})
        assert bad.status_code == 400


def test_hub_added_during_run_is_a_spike():
    world = advance(live_world(), lambda w: w.sim_time >= 200)
    result = control_tower.add_hub(world, "Perambur", 13.1210, 80.2330, 1000, None, Priority.NORMAL,
                                   routing.snap_to_road(13.1210, 80.2330))
    rec = result["recommendation"]
    assert rec.spike.shortage_l == pytest.approx(1000) and rec.status == RecommendationStatus.APPLIED
    with pytest.raises(control_tower.ControlError):
        control_tower.delete_hub(world, result["hub"])
    run_to_end(world)
    assert world.hub(result["hub"].id).delivered_l == pytest.approx(1000)
