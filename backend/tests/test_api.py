from datetime import datetime, timezone
from app.main import app, compare, evaluation_runs, health, meta, recommend, reveal, station_forecast, stations, utilisation
from app.replay import ReplayStore
from app.schemas import Point, RecommendRequest
from scripts.import_ocm import CITIES, in_city


def test_health_and_meta_expose_four_cities():
    assert health()["status"] == "ok"
    cities = meta()["cities"]
    assert {city["slug"] for city in cities} == {"bengaluru", "mysuru", "hubballi-dharwad", "belagavi"}


def test_station_and_recommendation_contracts():
    result = stations(city="bengaluru", horizon_h=1, radius_km=None)
    assert result.stations and result.stations[0].forecast.q10 <= result.stations[0].forecast.q50 <= result.stations[0].forecast.q90
    request = RecommendRequest(origin=Point(lat=12.9716, lon=77.5946), as_of=datetime.now(timezone.utc), energy_kwh=20, radius_km=25)
    response = recommend(request)
    assert set(response.options) == {"FASTEST", "BALANCED", "GREENEST"}


def test_station_forecast_reveal_compare_and_insights():
    station_id = stations(city="mysuru", horizon_h=1, radius_km=None).stations[0].id
    assert len(station_forecast(station_id, hours_back=48).forecast) == 48
    now = datetime.now(timezone.utc)
    assert reveal(station_id, now).evaluation_only is True
    request = RecommendRequest(origin=Point(lat=12.2958, lon=76.6394), as_of=now, radius_km=25)
    assert compare(request).rows
    assert utilisation(from_=None, city="mysuru")["stations_analysed"] > 0


def test_openapi_declares_public_contract():
    paths = app.openapi()["paths"]
    assert "/api/v1/recommend" in paths
    assert "/api/v1/stations/{station_id}/forecast" in paths
    assert evaluation_runs()["runs"]


def test_replay_is_deterministic_for_a_fixed_seed():
    first, second = ReplayStore(42), ReplayStore(42)
    at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert first.stations == second.stations
    assert first.forecast(first.stations[0], at, 2) == second.forecast(second.stations[0], at, 2)


def test_ocm_locations_are_rejected_outside_city_boundaries():
    bbox = CITIES["bengaluru"]["bbox"]
    assert in_city({"AddressInfo": {"Latitude": 12.97, "Longitude": 77.59}}, bbox)
    assert not in_city({"AddressInfo": {"Latitude": 15.85, "Longitude": 74.49}}, bbox)
