import pytest

import config
import state
from models import Risk, Stop, TruckStatus
from routing import haversine
from timing import (format_clock, risk_from_slack, service_time_min, slack_min,
                    travel_time_min, update_truck_etas)


@pytest.mark.parametrize("drop_l, expected", [(0, 10), (100, 11), (2500, 35), (3500, 45)])
def test_service_time(drop_l, expected):
    assert service_time_min(drop_l) == pytest.approx(expected)


def test_travel_time_uses_city_speed():
    assert travel_time_min(30) == pytest.approx(60)
    assert travel_time_min(15, 30 * config.RAIN_SPEED_FACTOR) == pytest.approx(50)


def test_slack():
    assert slack_min(360, 330) == 30
    assert slack_min(360, 370) == -10


@pytest.mark.parametrize("slack, expected", [
    (120, Risk.LOW),
    (30, Risk.LOW),
    (29.99, Risk.MEDIUM),
    (10, Risk.MEDIUM),
    (9.99, Risk.HIGH),
    (0, Risk.HIGH),
    (-0.01, Risk.LATE),
    (-60, Risk.LATE),
])
def test_risk_bands_at_boundaries(slack, expected):
    assert risk_from_slack(slack) == expected


def test_format_clock():
    assert format_clock(180) == "03:00"
    assert format_clock(359.6) == "06:00"
    assert format_clock(65) == "01:05"


def test_eta_for_planned_stops():
    world = state.default_world()
    truck = world.trucks[0]
    amb, ann = world.hub("HUB-AMB"), world.hub("HUB-ANN")
    truck.plan = [Stop(hub_id=amb.id, drop_l=3200, window_end=amb.window_end),
                  Stop(hub_id=ann.id, drop_l=2800, window_end=ann.window_end)]
    update_truck_etas(world, truck)

    leg1 = haversine(*world.plant.road, *amb.road)
    leg2 = haversine(*amb.road, *ann.road)
    eta1 = config.DISPATCH_START + travel_time_min(leg1)
    eta2 = eta1 + service_time_min(3200) + travel_time_min(leg2)

    s1, s2 = truck.plan
    assert s1.eta == pytest.approx(eta1)
    assert s2.eta == pytest.approx(eta2)
    assert s2.slack_min == pytest.approx(amb.window_end - eta2)
    assert s2.risk == risk_from_slack(s2.slack_min)


def test_eta_while_servicing_keeps_arrival_and_adds_remaining_service():
    world = state.default_world()
    world.sim_time = 200
    truck = world.trucks[0]
    amb, ann = world.hub("HUB-AMB"), world.hub("HUB-ANN")
    truck.position.lat, truck.position.lon = amb.road
    truck.status = TruckStatus.UNLOADING
    truck.activity_remaining_min = 5
    truck.plan = [Stop(hub_id=amb.id, drop_l=3200, window_end=360, eta=190),
                  Stop(hub_id=ann.id, drop_l=2800, window_end=360)]
    update_truck_etas(world, truck)

    assert truck.plan[0].eta == 190
    leg = haversine(*amb.road, *ann.road)
    assert truck.plan[1].eta == pytest.approx(200 + 5 + travel_time_min(leg))


def test_format_clock_rounds_halves_up_like_the_frontend():
    assert format_clock(210.5) == "03:31"
    assert format_clock(212.5) == "03:33"  # Python's round() would give 03:32
    assert format_clock(1439.6) == "00:00"


def test_buffered_load_rule():
    from planner import buffered_load_l
    assert buffered_load_l(3000, 4000) == pytest.approx(3150)
    assert buffered_load_l(6000, 6000) == 6000
