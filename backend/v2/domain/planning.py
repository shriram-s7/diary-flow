"""Planner DTOs and protocol. Algorithm and inventory authorization arrive in P18/P19."""
from collections import Counter
from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import field_validator, model_validator

from .types import (Amount, EntityId, Lane, Packaging, PositiveInt, Shift,
                    Units, Value, require_aware)


class AllocationLine(Value):
    allocation_id: EntityId
    receiver_id: EntityId
    sku_id: EntityId
    units: PositiveInt
    completion_deadline: datetime

    _aware = field_validator("completion_deadline")(require_aware)


class StockLot(Value):
    lot_id: EntityId
    sku_id: EntityId
    available_units: Units
    expires_at: datetime

    _aware = field_validator("expires_at")(require_aware)


class Vehicle(Value):
    vehicle_id: EntityId
    driver_id: EntityId
    home_id: EntityId
    capacity_slots: PositiveInt
    capacity_g: PositiveInt
    # Each member is a set explicitly qualified for simultaneous carriage.
    lane_configurations: tuple[frozenset[Lane], ...]
    shift: Shift

    @model_validator(mode="after")
    def lane_support(self):
        if not self.lane_configurations or any(not lanes for lanes in self.lane_configurations):
            raise ValueError("at least one nonempty lane configuration is required")
        return self


class RoadLeg(Value):
    origin_id: EntityId
    destination_id: EntityId
    km: Amount
    minutes: Amount
    approximate: bool = False


class PlannerInput(Value):
    run_id: EntityId
    revision: Units
    delivery_date: date
    level: Literal["plant", "hub"]
    source_id: EntityId
    dispatch_at: datetime
    allocations: tuple[AllocationLine, ...]
    products: tuple[Packaging, ...]
    vehicles: tuple[Vehicle, ...]
    stock: tuple[StockLot, ...]
    road_legs: tuple[RoadLeg, ...]

    _aware = field_validator("dispatch_at")(require_aware)

    @model_validator(mode="after")
    def consistent_ids(self):
        for rows, key in ((self.allocations, "allocation_id"), (self.products, "sku_id"),
                          (self.vehicles, "vehicle_id"), (self.stock, "lot_id")):
            ids = [getattr(row, key) for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {key}")
        products = {p.sku_id for p in self.products}
        if any(a.sku_id not in products for a in (*self.allocations, *self.stock)):
            raise ValueError("unknown SKU in allocation or stock")
        if any(v.home_id != self.source_id for v in self.vehicles):
            raise ValueError("vehicle belongs to another source")
        leg_keys = [(leg.origin_id, leg.destination_id) for leg in self.road_legs]
        if len(leg_keys) != len(set(leg_keys)):
            raise ValueError("duplicate road leg")
        return self


class Assignment(Value):
    allocation_id: EntityId
    units: PositiveInt


class PlannedStop(Value):
    stop_id: EntityId
    receiver_id: EntityId
    arrival_at: datetime
    completed_at: datetime
    assignments: tuple[Assignment, ...]

    _aware = field_validator("arrival_at", "completed_at")(require_aware)

    @model_validator(mode="after")
    def ordered(self):
        if self.completed_at < self.arrival_at or not self.assignments:
            raise ValueError("stop needs assignments and completion >= arrival")
        return self


class PlannedTrip(Value):
    vehicle_id: EntityId
    driver_id: EntityId
    departure_at: datetime
    return_at: datetime
    stops: tuple[PlannedStop, ...]
    total_km: Amount

    _aware = field_validator("departure_at", "return_at")(require_aware)

    @model_validator(mode="after")
    def ordered(self):
        if not self.stops:
            raise ValueError("planned trip requires stops")
        cursor = self.departure_at
        for stop in self.stops:
            if stop.arrival_at < cursor:
                raise ValueError("stops overlap or precede departure")
            cursor = stop.completed_at
        if self.return_at < cursor:
            raise ValueError("return precedes last completion")
        return self


class Unplanned(Value):
    allocation_id: EntityId
    units: PositiveInt
    reason: Literal["capacity", "lane", "stock", "expiry", "shift", "route", "driver", "window"]


class PlannerOutput(Value):
    input_revision: Units
    trips: tuple[PlannedTrip, ...]
    unplanned: tuple[Unplanned, ...]


class Planner(Protocol):
    def plan(self, request: PlannerInput) -> PlannerOutput: ...


def validate_coverage(request: PlannerInput, result: PlannerOutput) -> None:
    """Conservation/reference gate, not the P18 full route/stock feasibility validator."""
    if request.revision != result.input_revision:
        raise ValueError("stale planner result")
    expected = {a.allocation_id: a for a in request.allocations}
    actual = Counter()
    seen_stops = set()
    vehicles = {v.vehicle_id: v for v in request.vehicles}
    for trip in result.trips:
        vehicle = vehicles.get(trip.vehicle_id)
        if vehicle is None or vehicle.driver_id != trip.driver_id:
            raise ValueError("unknown vehicle or incorrect driver")
        for stop in trip.stops:
            if stop.stop_id in seen_stops:
                raise ValueError("duplicate stop id")
            seen_stops.add(stop.stop_id)
            for line in stop.assignments:
                if line.allocation_id not in expected:
                    raise ValueError("unknown allocation")
                if expected[line.allocation_id].receiver_id != stop.receiver_id:
                    raise ValueError("allocation assigned to wrong receiver")
                actual[line.allocation_id] += line.units
    for line in result.unplanned:
        if line.allocation_id not in expected:
            raise ValueError("unknown unplanned allocation")
        actual[line.allocation_id] += line.units
    if dict(actual) != {key: line.units for key, line in expected.items()}:
        raise ValueError("planned + unplanned must equal each allocation exactly")
