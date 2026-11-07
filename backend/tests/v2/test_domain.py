from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from v2.domain.formulas import (buffer_units, capped_spare_units, hub_default, pack,
                               requires_approval, risk, service_minutes, slack_minutes,
                               stop_times, suggestion_units, travel_minutes, trip_cost, utilisation)
from v2.domain.lifecycle import InvalidTransition, TRANSITIONS, transition
from v2.domain.planning import (AllocationLine, Assignment, PlannedStop, PlannedTrip,
                                PlannerInput, PlannerOutput, Unplanned, Vehicle, validate_coverage)
from v2.domain.settings import DEFAULTS, Settings
from v2.domain.timeline import BUSINESS_ZONE, daily_events, shift_on
from v2.domain.types import CommandContext, CommandResult, Lane, Packaging, Scope, Shift


def product(**overrides):
    data = dict(sku_id="milk", dimension="ml", pack_size=500, units_per_container=24,
                full_container_g=13000, container_type="crate", returnable=True, lane="chilled")
    return Packaging(**(data | overrides))


def test_partial_crates_are_per_stop_and_not_summed_before_packing():
    p = product()
    q = pack(25, p)
    assert (q.units, q.slots, q.litres, q.planning_g) == (25, 2, Decimal("12.5"), 26000)
    assert q.product_kg is None and q.measured_gross_g is None
    assert pack(1, p).slots + pack(1, p).slots == 2
    assert pack(0, p).slots == 0


def test_cartons_and_measured_mass_are_distinct():
    p = product(dimension="g", pack_size=200, units_per_container=25,
                full_container_g=6000, container_type="carton", returnable=False,
                unit_gross_g=205, tare_g=300)
    q = pack(26, p)
    assert q.slots == 2 and q.returnable_containers == 0 and q.litres is None
    assert q.product_kg == Decimal("5.2")
    assert q.measured_gross_g == 5930 and q.planning_g == 12000


@pytest.mark.parametrize("bad", [-1, 1.2, True, "24", float("nan")])
def test_unit_counts_are_never_coerced(bad):
    with pytest.raises(ValueError):
        pack(bad, product())


@pytest.mark.parametrize("overrides", [dict(units_per_container=0), dict(pack_size=-1),
    dict(full_container_g=True), dict(unit_gross_g=500), dict(unit_gross_g=600, tare_g=100),
    dict(dimension="litres"), dict(returnable="false")])
def test_packaging_rejects_inconsistent_dimensions_and_weight(overrides):
    with pytest.raises(ValidationError):
        product(**overrides)


@pytest.mark.parametrize("base,requested,limit,expected", [
    (100, 130, "0.3", False), (100, 131, "0.3", True),
    (100, 70, "0.3", False), (100, 69, "0.3", True),
    (100, 120, "0.2", False), (100, 121, "0.2", True),
    (0, 0, "0.3", False), (0, 1, "0.3", True),
])
def test_approval_boundaries(base, requested, limit, expected):
    assert requires_approval(base, requested, limit) is expected


def test_spare_units_respect_both_capacities_supply_and_lane():
    p = product()
    desired = buffer_units([24], DEFAULTS.van_buffer_z)
    assert desired == 12 and pack(desired, p).slots == 1
    assert capped_spare_units(desired, 100, 5, 12999, p, frozenset({Lane.CHILLED})) == 0
    assert capped_spare_units(desired, 7, 5, 13000, p, frozenset({Lane.CHILLED})) == 7
    assert capped_spare_units(desired, 100, 5, 13000, p, frozenset({Lane.AMBIENT})) == 0
    assert capped_spare_units(100, 100, 1, 999999, p, frozenset({Lane.CHILLED})) == 24


def test_buffer_exact_squares_fractional_inputs_and_hub_stock():
    assert buffer_units([3, 4]) == 5
    assert buffer_units([1, 1]) == 2
    assert buffer_units([Decimal("0.3"), Decimal("0.4")], 2) == 1
    assert buffer_units([], 1) == 0
    assert hub_default([100, 200], [3, 4], 40) == 265
    assert hub_default([100], [0], 150) == 0
    with pytest.raises(ValueError):
        hub_default([100], [], 0)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", -1, True, "not a number"])
def test_nonfinite_and_negative_quantities_fail(bad):
    for fn in (suggestion_units, lambda x: buffer_units([x]), lambda x: travel_minutes(x)):
        with pytest.raises(ValueError):
            fn(bad)


def test_travel_service_cost_and_capacity_units():
    assert travel_minutes(30) == 60
    assert travel_minutes(30, rain=True, traffic=True) == 250
    assert travel_minutes(30, settings=Settings(planning_speed_kmph=60)) == 30
    assert service_minutes(25) == 10
    assert trip_cost(20) == (Decimal(4), Decimal(360))
    assert utilisation(1, 4) == 25
    with pytest.raises(ValueError):
        utilisation(5, 4)
    with pytest.raises(ValueError):
        travel_minutes(2, speed_kmph=0)


@pytest.mark.parametrize("slack,expected", [(30,"Low"),(29,"Medium"),(10,"Medium"),
                                           (9,"High"),(0,"High"),(-1,"Late")])
def test_risk_boundaries(slack, expected):
    assert risk(slack) == expected


def test_overnight_completion_and_waiting():
    departure = datetime(2026, 10, 8, 23, 50, tzinfo=BUSINESS_ZONE)
    arrival, completed = stop_times(departure, 20, 25,
                                    opens_at=datetime(2026, 10, 9, 0, 30, tzinfo=BUSINESS_ZONE))
    assert arrival == datetime(2026, 10, 9, 0, 10, tzinfo=BUSINESS_ZONE)
    assert completed == datetime(2026, 10, 9, 0, 40, tzinfo=BUSINESS_ZONE)
    assert slack_minutes(completed - timedelta(minutes=1), completed) == -1
    with pytest.raises(ValueError):
        slack_minutes(datetime(2026, 10, 9), completed)


def test_event_dates_are_previous_day_and_delivery_day_in_utc():
    events = dict(daily_events(date(2026, 10, 9)))
    assert events["suggestions"] == datetime(2026, 10, 8, 8, 30, tzinfo=timezone.utc)
    assert events["hub_dispatch"].astimezone(BUSINESS_ZONE).date() == date(2026, 10, 9)
    assert events["day_close"] - events["suggestions"] == timedelta(hours=16)
    shift = shift_on(date(2026, 10, 8), time(20), time(2))
    assert shift.end - shift.start == timedelta(hours=6)
    with pytest.raises(ValueError):
        shift_on(date(2026, 10, 8), time(2), time(2))


@pytest.mark.parametrize("changes", [dict(booth_cutoff="13:00"), dict(rain_factor=0),
    dict(traffic_factor="NaN"), dict(risk_medium_minutes=30), dict(speed_presets=(1,1)),
    dict(speed_presets=(1,3600)), dict(app_name=" "), dict(detour_attempts=True)])
def test_invalid_settings_rejected(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)


def test_immutable_contracts_and_scope_shapes():
    assert Scope(role="booth", hub_id="h", booth_id="b").booth_id == "b"
    with pytest.raises(ValidationError):
        Scope(role="driver", hub_id="h")
    with pytest.raises(ValidationError):
        DEFAULTS.app_name = "other"
    with pytest.raises(ValidationError):
        CommandContext(command_id="c", run_id="r", actor_id="a", expected_revision=0,
                       delivery_date=date(2026,10,9), occurred_at=datetime(2026,10,9))


def test_result_separates_security_and_business_time_and_serializes_decimals():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    result = CommandResult[Packaging](correlation_id="c", revision=1, sim_time=now,
                                     wall_time=now + timedelta(hours=1), data=product())
    restored = CommandResult[Packaging].model_validate_json(result.model_dump_json())
    assert restored == result
    with pytest.raises(ValidationError):
        CommandResult[Packaging](correlation_id="c", revision=1, sim_time=now,
                                 wall_time=datetime(2026, 10, 9), data=product())


def test_all_lifecycles_reject_skips_and_terminal_reopening():
    assert transition("order", "confirmed", "locked") == "locked"
    for kind, states in TRANSITIONS.items():
        for current, targets in states.items():
            for target in states:
                if target in targets:
                    assert transition(kind, current, target) == target
                else:
                    with pytest.raises(InvalidTransition):
                        transition(kind, current, target)
    with pytest.raises(InvalidTransition):
        transition("unknown", "open", "closed")


def planning_fixture():
    start = datetime(2026,10,9,1,tzinfo=BUSINESS_ZONE)
    request = PlannerInput(run_id="r", revision=3, delivery_date=start.date(), level="hub",
        source_id="h", dispatch_at=start,
        allocations=(AllocationLine(allocation_id="a", receiver_id="b", sku_id="milk", units=25,
                                    completion_deadline=start+timedelta(hours=4)),),
        products=(product(),), vehicles=(Vehicle(vehicle_id="v", driver_id="d", home_id="h",
            capacity_slots=3, capacity_g=39000, lane_configurations=(frozenset({Lane.CHILLED}),),
            shift=Shift(start=start, end=start+timedelta(hours=5))),), stock=(), road_legs=())
    stop = PlannedStop(stop_id="s", receiver_id="b", arrival_at=start+timedelta(minutes=20),
        completed_at=start+timedelta(minutes=30), assignments=(Assignment(allocation_id="a", units=20),))
    trip = PlannedTrip(vehicle_id="v", driver_id="d", departure_at=start,
        return_at=start+timedelta(hours=1), stops=(stop,), total_km=20)
    result = PlannerOutput(input_revision=3, trips=(trip,),
                           unplanned=(Unplanned(allocation_id="a", units=5, reason="stock"),))
    return request, result


def test_planner_contract_coverage_preserves_shortfall():
    request, result = planning_fixture()
    validate_coverage(request, result)
    with pytest.raises(ValueError, match="exactly"):
        validate_coverage(request, result.model_copy(update={"unplanned": ()}))
    with pytest.raises(ValueError, match="stale"):
        validate_coverage(request, result.model_copy(update={"input_revision": 2}))
    with pytest.raises(ValueError, match="duplicate stop"):
        validate_coverage(request, result.model_copy(update={"trips": result.trips * 2}))


def test_planner_rejects_wrong_receiver_unknown_sku_and_home():
    request, result = planning_fixture()
    data = request.model_dump()
    data["allocations"][0]["sku_id"] = "unknown"
    with pytest.raises(ValidationError, match="unknown SKU"):
        PlannerInput(**data)
    trip = result.trips[0]
    wrong = trip.stops[0].model_copy(update={"receiver_id": "another"})
    with pytest.raises(ValueError, match="wrong receiver"):
        validate_coverage(request, result.model_copy(update={"trips": (trip.model_copy(update={"stops": (wrong,)}),)}))
