"""World state, its lock, and scenario loading (default Chennai scenario and CSV).

Every read or mutation of `world` must hold `lock`. The simulation thread and
the API handlers share this one lock. Always access the world as `state.world`
(never `from state import world`), because a scenario reset replaces the object.
"""
import csv
import io
import threading
from typing import Iterable, List, Optional

import config
from models import Hub, Plant, Position, Settings, Truck, TruckStatus, WorldState

lock = threading.RLock()


def make_plant() -> Plant:
    return Plant(**config.PLANT)


def make_truck(id: str, capacity_l: float, speed_kmph: float = config.CITY_SPEED_KMPH,
               driver_name: Optional[str] = None, driver_phone: Optional[str] = None) -> Truck:
    return Truck(
        id=id,
        capacity_l=capacity_l,
        speed_kmph=speed_kmph,
        driver_name=driver_name,
        driver_phone=driver_phone,
        status=TruckStatus.AT_PLANT,
        position=Position(lat=make_plant().road_lat, lon=make_plant().road_lon),
    )


def build_world(hubs: Iterable[Hub], trucks: Iterable[Truck]) -> WorldState:
    return WorldState(plant=make_plant(), hubs=list(hubs), trucks=list(trucks))


def scenario_hubs(scenario_id: str) -> List[Hub]:
    return [Hub(window_end=config.DEFAULT_WINDOW_END, **h) for h in config.SCENARIOS[scenario_id]["hubs"]]


def default_hubs() -> List[Hub]:
    return scenario_hubs("chennai-default")


def default_trucks() -> List[Truck]:
    return [make_truck(t["id"], t["capacity_l"], driver_name=t.get("driver_name"), driver_phone=t.get("driver_phone"))
            for t in config.DEFAULT_TRUCKS]


def default_world() -> WorldState:
    return build_world(default_hubs(), default_trucks())


def empty_world() -> WorldState:
    """The plant and the default fleet, no hubs: what the app opens with."""
    return build_world([], default_trucks())


def scenario_world(scenario_id: str) -> WorldState:
    return build_world(scenario_hubs(scenario_id), default_trucks())


world: WorldState = empty_world()

# The world just before the last /simulation/start, restored by /simulation/reset.
snapshot: Optional[WorldState] = None

# Manager settings; kept across scenario resets.
settings = Settings()


def parse_clock(value: str) -> float:
    """Accepts "HH:MM" or minutes from midnight."""
    value = value.strip()
    if ":" in value:
        hh, mm = value.split(":")
        return int(hh) * 60 + int(mm)
    return float(value)


def _rows(text: str, required: List[str]) -> List[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")
    return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]


def parse_hubs_csv(text: str) -> List[Hub]:
    """Columns: id,name,lat,lon,demand_l,window_end (window_end as HH:MM or minutes; blank = default)."""
    hubs = []
    for row in _rows(text, ["id", "name", "lat", "lon", "demand_l", "window_end"]):
        hubs.append(Hub(
            id=row["id"],
            name=row["name"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            demand_l=float(row["demand_l"]),
            window_end=parse_clock(row["window_end"]) if row["window_end"] else config.DEFAULT_WINDOW_END,
        ))
    return hubs


def parse_trucks_csv(text: str) -> List[Truck]:
    """Columns: id,capacity_l,speed_kmph (speed_kmph blank = CITY_SPEED_KMPH)."""
    trucks = []
    for row in _rows(text, ["id", "capacity_l", "speed_kmph"]):
        speed = float(row["speed_kmph"]) if row["speed_kmph"] else config.CITY_SPEED_KMPH
        trucks.append(make_truck(row["id"], float(row["capacity_l"]), speed))
    return trucks
