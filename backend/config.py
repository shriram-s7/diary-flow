"""Single source of truth for DairyFlow constants (see CLAUDE.md).

All times are minutes from midnight, all quantities are litres, all distances km.
The frontend reads these values from GET /config and must not hardcode them.
"""
import os

# --- Server (own ports so DairyFlow runs alongside other projects) ----------
API_HOST = os.environ.get("DAIRYFLOW_API_HOST", "127.0.0.1")   # local only
API_PORT = int(os.environ.get("DAIRYFLOW_API_PORT", "8600"))
UI_PORT = int(os.environ.get("DAIRYFLOW_UI_PORT", "5600"))
# Browsers may call the API directly from these origins (the dev UI normally goes through its /api proxy).
UI_ORIGINS = [f"http://localhost:{UI_PORT}", f"http://127.0.0.1:{UI_PORT}"]

# --- Time window -----------------------------------------------------------
DISPATCH_START = 3 * 60          # 03:00
DEFAULT_WINDOW_END = 6 * 60      # 06:00

# One speed for planning, ETA and simulation.
CITY_SPEED_KMPH = 30.0
RAIN_SPEED_FACTOR = 0.6
TRAFFIC_SPEED_FACTOR = 0.4

# --- Disruption Engine -----------------------------------------------------
ROADBLOCK_DETECT_DELAY_MIN = 5.0     # 5 sim minutes after placement
ROADBLOCK_PROXIMITY_DETECT_M = 300.0 # truck detects block if within 300 m
ROADBLOCK_DEFAULT_RADIUS_M = 150.0   # point closure 150 m
ROADBLOCK_AREA_MAX_RADIUS_M = 2000.0 # area closure up to 2 km
RAIN_MIN_RADIUS_KM = 1.0
RAIN_MAX_RADIUS_KM = 8.0
TRAFFIC_INFLUENCE_DISTANCE_M = 50.0  # segments within 50 m of traffic point
TRAFFIC_STRETCH_RADIUS_KM = 0.5      # ~1 km stretch (500 m either side)
DETOUR_MAX_EXTRA_MIN = 15.0          # a detour costing more than 15 min ...
DETOUR_MAX_EXTRA_KM_PCT = 30.0       # ... or more than 30% extra km is still taken, plus a manager alert card
DETOUR_BUDGET_SEC = 4.0              # real-time budget for one detour search (all its OSRM requests together)
DETOUR_MAX_ATTEMPTS = 2              # a search that times out twice -> the truck stops (BLOCKED)
DETOUR_VIA_BEARINGS = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
DETOUR_VIA_DISTANCES_KM = [1.5, 3.0, 5.0]
OPENROUTESERVICE_API_KEY = os.environ.get("OPENROUTESERVICE_API_KEY", "")

# --- Service at a hub ------------------------------------------------------
DOCK_TIME_MIN = 10.0
UNLOAD_RATE_L_PER_MIN = 100.0    # service_min = DOCK_TIME_MIN + drop_l / UNLOAD_RATE_L_PER_MIN

# --- Loading ---------------------------------------------------------------
LOADING_TIME_MIN = 5.0           # at the plant, finishing at DISPATCH_START so trucks leave on time
LOAD_BUFFER_PCT = 5.0            # load planned drops + 5%, capped at capacity

# --- Fuel and cost ---------------------------------------------------------
FUEL_KM_PER_L = 5.0
COST_PER_KM = 18.0               # ₹

# --- Simulation clock ------------------------------------------------------
SIM_SECONDS_PER_REAL_SECOND = 1.0   # 1x = real time; the speed setting multiplies this
SIM_SPEED_PRESETS = [1, 5, 15, 30, 60, 120]   # the speed buttons in the UI
MAX_SIM_SPEED = 3600                # the API accepts any speed up to this (tests run faster than the UI)
SIM_TICK_HZ = 10                    # the simulation thread advances the world 10 times per real second
SIM_UPDATE_INTERVAL_SEC = 1 / SIM_TICK_HZ   # dt_sim = 0.1 s x speed per tick
MAX_SUBSTEP_SIM_SEC = 5.0           # motion is integrated in sub-steps of at most 5 simulated seconds

# --- Live transport and client motion ----------------------------------------------
WS_PUSH_HZ = 5                      # /ws pushes truck motion 5 times per real second
WS_WORLD_EVERY = 5                  # ... and the full world (hubs, plans, decisions) every 5th message (1 Hz)
WS_MAX_DECISIONS = 100              # decisions included in a pushed world
MOTION_EASE_MS = 300                # client eases a position correction over 300 ms
MOTION_TELEPORT_M = 500             # ... and only jumps if it is off by more than 500 m
HEADING_EASE_MS = 250               # truck icon rotation easing
REROUTE_FADE_MS = 1000              # old remaining route fades out (red dashed)
REROUTE_DRAW_MS = 1500              # new route draws progressively from the truck

# --- Risk bands from slack_min = window_end - eta --------------------------
# slack >= 30 Low, 10..30 Medium, 0..10 High, < 0 Late
RISK_LOW_MIN_SLACK = 30.0
RISK_MEDIUM_MIN_SLACK = 10.0
RISK_HIGH_MIN_SLACK = 0.0

# --- Hub validation ----------------------------------------------------------
HUB_NAME_MIN_LEN, HUB_NAME_MAX_LEN = 3, 30
HUB_DEMAND_MIN_L, HUB_DEMAND_MAX_L = 100, 20_000
MIN_WINDOW_AFTER_DISPATCH_MIN = 30   # window_end >= DISPATCH_START + 30 min
LATEST_WINDOW_END = 8 * 60           # 08:00
MAX_SNAP_DISTANCE_M = 300            # farther from a road than this = no road access

# --- Routing ---------------------------------------------------------------
# Network I/O never runs in the simulation tick, under the world lock, or on the event loop: every OSRM
# call runs in a worker thread (routing.py) and its result is applied on a later tick.
OSRM_BASE_URL = os.environ.get("DAIRYFLOW_OSRM_URL", "https://router.project-osrm.org")
OSRM_TIMEOUT_SEC = 3.0              # per HTTP request
OSRM_RETRIES = 2                    # retries after a failed request, before falling back to a straight line
OSRM_BACKOFF_SEC = 0.5              # wait 0.5 s, then 1 s between attempts
OSRM_PREFETCH_WORKERS = 4
OSRM_JOB_WORKERS = 8                # route / detour jobs (each may wait on several requests)
OSRM_REQUEST_WORKERS = 32           # individual OSRM requests (detour candidates are requested in parallel)
NEAREST_TIMEOUT_SEC = 3.0
NEAREST_BUDGET_SEC = 3.0            # snapping a clicked point (all attempts together)

# Generated files (route cache, logs) live outside backend/ so the dev reloader never sees them.
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get("DAIRYFLOW_CACHE_DIR", os.path.join(REPO_DIR, ".cache"))
ROUTE_CACHE_DIR = os.path.join(CACHE_DIR, "route_cache")   # every OSRM response
LOG_DIR = os.path.join(CACHE_DIR, "logs")

# --- Resilience --------------------------------------------------------------
SIM_TICK_BUDGET_MS = 200            # a tick slower than this is counted as slow (GET /health)
WS_STALE_MS = 3000                  # the UI shows "Reconnecting…" after this long without a /ws message
SEGMENT_MATCH_TOLERANCE_DEG = 0.0001 # coordinate tolerance when matching a traffic impact to a road segment

# --- Default Chennai scenario ---------------------------------------------
# Coordinates: (lat, lon) is the area centre, verified with Nominatim ("<area>, Chennai"; Tambaram:
# the town, as Nominatim's first hit is the bypass expressway) to within 300 m. (road_lat, road_lon) is that
# point snapped to the road network with OSRM /nearest; routes start and end there and trucks stop there.
PLANT = {
    "id": "PLANT",
    "name": "Madhavaram Plant",
    "lat": 13.1414, "lon": 80.2325,
    "road_lat": 13.141378, "road_lon": 80.232914, "snap_distance_m": 44.9,
    "milk_available_l": 26000,
}

DEFAULT_HUBS = [
    {"id": "HUB-AMB", "name": "Ambattur",     "lat": 13.1056, "lon": 80.1640, "road_lat": 13.106207, "road_lon": 80.164089, "snap_distance_m": 68.2, "demand_l": 3200},
    {"id": "HUB-ANN", "name": "Anna Nagar",   "lat": 13.0882, "lon": 80.2073, "road_lat": 13.088003, "road_lon": 80.207287, "snap_distance_m": 22.0, "demand_l": 2800},
    {"id": "HUB-TNG", "name": "T. Nagar",     "lat": 13.0378, "lon": 80.2318, "road_lat": 13.038006, "road_lon": 80.231856, "snap_distance_m": 23.7, "demand_l": 3500},
    {"id": "HUB-ADY", "name": "Adyar",        "lat": 13.0063, "lon": 80.2574, "road_lat": 13.006295, "road_lon": 80.257423, "snap_distance_m": 2.6, "demand_l": 2600},
    {"id": "HUB-VEL", "name": "Velachery",    "lat": 12.9802, "lon": 80.2229, "road_lat": 12.980085, "road_lon": 80.222902, "snap_distance_m": 12.8, "demand_l": 3000},
    {"id": "HUB-POR", "name": "Porur",        "lat": 13.0325, "lon": 80.1583, "road_lat": 13.032556, "road_lon": 80.157992, "snap_distance_m": 33.9, "demand_l": 2400},
    {"id": "HUB-TAM", "name": "Tambaram",     "lat": 12.9245, "lon": 80.1151, "road_lat": 12.924430, "road_lon": 80.115102, "snap_distance_m": 7.8, "demand_l": 3100},
    {"id": "HUB-TVT", "name": "Tiruvottiyur", "lat": 13.1586, "lon": 80.2962, "road_lat": 13.158522, "road_lon": 80.296275, "snap_distance_m": 11.9, "demand_l": 1800},
]

# Scenarios offered on the start screen ("Start empty" = the plant and the default fleet, no hubs).
SCENARIOS = {
    "chennai-default": {
        "name": "Chennai — 8 regional hubs",
        "description": "The standard morning run: 22,400 L to 8 hubs, all by 06:00.",
        "hubs": DEFAULT_HUBS,
    },
}

DEFAULT_TRUCKS = [
    {"id": "TRK-01", "capacity_l": 6000, "driver_name": "Murugan S", "driver_phone": "+91 98401 23451"},
    {"id": "TRK-02", "capacity_l": 6000, "driver_name": "Karthik R", "driver_phone": "+91 98402 34562"},
    {"id": "TRK-03", "capacity_l": 5000, "driver_name": "Saravanan P", "driver_phone": "+91 98403 45673"},
    {"id": "TRK-04", "capacity_l": 4000, "driver_name": "Dinesh K", "driver_phone": "+91 98404 56784"},
    {"id": "TRK-05", "capacity_l": 4000, "driver_name": "Venkatesh M", "driver_phone": "+91 98405 67895"},
    {"id": "TRK-06", "capacity_l": 3000, "driver_name": "Anand B", "driver_phone": "+91 98406 78906"},  # reserve-sized
]


def as_dict() -> dict:
    """Constants exposed to the frontend via GET /config."""
    return {
        "DISPATCH_START": DISPATCH_START,
        "DEFAULT_WINDOW_END": DEFAULT_WINDOW_END,
        "CITY_SPEED_KMPH": CITY_SPEED_KMPH,
        "RAIN_SPEED_FACTOR": RAIN_SPEED_FACTOR,
        "TRAFFIC_SPEED_FACTOR": TRAFFIC_SPEED_FACTOR,
        "ROADBLOCK_DETECT_DELAY_MIN": ROADBLOCK_DETECT_DELAY_MIN,
        "ROADBLOCK_PROXIMITY_DETECT_M": ROADBLOCK_PROXIMITY_DETECT_M,
        "ROADBLOCK_DEFAULT_RADIUS_M": ROADBLOCK_DEFAULT_RADIUS_M,
        "ROADBLOCK_AREA_MAX_RADIUS_M": ROADBLOCK_AREA_MAX_RADIUS_M,
        "RAIN_MIN_RADIUS_KM": RAIN_MIN_RADIUS_KM,
        "RAIN_MAX_RADIUS_KM": RAIN_MAX_RADIUS_KM,
        "TRAFFIC_INFLUENCE_DISTANCE_M": TRAFFIC_INFLUENCE_DISTANCE_M,
        "TRAFFIC_STRETCH_RADIUS_KM": TRAFFIC_STRETCH_RADIUS_KM,
        "DETOUR_MAX_EXTRA_MIN": DETOUR_MAX_EXTRA_MIN,
        "DETOUR_MAX_EXTRA_KM_PCT": DETOUR_MAX_EXTRA_KM_PCT,
        "DETOUR_BUDGET_SEC": DETOUR_BUDGET_SEC,
        "WS_STALE_MS": WS_STALE_MS,
        "DOCK_TIME_MIN": DOCK_TIME_MIN,
        "UNLOAD_RATE_L_PER_MIN": UNLOAD_RATE_L_PER_MIN,
        "LOADING_TIME_MIN": LOADING_TIME_MIN,
        "LOAD_BUFFER_PCT": LOAD_BUFFER_PCT,
        "FUEL_KM_PER_L": FUEL_KM_PER_L,
        "COST_PER_KM": COST_PER_KM,
        "SIM_SECONDS_PER_REAL_SECOND": SIM_SECONDS_PER_REAL_SECOND,
        "SIM_SPEED_PRESETS": SIM_SPEED_PRESETS,
        "SIM_TICK_HZ": SIM_TICK_HZ,
        "MAX_SUBSTEP_SIM_SEC": MAX_SUBSTEP_SIM_SEC,
        "WS_PUSH_HZ": WS_PUSH_HZ,
        "MOTION": {"EASE_MS": MOTION_EASE_MS, "TELEPORT_M": MOTION_TELEPORT_M, "HEADING_EASE_MS": HEADING_EASE_MS,
                   "REROUTE_FADE_MS": REROUTE_FADE_MS, "REROUTE_DRAW_MS": REROUTE_DRAW_MS},
        "RISK_BANDS": {
            "LOW_MIN_SLACK": RISK_LOW_MIN_SLACK,
            "MEDIUM_MIN_SLACK": RISK_MEDIUM_MIN_SLACK,
            "HIGH_MIN_SLACK": RISK_HIGH_MIN_SLACK,
        },
        "PLANT": dict(PLANT),
        "DEFAULT_HUBS": [dict(h, window_end=DEFAULT_WINDOW_END) for h in DEFAULT_HUBS],
        "DEFAULT_TRUCKS": [dict(t) for t in DEFAULT_TRUCKS],
        "HUB_RULES": {
            "NAME_MIN_LEN": HUB_NAME_MIN_LEN, "NAME_MAX_LEN": HUB_NAME_MAX_LEN,
            "DEMAND_MIN_L": HUB_DEMAND_MIN_L, "DEMAND_MAX_L": HUB_DEMAND_MAX_L,
            "WINDOW_EARLIEST": DISPATCH_START + MIN_WINDOW_AFTER_DISPATCH_MIN, "WINDOW_LATEST": LATEST_WINDOW_END,
            "MAX_SNAP_DISTANCE_M": MAX_SNAP_DISTANCE_M,
        },
    }
