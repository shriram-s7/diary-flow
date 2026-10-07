"""Dispatch planner: builds each truck's ordered stops from the plant.

Objective, in strict order (CLAUDE.md):
  (a) cover all demand within plant stock (and fleet capacity),
  (b) use the fewest trucks,
  (c) minimise total km.

Method (deterministic):
  1. Allocate the servable litres (min of demand, stock, fleet capacity) to hubs
     by priority (Critical > High > Normal), then earliest window, then id.
  2. Try k = the fewest largest trucks whose capacity covers that amount. Build
     routes by cheapest insertion with capacity and windows as hard constraints.
     A hub is split across trucks only when no single truck can take all of it.
     If litres are left over, try k + 1, up to the whole fleet.
  3. If even the whole fleet cannot meet every window, place the remaining
     litres anyway (capacity still hard) and report every late stop.
  4. Reorder each truck's stops for the shortest closed tour plant -> stops -> plant (every order for
     up to 6 stops, else 2-opt; ties go to the order that delivers earlier; never adds lateness), then
     compute ETA / slack / risk with timing.py on that final order (including service time).

Never raises on infeasible input; problems are returned as warnings.
"""
import itertools
from typing import Dict, List, Optional, Sequence, Tuple

import config
import routing
from models import (DispatchPlan, Hub, HubShortfall, HubStatus, Plant, Priority, ReserveTruck, Risk, Stop,
                    Truck, TruckPlan, TruckStatus, WorldState)
from routing import road_km
from timing import format_clock, risk_from_slack, route_etas, slack_min, travel_time_min

EPS = 1e-6
EXACT_REORDER_MAX_STOPS = 6   # up to 6! = 720 orders per truck: try them all
PRIORITY_RANK = {Priority.CRITICAL: 0, Priority.HIGH: 1, Priority.NORMAL: 2}

# A route is an ordered list of (hub_id, drop_l).
Route = List[Tuple[str, float]]


def buffered_load_l(planned_l: float, capacity_l: float) -> float:
    """THE load rule: planned drops + LOAD_BUFFER_PCT as spare for spikes, capped at capacity."""
    return min(capacity_l, planned_l * (1 + config.LOAD_BUFFER_PCT / 100))


def _hub_key(hub: Hub):
    return PRIORITY_RANK[hub.priority], hub.window_end, hub.id


def _truck_key(truck: Truck):
    return -truck.capacity_l, truck.id


def _litres(x: float) -> str:
    return f"{x:,.0f} L"


class _Planner:
    def __init__(self, hubs: Dict[str, Hub], plant: Plant, start_time: float):
        self.hubs = hubs
        self.plant = plant
        self.start_time = start_time

    # --- route metrics -----------------------------------------------------

    def km(self, route: Route) -> float:
        """Road distance (km) for the closed tour plant -> stops -> plant."""
        lat, lon = self.plant.road_lat, self.plant.road_lon
        total = 0.0
        for hub_id, _ in route:
            hub = self.hubs[hub_id]
            total += road_km(lat, lon, hub.road_lat, hub.road_lon)
            lat, lon = hub.road_lat, hub.road_lon
        return total + road_km(lat, lon, self.plant.road_lat, self.plant.road_lon)

    def etas(self, truck: Truck, route: Route) -> Tuple[List[float], float]:
        stops = [(self.hubs[h].road_lat, self.hubs[h].road_lon, drop) for h, drop in route]
        etas, free_at = route_etas(self.start_time, self.plant.road_lat, self.plant.road_lon, stops, truck.speed_kmph)
        if not stops:
            return etas, free_at
        back_km = road_km(stops[-1][0], stops[-1][1], self.plant.road_lat, self.plant.road_lon)
        return etas, free_at + travel_time_min(back_km, truck.speed_kmph)

    def lateness(self, truck: Truck, route: Route) -> float:
        etas, _ = self.etas(truck, route)
        return sum(max(0.0, eta - self.hubs[h].window_end) for (h, _), eta in zip(route, etas))

    # --- construction -------------------------------------------------------

    def candidates(self, hub: Hub, rem: float, trucks: Sequence[Truck], routes: Dict[str, Route],
                   loads: Dict[str, float], enforce_windows: bool, full_only: bool):
        """Feasible insertions of `hub` as (score, truck_id, new_route, drop_l)."""
        out = []
        for ti, truck in enumerate(trucks):
            spare = truck.capacity_l - loads[truck.id]
            if spare <= EPS or (full_only and spare < rem - EPS):
                continue
            drop = min(rem, spare)
            route = routes[truck.id]
            existing = next((i for i, (h, _) in enumerate(route) if h == hub.id), None)
            if existing is not None:
                new = list(route)
                new[existing] = (hub.id, route[existing][1] + drop)
                options = [(existing, new, 0.0)]
            else:
                base = self.km(route)
                options = []
                for pos in range(len(route) + 1):
                    new = route[:pos] + [(hub.id, drop)] + route[pos:]
                    options.append((pos, new, self.km(new) - base))
            for pos, new, added_km in options:
                if enforce_windows and self.lateness(truck, new) > EPS:
                    continue
                if not enforce_windows:
                    # Capacity is still hard; minimise added lateness, then km.
                    added_late = self.lateness(truck, new) - self.lateness(truck, route)
                    score = (2, added_late, added_km, ti, pos)
                elif drop >= rem - EPS:
                    score = (0, added_km, ti, pos)       # whole remaining demand in one stop
                else:
                    score = (1, -drop, added_km, ti, pos)  # split: biggest piece first
                out.append((score, truck.id, new, drop))
        return out

    def build(self, trucks: Sequence[Truck], targets: Dict[str, float], enforce_windows: bool,
              routes: Optional[Dict[str, Route]] = None) -> Tuple[Dict[str, Route], Dict[str, float]]:
        """Cheapest insertion. Returns routes and litres that could not be placed."""
        routes = {t.id: list(routes.get(t.id, [])) if routes else [] for t in trucks}
        loads = {t.id: sum(d for _, d in routes[t.id]) for t in trucks}
        remaining = {h: l for h, l in targets.items() if l > EPS}
        blocked = set()
        while True:
            best = None
            for hub in sorted((self.hubs[h] for h in remaining if h not in blocked), key=_hub_key):
                rem = remaining[hub.id]
                cands = self.candidates(hub, rem, trucks, routes, loads, enforce_windows, full_only=True)
                if not cands:
                    cands = self.candidates(hub, rem, trucks, routes, loads, enforce_windows, full_only=False)
                if not cands:
                    blocked.add(hub.id)  # routes only grow, so it stays infeasible
                    continue
                score, truck_id, new, drop = min(cands, key=lambda c: c[0])
                key = (PRIORITY_RANK[hub.priority],) + score + (hub.id,)
                if best is None or key < best[0]:
                    best = (key, hub.id, truck_id, new, drop)
            if best is None:
                break
            _, hub_id, truck_id, new, drop = best
            routes[truck_id] = new
            loads[truck_id] += drop
            remaining[hub_id] -= drop
            if remaining[hub_id] <= EPS:
                del remaining[hub_id]
        return routes, remaining

    def order_cost(self, truck: Truck, route: Route) -> Tuple[float, float]:
        """(km of the closed tour plant -> stops -> plant, sum of arrival times).

        The second term breaks ties between equally long orders, e.g. a tour and its reverse, in favour
        of serving the nearer stops first so milk arrives earlier.
        """
        return round(self.km(route), 6), sum(self.etas(truck, route)[0])

    def improve(self, truck: Truck, route: Route) -> Route:
        """Best stop order for the closed tour without making any window worse.

        Exact (every order) for up to EXACT_REORDER_MAX_STOPS stops, else 2-opt over the closed tour.
        """
        if len(route) <= EXACT_REORDER_MAX_STOPS:
            limit = self.lateness(truck, route) + EPS
            candidates = (list(p) for p in itertools.permutations(route))
            feasible = [p for p in candidates if self.lateness(truck, p) <= limit]
            return min(feasible, key=lambda p: self.order_cost(truck, p))
        return self.two_opt(truck, route)

    def two_opt(self, truck: Truck, route: Route) -> Route:
        """Reverse segments of the closed tour while that lowers (km, sum of ETAs) without adding lateness.

        Segments may include the first and last stop, so both plant legs are part of every move.
        """
        best_cost, best_late = self.order_cost(truck, route), self.lateness(truck, route)
        improved = True
        while improved:
            improved = False
            for i in range(len(route)):
                for j in range(i + 1, len(route)):
                    new = route[:i] + route[i:j + 1][::-1] + route[j + 1:]
                    new_cost = self.order_cost(truck, new)
                    if new_cost < best_cost:
                        new_late = self.lateness(truck, new)
                        if new_late <= best_late + EPS:
                            route, best_cost, best_late, improved = new, new_cost, new_late, True
        return route


def plan_dispatch(hubs: Sequence[Hub], trucks: Sequence[Truck], plant: Plant, now: float) -> DispatchPlan:
    start_time = max(now, config.DISPATCH_START)
    decisions: List[str] = []
    warnings: List[str] = []

    pending = sorted((h for h in hubs if h.status != HubStatus.COMPLETED and h.remaining_l > EPS), key=_hub_key)
    hub_map = {h.id: h for h in pending}
    total_demand = sum(h.remaining_l for h in pending)

    fleet = sorted(trucks, key=_truck_key)
    usable = [t for t in fleet if t.capacity_l > 0 and t.speed_kmph > 0]
    usable_ids = {t.id for t in usable}
    for t in fleet:
        if t.id not in usable_ids:
            warnings.append(f"{t.id} cannot be used (capacity {t.capacity_l:.0f} L, speed {t.speed_kmph:.0f} km/h)")
    fleet_capacity = sum(t.capacity_l for t in usable)
    stock = max(0.0, plant.milk_available_l)

    decisions.append(f"Demand {_litres(total_demand)} across {len(pending)} hubs; plant stock {_litres(stock)}; "
                     f"fleet capacity {_litres(fleet_capacity)} in {len(usable)} trucks; "
                     f"dispatch from {format_clock(start_time)}.")

    # 1. Allocate servable litres by priority, then earliest window.
    servable = min(total_demand, stock, fleet_capacity)
    shortage_reason = None
    if total_demand - servable > EPS:
        shortage_reason = "plant stock" if stock <= fleet_capacity else "fleet capacity"
        warnings.append(f"Demand exceeds {shortage_reason}: only {_litres(servable)} of {_litres(total_demand)} "
                        f"can be delivered; {_litres(total_demand - servable)} short. Served by priority, "
                        f"then earliest window.")
    targets: Dict[str, float] = {}
    budget = servable
    for hub in pending:
        give = min(hub.remaining_l, budget)
        if give > EPS:
            targets[hub.id] = give
            budget -= give

    planner = _Planner(hub_map, plant, start_time)

    # 2. Fewest trucks: start with the smallest k of the largest trucks that covers the litres.
    k_min, cum = 0, 0.0
    while cum < servable - EPS and k_min < len(usable):
        cum += usable[k_min].capacity_l
        k_min += 1
    if k_min:
        decisions.append(f"Capacity needs at least {k_min} truck(s): "
                         f"{', '.join(t.id for t in usable[:k_min])} = {_litres(cum)}.")

    routes: Dict[str, Route] = {}
    leftover: Dict[str, float] = {}
    chosen_k = None
    for k in range(k_min, len(usable) + 1):
        routes, leftover = planner.build(usable[:k], targets, enforce_windows=True)
        if not leftover:
            chosen_k = k
            if k > k_min:
                decisions.append(f"With {k} trucks every hub is served within its window.")
            break
        late_l = sum(leftover.values())
        decisions.append(f"With {k} truck(s), {_litres(late_l)} cannot be delivered before the windows close"
                         + ("; adding a truck." if k < len(usable) else "."))

    windows_met = chosen_k is not None
    if not windows_met:
        # 3. Whole fleet still misses windows: place the rest anyway and report it.
        routes, leftover = planner.build(usable, leftover, enforce_windows=False, routes=routes)
        warnings.append("Not every window can be met even with the whole fleet; late stops are listed below.")

    # 4. Improve and time each route.
    truck_plans: List[TruckPlan] = []
    stock_left = stock - sum(d for r in routes.values() for _, d in r)
    split: Dict[str, List[Tuple[str, float]]] = {}
    for truck in usable:
        route = routes.get(truck.id, [])
        if not route:
            continue
        route = planner.improve(truck, route)
        etas, return_eta = planner.etas(truck, route)
        stops = []
        for (hub_id, drop), eta in zip(route, etas):
            hub = hub_map[hub_id]
            slack = slack_min(hub.window_end, eta)
            stops.append(Stop(hub_id=hub_id, drop_l=drop, eta=eta, window_end=hub.window_end,
                              slack_min=slack, risk=risk_from_slack(slack)))
            split.setdefault(hub_id, []).append((truck.id, drop))
            if slack < 0:
                warnings.append(f"{truck.id} reaches {hub.name} at {format_clock(eta)}, "
                                f"{-slack:.0f} min after its {format_clock(hub.window_end)} window.")
        planned = sum(s.drop_l for s in stops)
        wanted = buffered_load_l(planned, truck.capacity_l) - planned
        buffer = min(wanted, max(0.0, stock_left))
        if buffer + EPS < wanted:
            warnings.append(f"{truck.id} spare load reduced to {_litres(buffer)}: plant stock is exhausted.")
        stock_left -= buffer
        km = planner.km(route)
        truck_plans.append(TruckPlan(
            truck_id=truck.id,
            capacity_l=truck.capacity_l,
            stops=stops,
            planned_l=planned,
            load_l=planned + buffer,
            utilisation_pct=planned / truck.capacity_l * 100,
            total_km=km,
            fuel_l=km / truck.fuel_km_per_l,
            cost_inr=km * truck.cost_per_km,
            return_eta=return_eta,
        ))
        names = " -> ".join(hub_map[s.hub_id].name for s in stops)
        decisions.append(f"{truck.id}: Plant -> {names} -> Plant, {km:.1f} km, {_litres(planned)} "
                         f"({planned / truck.capacity_l * 100:.0f}% full), back {format_clock(return_eta)}.")

    for hub_id, parts in split.items():
        if len(parts) > 1:
            decisions.append(f"{hub_map[hub_id].name} split: " +
                             ", ".join(f"{_litres(d)} on {t}" for t, d in parts) + ".")

    # Reserve trucks.
    used_ids = {p.truck_id for p in truck_plans}
    n_used = len(used_ids)
    reserve: List[ReserveTruck] = []
    for truck in fleet:
        if truck.id in used_ids:
            continue
        if truck.id not in usable_ids:
            reason = "unusable: zero capacity or speed"
        elif total_demand <= EPS:
            reason = "not needed: no demand to deliver"
        elif shortage_reason == "plant stock" and servable > EPS:
            reason = f"not needed: available stock covered by {n_used} trucks"
        else:
            reason = f"not needed: demand covered by {n_used} trucks"
        reserve.append(ReserveTruck(truck_id=truck.id, capacity_l=truck.capacity_l, reason=reason))
        decisions.append(f"{truck.id} ({_litres(truck.capacity_l)}) kept at the plant as RESERVE: {reason}.")

    # Unserved litres per hub, most important first.
    unserved: List[HubShortfall] = []
    for hub in pending:
        planned = sum(d for _, d in split.get(hub.id, []))
        missing = hub.remaining_l - planned
        if missing > EPS:
            if hub.remaining_l - targets.get(hub.id, 0.0) > EPS:
                reason = f"insufficient {shortage_reason}"
            else:
                reason = "could not be placed on any truck"
            unserved.append(HubShortfall(hub_id=hub.id, name=hub.name, priority=hub.priority,
                                         window_end=hub.window_end, demand_l=hub.remaining_l,
                                         planned_l=planned, unserved_l=missing, reason=reason))
    for s in unserved:
        decisions.append(f"{s.name} ({s.priority.value}) short by {_litres(s.unserved_l)}: {s.reason}.")

    total_planned = sum(p.planned_l for p in truck_plans)
    return DispatchPlan(
        created_at=now,
        start_time=start_time,
        total_demand_l=total_demand,
        total_planned_l=total_planned,
        milk_available_l=stock,
        fleet_capacity_l=fleet_capacity,
        trucks_used=n_used,
        total_km=sum(p.total_km for p in truck_plans),
        total_fuel_l=sum(p.fuel_l for p in truck_plans),
        total_cost_inr=sum(p.cost_inr for p in truck_plans),
        windows_met=windows_met and all(s.risk != Risk.LATE for p in truck_plans for s in p.stops),
        truck_plans=truck_plans,
        reserve_trucks=reserve,
        unserved=unserved,
        warnings=warnings,
        decisions=decisions,
    )


def plan_world(world: WorldState, log: bool = True) -> DispatchPlan:
    """Plan the world's hubs and fleet, store the plan and give each truck its stops.

    Caller holds state.lock. Only meaningful before execution starts.
    """
    plan = plan_dispatch(world.hubs, world.trucks, world.plant, world.sim_time)
    world.plan = plan
    by_truck = {p.truck_id: p for p in plan.truck_plans}
    for truck in world.trucks:
        truck_plan = by_truck.get(truck.id)
        truck.plan = [s.model_copy() for s in truck_plan.stops] if truck_plan else []
        truck.status = TruckStatus.AT_PLANT if truck_plan else TruckStatus.RESERVE
    if log:
        world.log("SYSTEM", "PLAN_CREATED",
                  f"{plan.trucks_used} trucks, {plan.total_planned_l:,.0f} of {plan.total_demand_l:,.0f} L, "
                  f"{plan.total_km:.1f} km, {len(plan.reserve_trucks)} in reserve",
                  {"decisions": plan.decisions, "warnings": plan.warnings})
    return plan
