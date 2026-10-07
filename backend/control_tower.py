"""Control tower: reacts to live demand and priority changes (CLAUDE.md "Live demand change").

Increase  -> shortage = new remaining - milk already committed to the hub. Options:
             A) a truck already out with spare milk adds the hub after its current stop
                (or tops up its existing stop there),
             B) a truck idle at the plant makes a new trip plant -> hub -> plant,
             C) one A plus one B, when neither alone covers the shortage.
             Score: most litres covered -> no window broken -> least added km.
             Applied at once in "auto" mode; stored for approval in "approve" mode.
Decrease  -> planned drops to the hub are cut, latest-arriving first; the freed milk
             stays on the truck (spare_l) and returns to the plant.
Critical  -> the owning truck serves the hub right after its current stop, unless that
             would make another stop late; then it explains why not.

Before execution a change simply replans. Every change logs exactly one Decision whose
details hold the options table. All functions expect the caller to hold state.lock.
"""
import itertools
from typing import Any, Dict, List, Optional, Tuple

import config
import routing
import simulation
import state
from models import (ControlMode, Hub, HubStatus, Option, OptionPart, Priority, Recommendation,
                    RecommendationStatus, Risk, SimPhase, Spike, Stop, StopEta, StopStatus, Truck, TruckStatus,
                    WorldState)
from planner import plan_world
from routing import cached_route, haversine
from timing import format_clock, polyline_time_min, service_time_min, update_all_etas, update_truck_etas

EPS = 1e-6
OUT = (TruckStatus.LOADING, TruckStatus.EN_ROUTE, TruckStatus.UNLOADING, TruckStatus.RETURNING)
IDLE_AT_PLANT = (TruckStatus.RESERVE, TruckStatus.AT_PLANT)


class ControlError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _litres(x: float) -> str:
    return f"{x:,.0f} L"


def pending(truck: Truck) -> List[Stop]:
    return simulation.pending_stops(truck)


def committed_to(world: WorldState, hub_id: str) -> float:
    """Milk on its way to a hub: pending drops on every truck."""
    return sum(s.undelivered_l for t in world.trucks for s in pending(t) if s.hub_id == hub_id)


def _leg_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    route = cached_route(lat1, lon1, lat2, lon2)
    return route["distance_km"] if route else haversine(lat1, lon1, lat2, lon2)


def _remaining_route_km(truck: Truck) -> float:
    km, lat, lon = 0.0, truck.position.lat, truck.position.lon
    for p in truck.route[truck.route_index:]:
        km += haversine(lat, lon, p.lat, p.lon)
        lat, lon = p.lat, p.lon
    return km


def _point(world: WorldState, hub_id: Optional[str]) -> Tuple[float, float]:
    if hub_id is None:
        return world.plant.road_lat, world.plant.road_lon
    hub = world.hub(hub_id)
    return hub.road_lat, hub.road_lon


def _trial_etas(world: WorldState, truck: Truck, new_plan: List[Stop],
                status: Optional[TruckStatus] = None) -> List[StopEta]:
    """ETAs of every remaining stop if `truck` followed `new_plan` (timing.py, not a copy of it)."""
    trial = truck.model_copy()   # shallow: shares the route polyline (never mutated), so ETA caching applies
    trial.plan = [s.model_copy() for s in new_plan]
    if status is not None:
        trial.status = status
        if status == TruckStatus.EN_ROUTE:
            trial.route, trial.route_index = [], 0  # new leg from where it is now
    update_truck_etas(world, trial)
    return [StopEta(hub_id=s.hub_id, drop_l=s.drop_l, eta=s.eta, window_end=s.window_end,
                    slack_min=s.slack_min, risk=s.risk) for s in pending(trial)]


def _broken(world: WorldState, truck: Truck, etas: List[StopEta]) -> List[str]:
    """Stops that would be late after the change but are not late now."""
    before = {s.hub_id: s.slack_min for s in pending(truck)}
    out = []
    for e in etas:
        if e.slack_min < 0 and (before.get(e.hub_id) is None or before[e.hub_id] >= 0):
            out.append(f"{world.hub(e.hub_id).name} ({truck.id}) +{-e.slack_min:.0f} min")
    return out


def _etas_table(world: WorldState, etas: List[StopEta]) -> List[dict]:
    return [{"hub": world.hub(e.hub_id).name, "drop_l": e.drop_l, "eta": format_clock(e.eta),
             "window_end": format_clock(e.window_end), "slack_min": round(e.slack_min, 1), "risk": e.risk.value}
            for e in etas]


def _new_stop(hub: Hub, drop_l: float) -> Stop:
    return Stop(hub_id=hub.id, drop_l=drop_l, window_end=hub.window_end)


# --- Options ------------------------------------------------------------------

def _option_a(world: WorldState, truck: Truck, hub: Hub, shortage: float) -> Optional[OptionPart]:
    """A truck already out uses its spare milk: top up its stop at the hub, or add the hub after its current stop."""
    if truck.status not in OUT or truck.spare_l <= EPS:
        return None
    drop = min(shortage, truck.spare_l)
    stops = pending(truck)
    existing = next((s for s in stops if s.hub_id == hub.id), None)
    status = None
    if existing is not None:
        new_plan = [s.model_copy() for s in truck.plan]
        next(s for s in new_plan if s.status == StopStatus.PENDING and s.hub_id == hub.id).drop_l += drop
        action, added_km = "top_up", 0.0
    elif truck.status == TruckStatus.RETURNING:
        new_plan = truck.plan + [_new_stop(hub, drop)]
        action, status = "insert", TruckStatus.EN_ROUTE
        added_km = (haversine(truck.position.lat, truck.position.lon, hub.road_lat, hub.road_lon)
                    + _leg_km(hub.road_lat, hub.road_lon, world.plant.road_lat, world.plant.road_lon)
                    - _remaining_route_km(truck))
    else:
        idx = truck.plan.index(stops[0]) + 1
        new_plan = truck.plan[:idx] + [_new_stop(hub, drop)] + truck.plan[idx:]
        prev = _point(world, stops[0].hub_id)
        nxt = _point(world, stops[1].hub_id if len(stops) > 1 else None)
        action = "insert"
        added_km = _leg_km(*prev, hub.road_lat, hub.road_lon) + _leg_km(hub.road_lat, hub.road_lon, *nxt) - _leg_km(*prev, *nxt)
    return OptionPart(truck_id=truck.id, action=action, drop_l=drop, added_km=max(0.0, added_km),
                      etas=_trial_etas(world, truck, new_plan, status))


def _option_b(world: WorldState, truck: Truck, hub: Hub, litres: float) -> Optional[OptionPart]:
    """A truck idle at the plant loads and makes a new trip plant -> hub -> plant."""
    if truck.status not in IDLE_AT_PLANT or pending(truck):
        return None
    drop = min(litres, truck.capacity_l, world.plant.milk_available_l)
    if drop <= EPS:
        return None
    new_plan = truck.plan + [_new_stop(hub, drop)]
    km = (_leg_km(*world.plant.road, hub.road_lat, hub.road_lon)
          + _leg_km(hub.road_lat, hub.road_lon, *world.plant.road))
    return OptionPart(truck_id=truck.id, action="new_trip", drop_l=drop, added_km=km,
                      etas=_trial_etas(world, truck, new_plan, TruckStatus.AT_PLANT))


def _describe(world: WorldState, part: OptionPart, hub: Hub) -> str:
    truck = world.truck(part.truck_id)
    eta = next(e.eta for e in part.etas if e.hub_id == hub.id)
    if part.action == "top_up":
        return f"{truck.id} adds {_litres(part.drop_l)} to its {hub.name} stop (arrives {format_clock(eta)})"
    if part.action == "new_trip":
        return (f"{truck.id} loads and drives plant -> {hub.name} -> plant with {_litres(part.drop_l)} "
                f"(arrives {format_clock(eta)})")
    if truck.status == TruckStatus.RETURNING:
        return f"{truck.id} turns back to {hub.name} with {_litres(part.drop_l)} (arrives {format_clock(eta)})"
    after = world.hub(pending(truck)[0].hub_id).name
    return f"{truck.id} adds {hub.name} ({_litres(part.drop_l)}) after {after} (arrives {format_clock(eta)})"


def _make_option(world: WorldState, kind: str, parts: List[OptionPart], hub: Hub) -> Option:
    broken = []
    for part in parts:
        broken += _broken(world, world.truck(part.truck_id), part.etas)
    return Option(
        id=f"{kind}:" + "+".join(p.truck_id for p in parts),
        kind=kind, parts=parts,
        litres_covered=sum(p.drop_l for p in parts),
        added_km=sum(p.added_km for p in parts),
        windows_broken=broken,
        summary="; ".join(_describe(world, p, hub) for p in parts),
    )


def _score(option: Option):
    return (-round(option.litres_covered, 6), bool(option.windows_broken), round(option.added_km, 6),
            option.kind, option.id)


def _why_not(winner: Option, other: Option) -> str:
    if other.litres_covered < winner.litres_covered - EPS:
        return f"{other.id} covers only {_litres(other.litres_covered)}"
    if other.windows_broken and not winner.windows_broken:
        return f"{other.id} would make {', '.join(other.windows_broken)} late"
    return f"{other.id} drives {other.added_km - winner.added_km:+.1f} km more"


def evaluate_spike(world: WorldState, hub: Hub, old_demand_l: float, exclude=()) -> Recommendation:
    """Build every option for covering a hub's shortage and pick the best. Changes nothing.
    Trucks in `exclude` are not considered (e.g. the truck that just handed the stop over)."""
    update_all_etas(world)
    committed = committed_to(world, hub.id)
    shortage = max(0.0, hub.remaining_l - committed)
    spike = Spike(hub_id=hub.id, hub_name=hub.name, old_demand_l=old_demand_l, new_demand_l=hub.demand_l,
                  remaining_l=hub.remaining_l, committed_l=committed, shortage_l=shortage)

    trucks = sorted((t for t in world.trucks if t.id not in exclude), key=lambda t: t.id)
    a_parts = [p for p in (_option_a(world, t, hub, shortage) for t in trucks) if p]
    b_parts = [p for p in (_option_b(world, t, hub, shortage) for t in trucks) if p]
    options = ([_make_option(world, "A", [p], hub) for p in a_parts]
               + [_make_option(world, "B", [p], hub) for p in b_parts])
    if options and not any(o.litres_covered >= shortage - EPS for o in options):
        for a in a_parts:
            rest = shortage - a.drop_l
            for t in trucks:
                b = _option_b(world, t, hub, rest)
                if b:
                    options.append(_make_option(world, "C", [a, b], hub))
    options.sort(key=_score)

    rec_id = ""  # assigned by the caller when the recommendation is stored
    if not options:
        return Recommendation(id=rec_id, created_at=world.sim_time, spike=spike, options=[], chosen=None,
                              reason=f"No truck out has spare milk and no truck is free at the plant: "
                                     f"{_litres(shortage)} cannot be covered.")
    best = options[0]
    reason = (f"{best.id} chosen: {best.summary}. Covers {_litres(best.litres_covered)} of "
              f"{_litres(shortage)}, " + ("no window broken" if not best.windows_broken
                                          else "but makes " + ", ".join(best.windows_broken) + " late")
              + f", +{best.added_km:.1f} km.")
    others = [_why_not(best, o) for o in options[1:]]
    if others:
        reason += " Not chosen: " + "; ".join(others) + "."
    if best.litres_covered < shortage - EPS:
        reason += f" {_litres(shortage - best.litres_covered)} remains unserved: no more milk can reach {hub.name}."
    return Recommendation(id=rec_id, created_at=world.sim_time, spike=spike, options=options,
                          chosen=best.id, reason=reason)


def _options_table(world: WorldState, rec: Recommendation) -> List[dict]:
    return [{"id": o.id, "kind": o.kind, "litres_covered": o.litres_covered, "added_km": round(o.added_km, 2),
             "windows_broken": o.windows_broken, "chosen": o.id == rec.chosen, "summary": o.summary,
             "etas": {p.truck_id: _etas_table(world, p.etas) for p in o.parts}}
            for o in rec.options]


def apply_option(world: WorldState, option: Option, hub: Hub):
    for part in option.parts:
        truck = world.truck(part.truck_id)
        if part.action == "top_up":
            stop = next(s for s in pending(truck) if s.hub_id == hub.id)
            old_service = service_time_min(stop.drop_l)
            stop.drop_l += part.drop_l
            if truck.status == TruckStatus.UNLOADING and pending(truck)[0] is stop:
                truck.activity_remaining_min += service_time_min(stop.drop_l) - old_service
        elif part.action == "insert":
            if truck.status == TruckStatus.RETURNING:
                truck.plan.append(_new_stop(hub, part.drop_l))
                simulation.start_leg(world, truck, TruckStatus.EN_ROUTE)
            else:
                idx = truck.plan.index(pending(truck)[0]) + 1
                truck.plan.insert(idx, _new_stop(hub, part.drop_l))
        else:  # new_trip: the simulation loads it on the next tick
            truck.plan.append(_new_stop(hub, part.drop_l))
    update_all_etas(world)


# --- Demand ---------------------------------------------------------------------

def _require_live(world: WorldState):
    if world.phase in (SimPhase.STOPPED, SimPhase.COMPLETED):
        raise ControlError(409, "The run has ended; reset the simulation before changing hubs.")


def _replan(world: WorldState, decision_type: str, summary: str, details: dict) -> dict:
    if world.plan is not None:
        plan = plan_world(world, log=False)
        summary += (f" Replanned: {plan.trucks_used} trucks, {_litres(plan.total_planned_l)}, "
                    f"{plan.total_km:.1f} km.")
        details = dict(details, plan_decisions=plan.decisions, plan_warnings=plan.warnings)
    world.log("SYSTEM", decision_type, summary, details)
    return {"decision": world.decisions[-1]}


def change_demand(world: WorldState, hub: Hub, new_demand_l: float) -> dict:
    if new_demand_l < hub.delivered_l - EPS:
        raise ControlError(400, f"{hub.name} has already received {_litres(hub.delivered_l)}; "
                                f"demand cannot be set below that.")
    _require_live(world)
    old = hub.demand_l
    if abs(new_demand_l - old) <= EPS:
        return {"hub": hub, "decision": None, "recommendation": None}
    hub.demand_l = new_demand_l
    hub.status = HubStatus.COMPLETED if hub.remaining_l <= EPS else HubStatus.PENDING
    change = f"{hub.name} demand {_litres(old)} -> {_litres(new_demand_l)}."
    base = {"hub_id": hub.id, "old_demand_l": old, "new_demand_l": new_demand_l}

    if world.phase == SimPhase.PLANNING:
        out = _replan(world, "DEMAND_CHANGED", change, base)
        return dict(out, hub=hub, recommendation=None)
    if new_demand_l > old:
        return _demand_increase(world, hub, old, change, base)
    return _demand_decrease(world, hub, change, base)


def cover_shortage(world: WorldState, hub: Hub, change: str, exclude=()) -> dict:
    """A stop was handed over (roadblock): cover the hub's uncommitted litres like a demand increase, without
    the trucks in `exclude`. Auto-applied or left for approval per control_mode, like any spike."""
    _require_live(world)
    base = {"hub_id": hub.id, "old_demand_l": hub.demand_l, "new_demand_l": hub.demand_l, "handed_over": True}
    return _demand_increase(world, hub, hub.demand_l, change, base, exclude, decision_type="STOP_REASSIGNED")


def _demand_increase(world: WorldState, hub: Hub, old: float, change: str, base: dict, exclude=(),
                     decision_type: str = "DEMAND_INCREASE") -> dict:
    rec = evaluate_spike(world, hub, old, exclude)
    details = dict(base, spike=rec.spike.model_dump(mode="json"), options=_options_table(world, rec),
                   recommendation_id=None, reason=rec.reason)
    if rec.spike.shortage_l <= EPS:
        world.log("SYSTEM", decision_type,
                  f"{change} Already covered: {_litres(rec.spike.committed_l)} is on its way.", details)
        return {"hub": hub, "decision": world.decisions[-1], "recommendation": None}

    world.recommendation_seq += 1
    rec.id = f"REC-{world.recommendation_seq}"
    rec.exclude = sorted(exclude)
    details["recommendation_id"] = rec.id
    for old_rec in world.recommendations:
        if old_rec.status == RecommendationStatus.PENDING and old_rec.spike.hub_id == hub.id:
            old_rec.status = RecommendationStatus.SUPERSEDED
            old_rec.resolved_at = world.sim_time
    world.recommendations.append(rec)

    shortage = f"Shortage {_litres(rec.spike.shortage_l)}."
    if rec.chosen is None:
        rec.status = RecommendationStatus.REJECTED
        rec.resolved_at = world.sim_time
        world.log("SYSTEM", decision_type, f"{change} {shortage} {rec.reason}", details)
    elif state.settings.control_mode == ControlMode.AUTO:
        apply_option(world, _chosen(rec), hub)
        rec.status = RecommendationStatus.APPLIED
        rec.resolved_at = world.sim_time
        world.log("SYSTEM", decision_type, f"{change} {shortage} Applied: {rec.reason}", details)
    else:
        world.log("SYSTEM", "RECOMMENDATION_PENDING",
                  f"{change} {shortage} Awaiting approval of {rec.id}: {rec.reason}", details)
    return {"hub": hub, "decision": world.decisions[-1], "recommendation": rec}


def _chosen(rec: Recommendation) -> Option:
    return next(o for o in rec.options if o.id == rec.chosen)


def _cancel_trip_if_empty(world: WorldState, truck: Truck) -> Optional[str]:
    """A truck still loading with nothing left to deliver unloads and stays at the plant."""
    if truck.status == TruckStatus.LOADING and not pending(truck):
        world.plant.milk_available_l += truck.load_l
        returned = truck.load_l
        truck.load_l = 0.0
        truck.status = TruckStatus.AT_PLANT
        truck.activity_remaining_min = 0.0
        return f"{truck.id} trip cancelled; {_litres(returned)} back in plant stock"
    return None


def _demand_decrease(world: WorldState, hub: Hub, change: str, base: dict) -> dict:
    update_all_etas(world)
    committed = committed_to(world, hub.id)
    excess = committed - hub.remaining_l
    rows = []
    if excess > EPS:
        stops = sorted(((t, s) for t in world.trucks for s in pending(t) if s.hub_id == hub.id),
                       key=lambda ts: (-(ts[1].eta or 0), ts[0].id))
        for truck, stop in stops:
            if excess <= EPS:
                break
            cut = min(excess, stop.undelivered_l)
            excess -= cut
            row = {"truck_id": truck.id, "old_drop_l": stop.drop_l, "new_drop_l": stop.drop_l - cut,
                   "freed_l": cut, "eta": format_clock(stop.eta)}
            old_service = service_time_min(stop.drop_l)
            stop.drop_l -= cut
            unloading_here = truck.status == TruckStatus.UNLOADING and pending(truck)[0] is stop
            if unloading_here:
                truck.activity_remaining_min = max(0.0, truck.activity_remaining_min
                                                   - (old_service - service_time_min(stop.drop_l)))
            elif stop.drop_l <= EPS:
                was_current = pending(truck)[0] is stop
                truck.plan.remove(stop)
                row["stop_removed"] = True
                if was_current and truck.status == TruckStatus.EN_ROUTE:
                    simulation.start_leg(world, truck, TruckStatus.EN_ROUTE if pending(truck)
                                         else TruckStatus.RETURNING)
                note = _cancel_trip_if_empty(world, truck)
                if note:
                    row["note"] = note
            rows.append(row)
        update_all_etas(world)
    freed = sum(r["freed_l"] for r in rows)
    details = dict(base, committed_before_l=committed, remaining_l=hub.remaining_l, options=rows, freed_l=freed)
    if rows:
        parts = [f"{r['truck_id']} drop {_litres(r['old_drop_l'])} -> {_litres(r['new_drop_l'])}" for r in rows]
        summary = (f"{change} {'; '.join(parts)}. {_litres(freed)} freed: it stays on the truck "
                   f"and returns to the plant unless reassigned.")
        notes = [r["note"] for r in rows if "note" in r]
        if notes:
            summary += " " + "; ".join(notes) + "."
    else:
        summary = f"{change} No planned drop exceeds the new demand; nothing to change."
    world.log("SYSTEM", "DEMAND_DECREASE", summary, details)
    return {"hub": hub, "decision": world.decisions[-1], "recommendation": None}


# --- Approval -------------------------------------------------------------------

def _find(world: WorldState, rec_id: str) -> Recommendation:
    rec = next((r for r in world.recommendations if r.id == rec_id), None)
    if rec is None:
        raise ControlError(404, f"Recommendation {rec_id} not found")
    if rec.status != RecommendationStatus.PENDING:
        raise ControlError(409, f"Recommendation {rec_id} is already {rec.status.value}")
    return rec


def approve(world: WorldState, rec_id: str) -> Recommendation:
    """Apply a pending recommendation. Trucks have moved since it was made, so it is re-evaluated:
    the approved option is applied with current numbers if still possible, else the best current one."""
    rec = _find(world, rec_id)
    _require_live(world)
    hub = world.hub(rec.spike.hub_id)
    fresh = evaluate_spike(world, hub, rec.spike.old_demand_l, set(rec.exclude))
    details = {"recommendation_id": rec.id, "approved_option": rec.chosen,
               "options": _options_table(world, fresh), "spike": fresh.spike.model_dump(mode="json")}
    rec.resolved_at = world.sim_time
    if fresh.spike.shortage_l <= EPS:
        rec.status = RecommendationStatus.SUPERSEDED
        world.log("SYSTEM", "RECOMMENDATION_APPROVED",
                  f"{rec.id} approved, but {hub.name} is already fully covered; nothing to do.", details)
        return rec
    option = next((o for o in fresh.options if o.id == rec.chosen), None)
    note = ""
    if option is None:
        if fresh.chosen is None:
            rec.status = RecommendationStatus.REJECTED
            world.log("SYSTEM", "RECOMMENDATION_APPROVED",
                      f"{rec.id} approved, but no option is possible any more: {fresh.reason}", details)
            return rec
        option = _chosen(fresh)
        note = f" {rec.chosen} is no longer possible; applied the best current option instead."
    apply_option(world, option, hub)
    rec.options, rec.chosen, rec.spike = fresh.options, option.id, fresh.spike
    rec.status = RecommendationStatus.APPLIED
    world.log("SYSTEM", "RECOMMENDATION_APPROVED",
              f"{rec.id} approved: {option.summary}.{note}", dict(details, applied_option=option.id))
    return rec


def reject(world: WorldState, rec_id: str) -> Recommendation:
    rec = _find(world, rec_id)
    rec.status = RecommendationStatus.REJECTED
    rec.resolved_at = world.sim_time
    hub = world.hub(rec.spike.hub_id)
    world.log("SYSTEM", "RECOMMENDATION_REJECTED",
              f"{rec.id} rejected: {_litres(rec.spike.shortage_l)} for {hub.name} stays uncovered.",
              {"recommendation_id": rec.id, "options": _options_table(world, rec)})
    return rec


# --- Priority -------------------------------------------------------------------

def change_priority(world: WorldState, hub: Hub, priority: Priority) -> dict:
    _require_live(world)
    old = hub.priority
    hub.priority = priority
    change = f"{hub.name} priority {old.value} -> {priority.value}."
    base = {"hub_id": hub.id, "old_priority": old.value, "new_priority": priority.value}
    if world.phase == SimPhase.PLANNING:
        return dict(_replan(world, "PRIORITY_CHANGED", change, base), hub=hub)
    if priority != Priority.CRITICAL:
        world.log("SYSTEM", "PRIORITY_CHANGED", change, base)
        return {"hub": hub, "decision": world.decisions[-1]}

    update_all_etas(world)
    owners = [t for t in sorted(world.trucks, key=lambda t: t.id) if any(s.hub_id == hub.id for s in pending(t))]
    if not owners:
        world.log("SYSTEM", "PRIORITY_CRITICAL",
                  f"{change} No truck has a pending stop at {hub.name}; nothing to reorder.", dict(base, options=[]))
        return {"hub": hub, "decision": world.decisions[-1]}

    outcomes, table = [], []
    for truck in owners:
        stops = pending(truck)
        # Keep the stop in progress: the truck is driving to or unloading at stops[0].
        target = 1 if truck.status in (TruckStatus.EN_ROUTE, TruckStatus.UNLOADING) else 0
        pos = next(i for i, s in enumerate(stops) if s.hub_id == hub.id)
        keep = [{"hub": world.hub(s.hub_id).name, "eta": format_clock(s.eta), "slack_min": round(s.slack_min, 1)}
                for s in stops]
        if pos <= target:
            outcomes.append(f"{truck.id} already serves {hub.name} next")
            table.append({"truck_id": truck.id, "keep_order": keep, "moved": False, "reason": "already next"})
            continue
        reordered = stops[:target] + [stops[pos]] + stops[target:pos] + stops[pos + 1:]
        done = [s for s in truck.plan if s.status != StopStatus.PENDING]
        new_plan = done + reordered
        etas = _trial_etas(world, truck, new_plan)
        broken = _broken(world, truck, etas)
        row = {"truck_id": truck.id, "keep_order": keep, "serve_next": _etas_table(world, etas),
               "windows_broken": broken, "moved": not broken}
        if broken:
            row["reason"] = f"would make {', '.join(broken)} late"
            outcomes.append(f"{truck.id} keeps its order: serving {hub.name} next {row['reason']}")
        else:
            truck.plan = new_plan
            row["reason"] = "no other window breaks"
            new_eta = next(e.eta for e in etas if e.hub_id == hub.id)
            outcomes.append(f"{truck.id} now serves {hub.name} next (arrives {format_clock(new_eta)}); "
                            f"no other window breaks")
        table.append(row)
    update_all_etas(world)
    world.log("SYSTEM", "PRIORITY_CRITICAL", f"{change} " + "; ".join(outcomes) + ".", dict(base, options=table))
    return {"hub": hub, "decision": world.decisions[-1]}


# --- Validation -----------------------------------------------------------------------

NO_ROAD_MESSAGE = "No road access here — pick a point on a road"


def window_error(window_end: float) -> Optional[str]:
    earliest = config.DISPATCH_START + config.MIN_WINDOW_AFTER_DISPATCH_MIN
    if not earliest <= window_end <= config.LATEST_WINDOW_END:
        return (f"The window must end between {format_clock(earliest)} and "
                f"{format_clock(config.LATEST_WINDOW_END)}.")
    return None


def hub_fields_error(existing_names: List[str], name: str, demand_l: float, window_end: float) -> Optional[str]:
    """First problem with a new hub's name, demand or window, or None."""
    name = name.strip()
    if not config.HUB_NAME_MIN_LEN <= len(name) <= config.HUB_NAME_MAX_LEN:
        return f"Hub name must be {config.HUB_NAME_MIN_LEN}–{config.HUB_NAME_MAX_LEN} characters."
    if name.casefold() in {n.casefold() for n in existing_names}:
        return f"A hub named {name} already exists."
    if not config.HUB_DEMAND_MIN_L <= demand_l <= config.HUB_DEMAND_MAX_L:
        return f"Demand must be between {_litres(config.HUB_DEMAND_MIN_L)} and {_litres(config.HUB_DEMAND_MAX_L)}."
    return window_error(window_end)


def snap_error(snap: Optional[dict]) -> Optional[str]:
    """Why a snapped point cannot be a hub, or None. `snap` is routing.snap_to_road()'s result."""
    if snap is None:
        return "Could not reach the road-routing service to check road access; try again."
    if snap["snap_distance_m"] > config.MAX_SNAP_DISTANCE_M:
        return NO_ROAD_MESSAGE
    return None


# --- Windows and hub add / delete --------------------------------------------------

def change_window(world: WorldState, hub: Hub, window_end: float) -> dict:
    problem = window_error(window_end)
    if problem:
        raise ControlError(400, problem)
    _require_live(world)
    old = hub.window_end
    if abs(window_end - old) <= EPS:
        return {"hub": hub, "decision": None, "recommendation": None}
    hub.window_end = window_end
    change = f"{hub.name} window {format_clock(old)} -> {format_clock(window_end)}."
    base = {"hub_id": hub.id, "old_window_end": old, "new_window_end": window_end}
    if world.phase == SimPhase.PLANNING:
        return dict(_replan(world, "WINDOW_CHANGED", change, base), hub=hub, recommendation=None)
    update_all_etas(world)
    rows = [{"truck_id": t.id, "drop_l": s.drop_l, "eta": format_clock(s.eta), "slack_min": round(s.slack_min, 1),
             "risk": s.risk.value} for t in world.trucks for s in pending(t) if s.hub_id == hub.id]
    late = [r for r in rows if r["slack_min"] < 0]
    if late:
        outcome = "Now late: " + ", ".join(f"{r['truck_id']} arrives {r['eta']} ({-r['slack_min']:.0f} min late)"
                                           for r in late) + "."
    elif rows:
        outcome = "Every pending delivery still arrives in time."
    else:
        outcome = "No pending deliveries to this hub."
    world.log("SYSTEM", "WINDOW_CHANGED", f"{change} {outcome}", dict(base, options=rows))
    return {"hub": hub, "decision": world.decisions[-1], "recommendation": None}


def add_hub(world: WorldState, name: str, lat: float, lon: float, demand_l: float,
            window_end: Optional[float], priority: Priority, snap: Optional[dict]) -> dict:
    """Add a hub at its road point (`snap` from routing.snap_to_road, fetched outside the lock).

    Before start: replan. During the run: a new hub is a demand spike from 0 L.
    """
    _require_live(world)
    window_end = config.DEFAULT_WINDOW_END if window_end is None else window_end
    problem = hub_fields_error([h.name for h in world.hubs], name, demand_l, window_end) or snap_error(snap)
    if problem:
        raise ControlError(400, problem)
    existing = {h.id for h in world.hubs}
    n = 1
    while f"HUB-LIVE-{n}" in existing:
        n += 1
    executing = world.phase == SimPhase.EXECUTING
    hub = Hub(id=f"HUB-LIVE-{n}", name=name.strip(), lat=lat, lon=lon, road_lat=snap["road_lat"],
              road_lon=snap["road_lon"], snap_distance_m=snap["snap_distance_m"],
              demand_l=0 if executing else demand_l, window_end=window_end, priority=priority)
    world.hubs.append(hub)
    if executing:
        return change_demand(world, hub, demand_l)
    out = _replan(world, "HUB_ADDED", f"Hub {hub.name} added with {_litres(demand_l)} demand, "
                                      f"{hub.snap_distance_m:.0f} m from its road point.", {"hub_id": hub.id})
    return dict(out, hub=hub, recommendation=None)


def delete_hub(world: WorldState, hub: Hub) -> dict:
    if world.phase != SimPhase.PLANNING:
        raise ControlError(409, "Hubs can only be deleted before the simulation starts.")
    world.hubs = [h for h in world.hubs if h.id != hub.id]
    for truck in world.trucks:
        truck.plan = [s for s in truck.plan if s.hub_id != hub.id]
    out = _replan(world, "HUB_DELETED", f"Hub {hub.name} deleted.", {"hub_id": hub.id})
    return dict(out, hub=hub, recommendation=None)


# --- Fleet editing (before start) ------------------------------------------------------

def _require_planning(world: WorldState):
    if world.phase != SimPhase.PLANNING:
        raise ControlError(409, "The fleet can only be edited before the simulation starts.")


def add_truck(world: WorldState, truck_id: str, capacity_l: float) -> dict:
    _require_planning(world)
    truck_id = truck_id.strip()
    if world.truck(truck_id):
        raise ControlError(400, f"A truck named {truck_id} already exists.")
    world.trucks.append(state.make_truck(truck_id, capacity_l))
    return _replan(world, "FLEET_CHANGED", f"Truck {truck_id} ({_litres(capacity_l)}) added.", {"truck_id": truck_id})


def update_truck(world: WorldState, truck: Truck, capacity_l: float) -> dict:
    _require_planning(world)
    old = truck.capacity_l
    truck.capacity_l = capacity_l
    return _replan(world, "FLEET_CHANGED", f"{truck.id} capacity {_litres(old)} -> {_litres(capacity_l)}.",
                   {"truck_id": truck.id})


def delete_truck(world: WorldState, truck: Truck) -> dict:
    _require_planning(world)
    world.trucks = [t for t in world.trucks if t.id != truck.id]
    return _replan(world, "FLEET_CHANGED", f"Truck {truck.id} removed.", {"truck_id": truck.id})


# --- Dynamic resequencing (rain / traffic) -------------------------------------------------------------
# Roadblocks live in disruptions.py.

_risk_seen: Dict[Tuple[int, str], tuple] = {}


def check_and_resequence_high_risk_stops(world: WorldState):
    """When rain or traffic slows trucks and a stop's risk becomes High/Late,
    re-evaluate remaining stops order and propose/apply a resequence."""
    for truck in world.trucks:
        if truck.status not in (TruckStatus.EN_ROUTE, TruckStatus.RETURNING) or truck.reroute_pending:
            continue
        p = pending(truck)
        if len(p) < 2:
            continue
        has_high_or_late = any(s.risk in (Risk.HIGH, Risk.LATE) for s in p)
        if not has_high_or_late:
            continue
        # Only when the risks change (an escalation), not on every tick: permutations are not free.
        signature = tuple((s.hub_id, s.risk) for s in p)
        key = (id(world), truck.id)
        if _risk_seen.get(key) == signature:
            continue
        if len(_risk_seen) > 10_000:
            _risk_seen.clear()
        _risk_seen[key] = signature

        curr_slack = sum(s.slack_min for s in p if s.slack_min is not None)
        curr_late_cnt = sum(1 for s in p if s.slack_min is not None and s.slack_min < 0)

        best_perm = None
        best_score = (curr_late_cnt, -curr_slack)

        for perm in itertools.permutations(p):
            if list(perm) == p:
                continue
            trial_etas = _trial_etas(world, truck, list(perm))
            trial_late = sum(1 for e in trial_etas if e.slack_min < 0)
            trial_slack = sum(e.slack_min for e in trial_etas)
            score = (trial_late, -trial_slack)
            if score < best_score:
                best_score = score
                best_perm = list(perm)

        if best_perm:
            if getattr(state.settings, "control_mode", ControlMode.AUTO) == ControlMode.AUTO:
                done = [s for s in truck.plan if s.status == StopStatus.DONE]
                old_order = [s.hub_id for s in p]
                new_order = [s.hub_id for s in best_perm]
                truck.plan = done + best_perm
                update_truck_etas(world, truck)
                if old_order[0] != new_order[0]:
                    simulation.start_leg(world, truck, truck.status)
                world.log(truck.id, "RESEQUENCE",
                          f"{truck.id} stops resequenced to avoid delay: {new_order} (was {old_order})",
                          {"old_order": old_order, "new_order": new_order})
