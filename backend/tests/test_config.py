import pytest
from fastapi.testclient import TestClient

import config
import routing
import state
from api import app


def test_constants_match_spec():
    assert config.DISPATCH_START == 180
    assert config.DEFAULT_WINDOW_END == 360
    assert config.CITY_SPEED_KMPH == 30
    assert config.RAIN_SPEED_FACTOR == 0.6
    assert config.TRAFFIC_SPEED_FACTOR == 0.4
    assert config.DOCK_TIME_MIN == 10
    assert config.UNLOAD_RATE_L_PER_MIN == 100
    assert config.LOAD_BUFFER_PCT == 5
    assert config.FUEL_KM_PER_L == 5
    assert config.COST_PER_KM == 18
    assert config.SIM_SECONDS_PER_REAL_SECOND == 1
    assert config.SIM_SPEED_PRESETS == [1, 5, 15, 30, 60, 120]
    assert config.SIM_UPDATE_INTERVAL_SEC == 0.1 and config.MAX_SUBSTEP_SIM_SEC == 5
    assert config.PLANT["milk_available_l"] == 26000


def test_get_config_endpoint():
    with TestClient(app) as client:
        body = client.get("/config").json()
    assert body["CITY_SPEED_KMPH"] == 30
    assert body["DISPATCH_START"] == 180
    assert body["RISK_BANDS"] == {"LOW_MIN_SLACK": 30, "MEDIUM_MIN_SLACK": 10, "HIGH_MIN_SLACK": 0}
    assert body["PLANT"]["name"] == "Madhavaram Plant"
    assert len(body["DEFAULT_HUBS"]) == 8
    assert len(body["DEFAULT_TRUCKS"]) == 6


def test_starts_empty_then_loads_a_scenario():
    with TestClient(app) as client:
        world = client.get("/state").json()
        assert world["hubs"] == []                                    # no auto-loaded scenario
        assert world["plant"]["milk_available_l"] == 26000
        assert [t["capacity_l"] for t in world["trucks"]] == [6000, 6000, 5000, 4000, 4000, 3000]

        scenarios = client.get("/scenarios").json()
        assert scenarios[0]["id"] == "chennai-default" and scenarios[0]["total_demand_l"] == 22400
        assert client.post("/scenarios/nope/load").status_code == 404
        client.post("/scenarios/chennai-default/load")
        world = client.get("/state").json()
        assert sum(h["demand_l"] for h in world["hubs"]) == 22400
        assert all(h["remaining_l"] == h["demand_l"] for h in world["hubs"])

        client.put("/hubs/HUB-AMB/demand", json={"demand_l": 5000})
        client.post("/scenarios/chennai-default/load")
        amb = next(h for h in client.get("/state").json()["hubs"] if h["id"] == "HUB-AMB")
        assert amb["demand_l"] == 3200
        client.post("/scenario/empty")
        assert client.get("/state").json()["hubs"] == []


def test_scenario_coordinates_are_snapped_to_roads():
    for place in [config.PLANT] + config.DEFAULT_HUBS:
        assert place["snap_distance_m"] <= config.MAX_SNAP_DISTANCE_M
        moved_m = 1000 * routing.haversine(place["lat"], place["lon"], place["road_lat"], place["road_lon"])
        assert moved_m == pytest.approx(place["snap_distance_m"], abs=1.0)


def test_csv_upload_round_trip():
    with TestClient(app) as client:
        with open("sample_data/hubs.csv", "rb") as f:
            assert client.post("/upload/hubs", files={"file": ("hubs.csv", f)}).json()["count"] == 8
        with open("sample_data/trucks.csv", "rb") as f:
            assert client.post("/upload/trucks", files={"file": ("trucks.csv", f)}).json()["count"] == 6
        world = client.get("/state").json()
    assert world["hubs"][0]["window_end"] == 360
    assert world["trucks"][0]["speed_kmph"] == 30


def test_csv_missing_column_rejected():
    with pytest.raises(ValueError, match="demand_l"):
        state.parse_hubs_csv("id,name,lat,lon,window_end\nX,X,13,80,06:00\n")
