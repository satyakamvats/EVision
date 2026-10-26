from datetime import datetime, timezone
from app.main import app, compare, datasets_status, evaluation_runs, health, meta, models_status, providers_status, recommend, reveal, station_forecast, stations, utilisation
from app.replay import ReplayStore
from app.schemas import Point, RecommendRequest
from app.decision import erlang_c_wait
from app.forecasting import apply_cqr, fit_cqr
from app.routing import FallbackRoutingProvider, haversine_km
from scripts.import_ocm import CITIES, in_city


def test_health_and_meta_expose_four_cities():
    assert health()["status"] == "ok"
    cities = meta()["cities"]
    assert {city["slug"] for city in cities} == {"bengaluru", "mysuru", "hubballi-dharwad", "belagavi"}


def test_phase_one_readiness_contracts_are_explicit():
    assert health()["data_source"] in {"synthetic", "urban_ev", "charged", "scenario_estimate"}
    assert "providers" in providers_status()
    assert datasets_status()["datasets"]["synthetic"]["available"] is True
    assert "active_model_version" in models_status()


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
    assert "/api/v1/geocode" in paths
    assert evaluation_runs()["runs"]


def test_phase_two_authenticated_contract_is_declared():
    paths = app.openapi()["paths"]
    for path in (
        "/api/v1/users/me",
        "/api/v1/users/me/preferences",
        "/api/v1/users/me/favourites",
        "/api/v1/users/me/favourites/{station_id}",
        "/api/v1/users/me/recommendation-history",
        "/api/v1/users/me/recommendation-history/{history_id}",
    ):
        assert path in paths
    assert "get" in paths["/api/v1/users/me"]


def test_replay_is_deterministic_for_a_fixed_seed():
    first, second = ReplayStore(42), ReplayStore(42)
    at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert first.stations == second.stations
    assert first.forecast(first.stations[0], at, 2) == second.forecast(second.stations[0], at, 2)


def test_ocm_locations_are_rejected_outside_city_boundaries():
    bbox = CITIES["bengaluru"]["bbox"]
    assert in_city({"AddressInfo": {"Latitude": 12.97, "Longitude": 77.59}}, bbox)
    assert not in_city({"AddressInfo": {"Latitude": 15.85, "Longitude": 74.49}}, bbox)


def test_queueing_is_zero_at_zero_occupancy_and_monotonic():
    empty, empty_wait = erlang_c_wait(4, .75, 0)
    _, busy_wait = erlang_c_wait(4, .75, 2)
    assert empty_wait == 0
    assert empty.p_wait == 0
    assert busy_wait > empty_wait


def test_routing_has_explicit_fallback_and_distance():
    provider = FallbackRoutingProvider()
    result = provider.route((12.9716, 77.5946), (12.9816, 77.6046))
    assert result.source == "haversine_fallback"
    assert result.distance_km > haversine_km(12.9716, 77.5946, 12.9816, 77.6046)


def test_cqr_expands_and_keeps_quantiles_ordered():
    qhat = fit_cqr([.2, .3, .4], [.6, .7, .8], [.4, .5, .6])
    low, median, high = apply_cqr([.2], [.4], [.6], qhat)
    assert low[0] <= median[0] <= high[0]
    assert 0 <= low[0] <= 1 and 0 <= high[0] <= 1
