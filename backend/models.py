"""Pydantic models for DairyFlow. Entity names and units follow CLAUDE.md.

Units: litres (_l), minutes from midnight (eta, window_end, time), km, kmph.
"""
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from typing_extensions import Annotated

from pydantic import BaseModel, Field, computed_field, model_validator

import config


class Priority(str, Enum):
    NORMAL = "Normal"
    HIGH = "High"
    CRITICAL = "Critical"


class HubStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    UNREACHABLE = "UNREACHABLE"


class TruckStatus(str, Enum):
    RESERVE = "RESERVE"
    LOADING = "LOADING"
    EN_ROUTE = "EN_ROUTE"
    UNLOADING = "UNLOADING"
    RETURNING = "RETURNING"
    AT_PLANT = "AT_PLANT"
    BLOCKED = "BLOCKED"      # stopped safely: no route around a roadblock (waits for the manager or clearance)


class StopStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"


class Risk(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    LATE = "Late"


class Position(BaseModel):
    lat: float
    lon: float


class Located(BaseModel):
    """A place with its raw coordinates and the point on the road network where trucks stop.

    (lat, lon) is where the place is; (road_lat, road_lon) is that point snapped to the nearest road
    (OSRM /nearest). Routing, ETAs, arrivals and map pins all use the road point.
    """
    lat: float
    lon: float
    road_lat: Optional[float] = None
    road_lon: Optional[float] = None
    snap_distance_m: Optional[float] = None   # None: not snapped (road point = raw point)

    @model_validator(mode="after")
    def _default_road_point(self):
        if self.road_lat is None or self.road_lon is None:
            self.road_lat, self.road_lon = self.lat, self.lon
        return self

    @property
    def road(self) -> "tuple[float, float]":
        return self.road_lat, self.road_lon


class Plant(Located):
    id: str
    name: str
    milk_available_l: float


class HubDelivery(BaseModel):
    """One truck's stop at a hub, copied from that truck's Stop each tick (same numbers everywhere)."""
    truck_id: str
    drop_l: float
    delivered_l: float
    eta: Optional[float]
    arrived_at: Optional[float]
    completed_at: Optional[float]
    unload_min_left: Optional[float]
    slack_min: Optional[float]
    risk: Optional[Risk]


class Hub(Located):
    id: str
    name: str
    demand_l: float
    delivered_l: float = 0.0
    window_end: float = config.DEFAULT_WINDOW_END
    priority: Priority = Priority.NORMAL
    status: HubStatus = HubStatus.PENDING
    # From timing.update_hub_etas each tick:
    eta: Optional[float] = None          # latest future arrival of a truck still on its way (never in the past)
    slack_min: Optional[float] = None    # worst slack over all its stops
    risk: Optional[Risk] = None          # worst risk over all its stops
    deliveries: List[HubDelivery] = []   # every truck's stop here, in arrival order

    @computed_field
    @property
    def remaining_l(self) -> float:
        return max(0.0, self.demand_l - self.delivered_l)


class Stop(BaseModel):
    hub_id: str
    drop_l: float
    eta: Optional[float] = None              # arrival: predicted until arrived_at is set, then actual
    window_end: float
    slack_min: Optional[float] = None
    risk: Optional[Risk] = None
    status: StopStatus = StopStatus.PENDING
    delivered_l: float = 0.0                 # rises continuously while unloading
    arrived_at: Optional[float] = None
    completed_at: Optional[float] = None
    unload_min_left: Optional[float] = None  # while unloading

    @property
    def undelivered_l(self) -> float:
        return max(0.0, self.drop_l - self.delivered_l)


class Truck(BaseModel):
    id: str
    capacity_l: float
    load_l: float = 0.0
    speed_kmph: float = config.CITY_SPEED_KMPH
    fuel_km_per_l: float = config.FUEL_KM_PER_L
    cost_per_km: float = config.COST_PER_KM
    status: TruckStatus = TruckStatus.AT_PLANT
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    plan: List[Stop] = []
    position: Position
    # Execution state
    route: List[Position] = []           # polyline of the leg currently being driven
    route_index: int = 0                 # index of the next polyline point
    route_source: Optional[str] = None   # "osrm" or "fallback"
    route_id: Optional[str] = None       # id of the Route object for this leg (for reroute detection)
    distance_along_route_m: float = 0.0  # metres from route[0] to the truck's current position
    # Roadblocks (disruptions.py). While a detour is being searched the truck keeps driving on its route but
    # not past route[hold_index]: it stops there (at the block) and waits for the result.
    reroute_pending: bool = False
    hold_index: Optional[int] = None
    blocked_by: Optional[str] = None     # roadblock id that stopped (BLOCKED) or last rerouted the truck
    activity_remaining_min: float = 0.0  # LOADING or UNLOADING time left
    km_driven: float = 0.0
    fuel_used_l: float = 0.0
    return_km: float = 0.0               # part of km_driven spent RETURNING
    return_fuel_l: float = 0.0
    returned_at: Optional[float] = None  # last arrival back at the plant
    # Driver communication
    instructions: Optional[str] = None
    instruction_status: Optional[str] = None

    def current_stop(self) -> Optional[Stop]:
        return next((s for s in self.plan if s.status == StopStatus.PENDING), None)

    @computed_field
    @property
    def committed_l(self) -> float:
        """Milk on board promised to pending stops (not yet unloaded)."""
        return sum(s.undelivered_l for s in self.plan if s.status == StopStatus.PENDING)

    @computed_field
    @property
    def spare_l(self) -> float:
        """Milk on board not promised to any stop: returning to the plant unless reassigned."""
        return max(0.0, self.load_l - self.committed_l)


class Decision(BaseModel):
    time: float
    truck_id: str  # a truck id or "SYSTEM"
    type: str
    summary: str
    details: Optional[Dict[str, Any]] = None


class RainImpact(BaseModel):
    id: str
    type: Literal["rain"] = "rain"
    lat: float
    lon: float
    radius_km: float
    road_name: Optional[str] = None
    created_at: float


class TrafficImpact(BaseModel):
    """Traffic congestion along a ~1 km road stretch centered at (lat, lon)."""
    id: str
    type: Literal["traffic"] = "traffic"
    lat: float
    lon: float
    radius_km: float = 0.5
    road_name: Optional[str] = None
    truck_id: Optional[str] = None
    segment_index: Optional[int] = None
    start: Optional[Position] = None
    end: Optional[Position] = None
    created_at: float


class RoadblockImpact(BaseModel):
    """Point closure (150 m) or area closure (up to 2 km). Impassable to traffic."""
    id: str
    type: Literal["roadblock"] = "roadblock"
    lat: float
    lon: float
    radius_m: float = 150.0
    road_name: Optional[str] = None
    active: bool = True
    created_at: float


Impact = Annotated[Union[RainImpact, TrafficImpact, RoadblockImpact], Field(discriminator="type")]


class TruckPlan(BaseModel):
    truck_id: str
    capacity_l: float
    stops: List[Stop]
    planned_l: float          # sum of drop_l
    load_l: float             # planned_l + LOAD_BUFFER_PCT, capped at capacity and plant stock
    utilisation_pct: float    # planned_l / capacity_l
    total_km: float           # plant -> stops -> plant, haversine
    fuel_l: float
    cost_inr: float
    return_eta: float         # back at the plant, minutes from midnight


class ReserveTruck(BaseModel):
    truck_id: str
    capacity_l: float
    reason: str


class HubShortfall(BaseModel):
    hub_id: str
    name: str
    priority: Priority
    window_end: float
    demand_l: float
    planned_l: float
    unserved_l: float
    reason: str


class DispatchPlan(BaseModel):
    created_at: float
    start_time: float
    total_demand_l: float
    total_planned_l: float
    milk_available_l: float
    fleet_capacity_l: float
    trucks_used: int
    total_km: float
    total_fuel_l: float
    total_cost_inr: float
    windows_met: bool
    truck_plans: List[TruckPlan] = []
    reserve_trucks: List[ReserveTruck] = []
    unserved: List[HubShortfall] = []
    warnings: List[str] = []
    decisions: List[str] = []


class HubResult(BaseModel):
    hub_id: str
    name: str
    demand_l: float
    delivered_l: float
    unserved_l: float
    window_end: float
    last_arrival: Optional[float]
    on_time: bool             # fully delivered and every stop arrived by window_end


class TruckResult(BaseModel):
    truck_id: str
    used: bool
    delivered_l: float
    km: float
    fuel_l: float
    cost_inr: float
    return_km: float
    return_fuel_l: float


class SimulationSummary(BaseModel):
    completed_at: float
    total_demand_l: float
    total_delivered_l: float
    stops_total: int
    stops_on_time: int
    on_time_pct: float        # share of completed stops that arrived by window_end
    total_km: float
    total_fuel_l: float
    total_cost_inr: float
    trucks_used: List[str]
    trucks_reserve: List[str]
    hubs: List[HubResult]
    trucks: List[TruckResult]


class ControlMode(str, Enum):
    AUTO = "auto"         # apply the recommended option immediately
    APPROVE = "approve"   # store it until a manager approves or rejects it


class Settings(BaseModel):
    control_mode: ControlMode = ControlMode.AUTO


class StopEta(BaseModel):
    hub_id: str
    drop_l: float
    eta: float
    window_end: float
    slack_min: float
    risk: Risk


class OptionPart(BaseModel):
    truck_id: str
    action: Literal["top_up", "insert", "new_trip"]
    drop_l: float
    added_km: float
    etas: List[StopEta]          # every remaining stop of this truck after the change


class Option(BaseModel):
    id: str                      # e.g. "A:TRK-02", "B:TRK-06", "C:TRK-02+TRK-06"
    kind: Literal["A", "B", "C"]
    parts: List[OptionPart]
    litres_covered: float
    added_km: float
    windows_broken: List[str]    # "Hub (TRK-xx) +12 min" for stops this option would make late
    summary: str


class Spike(BaseModel):
    hub_id: str
    hub_name: str
    old_demand_l: float
    new_demand_l: float
    remaining_l: float           # new demand - delivered
    committed_l: float           # already on its way to this hub
    shortage_l: float            # remaining - committed


class RecommendationStatus(str, Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class Recommendation(BaseModel):
    id: str
    created_at: float
    spike: Spike
    options: List[Option]
    chosen: Optional[str]        # option id, None if nothing can help
    reason: str
    status: RecommendationStatus = RecommendationStatus.PENDING
    resolved_at: Optional[float] = None
    exclude: List[str] = []      # trucks not considered (the truck that handed the stop over)


class AlertOption(BaseModel):
    id: str            # action for POST /decisions/resolve
    label: str
    description: str = ""


class Alert(BaseModel):
    """A card for the manager. DETOUR_COSTLY: the truck already took a costly detour (nothing happens if the
    card is ignored). NO_ROUTE: the truck stopped safely (BLOCKED) and waits for a decision or clearance."""
    id: str
    kind: Literal["DETOUR_COSTLY", "NO_ROUTE"]
    truck_id: str
    hub_id: Optional[str] = None
    hub_name: Optional[str] = None
    roadblock_id: Optional[str] = None
    created_at: float
    summary: str
    extra_km: Optional[float] = None
    extra_min: Optional[float] = None
    extra_km_pct: Optional[float] = None
    windows_broken: List[str] = []
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    options: List[AlertOption] = []
    default_option: Optional[str] = None
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    resolution: Optional[str] = None
    resolved_at: Optional[float] = None


class SimPhase(str, Enum):
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


class WorldState(BaseModel):
    plant: Plant
    hubs: List[Hub] = []
    trucks: List[Truck] = []
    decisions: List[Decision] = []
    impacts: List[Impact] = []
    impact_seq: int = 0
    sim_time: float = config.DISPATCH_START
    sim_speed: float = 1.0
    running: bool = False
    paused: bool = False
    phase: SimPhase = SimPhase.PLANNING
    plan: Optional[DispatchPlan] = None
    summary: Optional[SimulationSummary] = None
    recommendations: List[Recommendation] = []
    recommendation_seq: int = 0
    alerts: List[Alert] = []
    alert_seq: int = 0

    def hub(self, hub_id: str) -> Optional[Hub]:
        return next((h for h in self.hubs if h.id == hub_id), None)

    def truck(self, truck_id: str) -> Optional[Truck]:
        return next((t for t in self.trucks if t.id == truck_id), None)

    def log(self, truck_id: str, type: str, summary: str, details: Optional[Dict[str, Any]] = None,
            time: Optional[float] = None):
        self.decisions.append(Decision(time=self.sim_time if time is None else time, truck_id=truck_id,
                                       type=type, summary=summary, details=details))


# --- Request bodies --------------------------------------------------------

class HubCreateRequest(BaseModel):
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    demand_l: float = Field(allow_inf_nan=False)
    window_end: Optional[float] = Field(default=None, allow_inf_nan=False)
    priority: Priority = Priority.NORMAL


class SnapResult(BaseModel):
    lat: float
    lon: float
    road_lat: Optional[float]
    road_lon: Optional[float]
    snap_distance_m: Optional[float]
    road_name: Optional[str] = None
    ok: bool
    message: str


class TruckCreate(BaseModel):
    id: str = Field(min_length=1, max_length=20)
    capacity_l: float = Field(gt=0, le=50_000, allow_inf_nan=False)


class TruckUpdate(BaseModel):
    capacity_l: float = Field(gt=0, le=50_000, allow_inf_nan=False)


class HubWindowUpdate(BaseModel):
    window_end: float = Field(ge=0, lt=24 * 60, allow_inf_nan=False)  # minutes from midnight


class HubDemandUpdate(BaseModel):
    demand_l: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)


class HubPriorityUpdate(BaseModel):
    priority: Priority


class SpeedRequest(BaseModel):
    speed: float = Field(gt=0)


class InstructionRequest(BaseModel):
    instruction: str


class RainImpactCreate(BaseModel):
    lat: float
    lon: float
    radius_km: float = Field(gt=0)


class TrafficImpactCreate(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    truck_id: Optional[str] = None
    segment_index: Optional[int] = None


class RoadblockImpactCreate(BaseModel):
    lat: float
    lon: float
    radius_m: float = Field(default=150.0, ge=50.0, le=2000.0)


class DecisionResolveRequest(BaseModel):
    truck_id: str
    action: str
    alert_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
