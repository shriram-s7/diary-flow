"""Disruption engine (CLAUDE.md "Disruptions"): roadblocks reroute at once, costly detours raise an alert but are
still taken, no route -> BLOCKED + red alert, clearing resumes, and nothing ever waits on the network or pauses."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import config
import disruptions
import routing
import simulation
import state
from api import app
from models import HubStatus, RoadblockImpact, SimPhase, TruckStatus
try:
    from test_simulation import planned_world
except ImportError:
    from tests.test_simulation import planned_world

from timing import update_all_etas


def en_route(world, truck_id, min_index=3):
    while world.truck(truck_id).status != TruckStatus.EN_ROUTE or world.truck(truck_id).route_index < min_index:
        simulation.step(world, 0.5)
    return world.truck(truck_id)


def block_ahead(world, truck, points_ahead=10, radius_m=150.0, block_id="rb-test", created_at=None):
    p = truck.route[truck.route_index + points_ahead]
    block = RoadblockImpact(id=block_id, lat=p.lat, lon=p.lon, radius_m=radius_m, road_name="Anna Salai",
                            created_at=world.sim_time if created_at is None else created_at)
    world.impacts.append(block)
    return block


def decisions(world, *types, truck_id=None):
    return [d for d in world.decisions if d.type in types and (truck_id is None or d.truck_id == truck_id)]


def run_until(world, predicate, dt=0.1, limit=2000):
    for _ in range(limit):
        if predicate(world):
            return world
        simulation.step(world, dt)
    raise AssertionError("condition not reached")


def never_inside(world, truck, block):
    r = routing.block_radius_km(block)
    return routing.haversine(truck.position.lat, truck.position.lon, block.lat, block.lon) > r


# --- Detour found -> reroute at once ------------------------------------------------------------------

def test_detour_reroutes_immediately_and_never_pauses():
    world = planned_world()
    truck = en_route(world, "TRK-01")
    block = block_ahead(world, truck, created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)
    run_until(world, lambda w: decisions(w, "AUTO_REROUTE", truck_id="TRK-01"))

    d = decisions(world, "AUTO_REROUTE", truck_id="TRK-01")[-1]
    assert "rerouted around closure on Anna Salai" in d.summary and "all windows OK" in d.summary
    assert truck.status == TruckStatus.EN_ROUTE and not truck.reroute_pending and truck.hold_index is None
    assert truck.instructions.startswith("Road closed ahead — rerouting (+") and truck.instructions.endswith("min)")
    assert not routing.route_intersects_blocks(truck.route[truck.route_index:], [block], skip_start_inside=True)
    assert world.paused is False
    assert not [a for a in world.alerts if a.status == "OPEN"]   # cheap detour: no card
    assert decisions(world, "ROADBLOCK_DETECTED", truck_id="TRK-01")

    # ... and it still delivers everything (around the block) once the run completes.
    run_until(world, lambda w: w.phase == SimPhase.COMPLETED, dt=1.0, limit=5000)
    assert all(h.remaining_l == 0 for h in world.hubs)


def test_costly_detour_is_taken_and_raises_an_alert_card():
    world = planned_world()
    truck = en_route(world, "TRK-02")
    first = truck.plan[0]
    world.hub(first.hub_id).window_end = first.eta + 0.3   # the detour now breaks this window
    block_ahead(world, truck, points_ahead=6, radius_m=1000.0,
                created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)   # area closure
    run_until(world, lambda w: decisions(w, "AUTO_REROUTE", truck_id="TRK-02"))

    assert truck.status == TruckStatus.EN_ROUTE        # took the detour: no HOLDING, no pause
    assert world.paused is False
    alert = next(a for a in world.alerts if a.truck_id == "TRK-02" and a.status == "OPEN")
    assert alert.kind == "DETOUR_COSTLY" and alert.default_option == "keep_detour"
    assert [o.id for o in alert.options] == ["keep_detour", "reassign_stop", "skip_stop"]
    assert alert.windows_broken and alert.extra_min is not None and alert.extra_km is not None
    assert decisions(world, "DETOUR_ALERT", truck_id="TRK-02")

    # Ignoring the card changes nothing: the truck keeps driving the detour.
    route_id = truck.route_id
    simulation.step(world, 1.0)
    assert truck.route_id == route_id and truck.status == TruckStatus.EN_ROUTE

    with TestClient(app) as client:
        state.world = world
        r = client.post("/decisions/resolve", json={"truck_id": "TRK-02", "action": "keep"})  # alias
        assert r.status_code == 200, r.text
    assert alert.status == "RESOLVED" and alert.resolution == "keep_detour"


def test_costly_detour_stop_handed_to_another_truck():
    world = planned_world()
    truck = en_route(world, "TRK-02")
    hub_id = truck.plan[0].hub_id
    world.hub(hub_id).window_end = truck.plan[0].eta + 0.3
    block_ahead(world, truck, points_ahead=6, radius_m=1000.0,
                created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)
    run_until(world, lambda w: any(a.truck_id == "TRK-02" and a.status == "OPEN" for a in w.alerts))

    with TestClient(app) as client:
        state.world = world
        r = client.post("/decisions/resolve", json={"truck_id": "TRK-02", "action": "reassign_stop"})
        assert r.status_code == 200, r.text
    assert all(s.hub_id != hub_id for s in simulation.pending_stops(truck))
    d = decisions(world, "STOP_REASSIGNED")[-1]
    assert d.details["handed_over"] and d.details["recommendation_id"]
    # The truck that handed the stop over is never the one chosen to cover it.
    assert all("TRK-02" not in o["id"] for o in d.details["options"])


def test_skip_stop_notifies_hub_and_drives_on():
    world = planned_world()
    truck = en_route(world, "TRK-02")
    hub_id = truck.plan[0].hub_id
    world.hub(hub_id).window_end = truck.plan[0].eta + 0.3
    block_ahead(world, truck, points_ahead=6, radius_m=1000.0,
                created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)
    run_until(world, lambda w: any(a.truck_id == "TRK-02" and a.status == "OPEN" for a in w.alerts))
    with TestClient(app) as client:
        state.world = world
        assert client.post("/decisions/resolve", json={"truck_id": "TRK-02", "action": "skip"}).status_code == 200
    assert "notified" in decisions(world, "STOP_SKIPPED")[-1].summary
    assert all(s.hub_id != hub_id for s in simulation.pending_stops(truck))
    assert truck.status in (TruckStatus.EN_ROUTE, TruckStatus.RETURNING)


# --- No detour -> BLOCKED + red alert -------------------------------------------------------------------

def blocked_world():
    world = planned_world()
    truck = en_route(world, "TRK-04")
    hub = world.hub(truck.plan[0].hub_id)
    block = RoadblockImpact(id="block-impassable", lat=hub.road_lat, lon=hub.road_lon, radius_m=2000.0,
                            road_name="Ennore High Road", created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)
    world.impacts.append(block)
    run_until(world, lambda w: w.truck("TRK-04").status == TruckStatus.BLOCKED)
    simulation.step(world, 0.1)   # the background reachability check (plant -> hub) lands
    return world, truck, hub, block


def test_no_route_blocks_truck_with_red_alert():
    world, truck, hub, block = blocked_world()
    assert "Stop safely — no route available" in truck.instructions
    assert never_inside(world, truck, block)
    assert world.paused is False and world.running
    d = decisions(world, "NO_ROUTE", truck_id="TRK-04")[-1]
    assert "all routes closed" in d.summary
    assert d.details["driver_name"] == truck.driver_name and d.details["driver_phone"] == truck.driver_phone
    alert = next(a for a in world.alerts if a.truck_id == "TRK-04" and a.status == "OPEN")
    assert alert.kind == "NO_ROUTE"
    assert [o.id for o in alert.options] == ["call_driver", "return_to_plant", "reassign_stop", "wait_for_clearance"]
    assert hub.status == HubStatus.UNREACHABLE

    # The clock keeps running while the truck waits.
    t0 = world.sim_time
    simulation.step(world, 1.0)
    assert world.sim_time > t0 and truck.status == TruckStatus.BLOCKED


def test_clearing_roadblock_resumes_blocked_truck_immediately():
    world, truck, hub, block = blocked_world()
    with TestClient(app) as client:
        state.world = world
        assert client.post("/decisions/resolve",
                           json={"truck_id": "TRK-04", "action": "wait"}).status_code == 200
        assert client.delete(f"/impacts/{block.id}").status_code == 200
    assert truck.status == TruckStatus.EN_ROUTE            # at once, not on a later tick
    assert truck.route and "resuming delivery route" in truck.instructions
    assert hub.status == HubStatus.PENDING
    assert decisions(world, "ROADBLOCK_CLEARED", truck_id="TRK-04")
    assert not [a for a in world.alerts if a.truck_id == "TRK-04" and a.status == "OPEN"]
    pos = (truck.position.lat, truck.position.lon)
    simulation.step(world, 1.0)
    assert (truck.position.lat, truck.position.lon) != pos


def test_blocked_truck_returns_to_plant_on_request():
    world, truck, hub, block = blocked_world()
    with TestClient(app) as client:
        state.world = world
        r = client.post("/decisions/resolve", json={"truck_id": "TRK-04", "action": "return_to_plant"})
        assert r.status_code == 200, r.text
        assert client.post("/decisions/resolve", json={"truck_id": "TRK-04", "action": "fly"}).status_code == 400
    assert not simulation.pending_stops(truck)
    run_until(world, lambda w: truck.status in (TruckStatus.RETURNING, TruckStatus.BLOCKED, TruckStatus.AT_PLANT))
    assert truck.status != TruckStatus.EN_ROUTE


def test_search_timing_out_twice_blocks_the_truck(monkeypatch):
    """OSRM unreachable: every request fails, the search reports a timeout, retries once, then BLOCKED."""
    monkeypatch.setattr(routing, "osrm_route_options", lambda *a, **k: None)
    world = planned_world()
    truck = en_route(world, "TRK-01")
    block = block_ahead(world, truck, created_at=world.sim_time - config.ROADBLOCK_DETECT_DELAY_MIN)
    run_until(world, lambda w: w.truck("TRK-01").status == TruckStatus.BLOCKED)
    assert len(decisions(world, "DETOUR_SEARCH_RETRY", truck_id="TRK-01")) == config.DETOUR_MAX_ATTEMPTS - 1
    d = decisions(world, "NO_ROUTE", truck_id="TRK-01")[-1]
    assert "timed out 2×" in d.summary and d.details["search_status"] == "timeout"
    assert never_inside(world, truck, block)
    assert world.truck("TRK-01").status == TruckStatus.BLOCKED and not world.paused


# --- Detection --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sim_speed", [1, 120])
def test_roadblock_detection_delay_at_1x_and_120x(sim_speed):
    """Detected ROADBLOCK_DETECT_DELAY_MIN sim minutes after placement (or within 300 m), at any sim speed:
    the clock advances in real ticks of 0.1 s x speed."""
    world = planned_world()
    truck = en_route(world, "TRK-03")
    world.sim_speed = sim_speed
    far = truck.route[-2]
    world.impacts.append(RoadblockImpact(id="block-delay", lat=far.lat, lon=far.lon, radius_m=150.0,
                                         created_at=world.sim_time))
    assert routing.haversine(truck.position.lat, truck.position.lon, far.lat, far.lon) > 1.0
    tick = config.SIM_UPDATE_INTERVAL_SEC * sim_speed / 60.0   # sim minutes per real tick
    t_place = world.sim_time
    while world.sim_time - t_place < config.ROADBLOCK_DETECT_DELAY_MIN - tick - 1e-9:
        simulation.step(world, tick)
        gap_m = (routing.haversine(truck.position.lat, truck.position.lon, far.lat, far.lon) - 0.15) * 1000
        if gap_m > config.ROADBLOCK_PROXIMITY_DETECT_M:
            assert not decisions(world, "ROADBLOCK_DETECTED"), f"detected early at {world.sim_time - t_place:.2f} min"
    for _ in range(3):
        simulation.step(world, tick)
    d = decisions(world, "ROADBLOCK_DETECTED", truck_id="TRK-03")
    assert d, "not detected after the delay"
    assert d[0].time - t_place <= config.ROADBLOCK_DETECT_DELAY_MIN + 2 * tick + 1e-6


def test_truck_keeps_driving_while_detour_is_searched(monkeypatch):
    """The search runs in a worker thread: the tick never waits, the truck drives on up to its hold point
    (never into the block), and the detour is installed when the search answers."""
    pool = ThreadPoolExecutor(max_workers=2)
    release = threading.Event()
    real_search = routing.search_detour

    def slow_search(*args, **kwargs):
        release.wait(5)
        return real_search(*args, **kwargs)

    monkeypatch.setattr(routing, "submit", pool.submit)
    monkeypatch.setattr(routing, "search_detour", slow_search)
    world = planned_world()
    truck = en_route(world, "TRK-01")
    block = block_ahead(world, truck, points_ahead=12)
    world.sim_speed = 1
    run_until(world, lambda w: truck.reroute_pending, dt=0.05)   # seen on approach (within 300 m)

    start = (truck.position.lat, truck.position.lon)
    for _ in range(200):   # 10 sim minutes; each step returns at once although the search is still running
        t0 = time.perf_counter()
        simulation.step(world, 0.05)
        assert time.perf_counter() - t0 < 0.2
        assert never_inside(world, truck, block)
    assert truck.reroute_pending and (truck.position.lat, truck.position.lon) != start  # it moved on
    assert truck.route_index > truck.hold_index                                         # waiting at the hold point

    release.set()
    deadline = time.time() + 5
    while truck.reroute_pending and time.time() < deadline:
        simulation.step(world, 0.05)
        time.sleep(0.01)
    assert not truck.reroute_pending and decisions(world, "AUTO_REROUTE", truck_id="TRK-01")
    pool.shutdown()


def test_add_roadblock_api_returns_at_once_and_never_pauses():
    world = planned_world()
    truck = en_route(world, "TRK-01")
    nxt = truck.route[truck.route_index]
    frac = min(1.0, 0.25 / routing.haversine(truck.position.lat, truck.position.lon, nxt.lat, nxt.lon))
    p = routing.Position(lat=truck.position.lat + (nxt.lat - truck.position.lat) * frac,
                         lon=truck.position.lon + (nxt.lon - truck.position.lon) * frac)   # 250 m ahead
    with TestClient(app) as client:
        state.world = world
        r = client.post("/impacts/roadblock", json={"lat": p.lat, "lon": p.lon, "radius_m": 150.0})
        assert r.status_code == 200, r.text
        assert truck.reroute_pending           # within 300 m: detected at once, search submitted
        assert world.paused is False
        assert "pause_on_alerts" not in client.get("/settings").json()
        assert "PAUSE_ON_ALERTS_DEFAULT" not in client.get("/config").json()


# --- Rain -------------------------------------------------------------------------------------------------

def test_rain_increases_eta_and_updates_risk():
    """Rain slows down trucks (x0.6) and increases ETAs; clearing it restores them."""
    world = planned_world()
    truck = en_route(world, "TRK-01")
    stop = truck.plan[0]
    base_eta = stop.eta
    with TestClient(app) as client:
        state.world = world
        r = client.post("/impacts/rain", json={"lat": truck.position.lat, "lon": truck.position.lon, "radius_km": 5.0})
        assert r.status_code == 200
        assert stop.eta > base_eta
        assert client.delete(f"/impacts/{r.json()['id']}").status_code == 200
    update_all_etas(world)
    assert stop.eta == pytest.approx(base_eta)


# --- Resilience -------------------------------------------------------------------------------------------

def test_a_failing_tick_is_logged_and_the_clock_keeps_running(monkeypatch):
    real_step = simulation.step
    calls = {"n": 0}

    def flaky_step(world, dt):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ZeroDivisionError("boom")
        return real_step(world, dt)

    monkeypatch.setattr(simulation, "step", flaky_step)
    with TestClient(app) as client:
        client.post("/scenarios/chennai-default/load")
        client.post("/simulation/speed", json={"speed": 60})
        client.post("/simulation/start")
        time.sleep(1.0)
        health = client.get("/health").json()
        world = client.get("/state").json()
        client.post("/simulation/stop")
    assert health["sim_thread_alive"] and health["ticks"]["errors"] == 1
    assert any(d["type"] == "SIM_ERROR" and "boom" in d["summary"] for d in world["decisions"])
    assert world["sim_time"] > config.DISPATCH_START - config.LOADING_TIME_MIN + 0.5
