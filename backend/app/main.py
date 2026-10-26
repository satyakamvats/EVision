from datetime import datetime, timedelta, timezone
from math import sin
from uuid import UUID
import httpx
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import cast, func, select, text
from geoalchemy2 import Geometry
from .database import session_factory
from .models import City, Station, StationConnector, FavouriteStation, RecommendationHistory, UserPreference, UserProfile
from .auth import AuthUser, current_user, optional_user
from .carbon import ElectricityMapsCarbonProvider, ScenarioCarbonProvider
from .decision import DecisionEngine, erlang_c_wait
from .errors import api_error
from .evaluation import run_synthetic_replay
from .forecast_runtime import ForecastRuntime
from .geocoding import NominatimGeocoder
from .routing import FallbackRoutingProvider, OsrmRoutingProvider, RouteResult, haversine_km
from .replay import CITIES, ReplayStore, ScenarioReplayStore, erlang_c
from .schemas import CompareOut, CompareRow, FavouriteRequest, PreferencesPatch, QueueExplainOut, RecommendOut, RecommendRequest, RecommendationOption, RevealOut, RouteRequest, StationDetailOut, StationForecastOut, StationListOut, UserProfilePatch
from .settings import get_settings


settings = get_settings()
logger = logging.getLogger("evision.api")
rate_buckets: dict[tuple[str, str], list[float]] = {}
scenario_root = Path(settings.artifacts_dir) / "datasets" / "karnataka_scenario"
store = ScenarioReplayStore(scenario_root) if (scenario_root / "stations.json").exists() and (scenario_root / "observations.jsonl").exists() else ReplayStore(settings.seed)
router = FallbackRoutingProvider(OsrmRoutingProvider(settings.routing_url, settings.routing_timeout_s, settings.routing_cache_ttl_s) if settings.routing_provider == "osrm" else None)
geocoder = NominatimGeocoder(settings.geocoding_url, settings.geocoding_user_agent, settings.geocoding_min_interval_s)
carbon_provider = ElectricityMapsCarbonProvider(settings.electricity_maps_api_key, ScenarioCarbonProvider()) if settings.mode != "synthetic_replay" else ScenarioCarbonProvider()
engine = DecisionEngine(store, routing=router, carbon=carbon_provider)
if isinstance(store, ScenarioReplayStore):
    model_root = Path(settings.artifacts_dir) / "models" / "scenario_estimate"
    if (model_root / "registry.json").exists():
        try:
            store.set_forecast_provider(ForecastRuntime(model_root))
        except Exception:
            logger.exception("registered forecast artifacts could not be loaded; deterministic fallback remains active")


def load_database_catalogue() -> list[dict]:
    """Use imported locations when the local catalogue has been seeded.

    Availability and forecasts remain deterministic replay values; only public
    station metadata and coordinates originate from Open Charge Map.
    """
    session = session_factory()()
    try:
        location_geometry = cast(Station.location, Geometry("POINT", srid=4326))
        rows = session.execute(select(Station, City.slug, func.ST_Y(location_geometry), func.ST_X(location_geometry)).join(City, City.id == Station.city_id).where(Station.provider == "Open Charge Map")).all()
        loaded: list[dict] = []
        for station, city, lat, lng in rows:
            connectors = list(session.scalars(select(StationConnector.connector_type).where(StationConnector.station_id == station.id)))
            loaded.append({"id": station.external_id, "city": city, "name": station.name, "address": station.address or city, "lat": float(lat), "lng": float(lng), "capacity": station.capacity, "chargers": station.capacity, "power_kw": station.rated_power_kw or 7.2, "connectors": connectors or ["Unknown"], "price": 16.0, "rating": 4.5, "attribution": station.attribution})
        return loaded
    finally:
        session.close()


try:
    imported_catalogue = load_database_catalogue()
    if imported_catalogue and not isinstance(store, ScenarioReplayStore):
        store.stations = imported_catalogue
except Exception:
    # Development fallback keeps the API usable before PostGIS is initialised.
    pass
app = FastAPI(title="EVision API", version="0.2.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=api_error("validation_error", "Request validation failed.", {"errors": exc.errors()}))


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else api_error("request_error", str(exc.detail))
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.middleware("http")
async def request_id_middleware(request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"evision-{id(request)}"
    started = time.perf_counter()
    bucket = "/recommend" if request.url.path in {"/api/v1/recommend", "/api/v1/recommend/compare"} else "/reveal" if request.url.path.endswith("/reveal") else None
    remaining = None
    if bucket:
        limit = settings.recommendation_rate_limit_per_minute if bucket == "/recommend" else settings.reveal_rate_limit_per_minute
        now = time.monotonic()
        client = request.client.host if request.client else "unknown"
        key = (client, bucket)
        recent = [stamp for stamp in rate_buckets.get(key, []) if now - stamp < 60]
        if len(recent) >= limit:
            response = JSONResponse(status_code=429, content=api_error("rate_limited", "Too many requests. Please retry shortly."), headers={"Retry-After": "60", "X-RateLimit-Remaining": "0"})
            response.headers["X-Request-ID"] = request_id
            return response
        recent.append(now)
        rate_buckets[key] = recent
        remaining = limit - len(recent)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    logger.info(json.dumps({"request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}))
    return response


def aware(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def active_data_source() -> str:
    return getattr(store, "data_source", "synthetic") if settings.mode == "synthetic_replay" else settings.mode


def routed_summaries(summaries: list, origin: tuple[float, float] | None) -> list:
    if origin is None:
        return summaries
    # The map can contain up to 100 stations.  Routing every marker through
    # the public OSRM service makes a new map click look stuck.  Keep routed
    # times for the nearest candidates and use the same explicit fallback
    # used elsewhere for the remaining visual markers.
    for index, summary in enumerate(summaries):
        if index < 12:
            result = router.route(origin, (summary.lat, summary.lng))
        else:
            distance = haversine_km(*origin, summary.lat, summary.lng) * 1.15
            result = RouteResult(round(distance, 3), round(distance / 30 * 60, 1), "haversine_fallback")
        summary.distance_km = result.distance_km
        summary.eta_min = result.duration_min
        summary.route_source = result.source
    return summaries


def database_ready() -> bool:
    session = None
    try:
        session = session_factory()()
        session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        if session:
            session.close()


def readiness_checks() -> dict[str, bool]:
    artifacts = Path(settings.artifacts_dir)
    model_root = artifacts / "models"
    return {
        "database": database_ready(),
        "dataset_artifacts": artifacts.exists() or settings.mode == "synthetic_replay",
        "active_model": bool(store.model_version),
        "routing_provider": settings.routing_provider in {"haversine", "osrm"},
        "authentication": bool(settings.supabase_url) or settings.mode == "synthetic_replay",
        "model_artifacts": model_root.exists() or settings.mode == "synthetic_replay",
    }


@app.get("/api/v1/health")
def health():
    checks = readiness_checks()
    operational = all(value for name, value in checks.items() if name != "database") and (checks["database"] or settings.mode == "synthetic_replay")
    return {"status": "ok" if operational else "degraded", "mode": settings.mode, "data_source": active_data_source(), "checks": checks, "database_configured": bool(settings.database_url), "model_version": store.model_version, "auth_configured": bool(settings.supabase_url), "routing_provider": settings.routing_provider}


@app.get("/api/v1/readiness")
def readiness():
    checks = readiness_checks()
    ready = all(value for name, value in checks.items() if name != "database") and (checks["database"] or settings.mode == "synthetic_replay")
    return {"ready": ready, "checks": checks, "data_source": active_data_source(), "model_version": store.model_version}


@app.get("/api/v1/meta")
def meta():
    return {"data_source": active_data_source(), "mode": settings.mode, "currency": "INR", "cities": [{"slug": c.slug, "name": c.name} for c in CITIES], "horizons": [1, 2, 3], "defaults": {"energy_kwh": 20, "radius_km": 25, "risk_kappa": .5}, "model_version": store.model_version, "carbon_scenarios": ["flat", "tou_profile", "low", "high"]}


@app.get("/api/v1/providers/status")
def providers_status():
    return {"data_source": active_data_source(), "providers": {"open_charge_map": {"configured": bool(settings.ocm_api_key), "role": "station_metadata_only"}, "electricity_maps": {"configured": bool(settings.electricity_maps_api_key and settings.mode != "synthetic_replay"), "role": "optional_carbon_observation"}, "routing": {"configured": settings.routing_provider == "osrm", "provider": settings.routing_provider, "fallback": "haversine"}, "supabase": {"configured": bool(settings.supabase_url), "role": "authentication"}}}


@app.get("/api/v1/datasets/status")
def datasets_status():
    artifacts = Path(settings.artifacts_dir)
    root = artifacts / "datasets"
    datasets = {}
    for source in ("synthetic", "urban_ev", "charged", "open_charge_map"):
        metadata_path = root / source / "metadata.json"
        quality_path = root / source / "quality_report.json"
        quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
        available = metadata_path.exists() and quality.get("available", True)
        status = "ready" if available and not quality.get("errors") else "empty" if quality_path.exists() and not quality.get("available", True) else "quality_failed" if quality_path.exists() and quality.get("errors") else "not_loaded"
        datasets[source] = {"available": available, "status": status, "metadata": json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else None, "quality": quality or None}
    return {"active": active_data_source(), "datasets": datasets}


@app.get("/api/v1/models/status")
def models_status():
    model_root = Path(settings.artifacts_dir) / "models"
    versions = sorted(str(path) for path in model_root.glob("**/metadata.json")) if model_root.exists() else []
    registry_path = model_root / "scenario_estimate" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else None
    return {"active_model_version": store.model_version, "forecast_models_available": bool(versions) or registry is not None, "registered_models": versions, "uncertainty_calibrated": any("horizon" in path for path in versions) and registry is not None, "data_source": active_data_source(), "calibration": "validation_only_split_conformal_quantile" if registry else "deterministic_fallback", "registry": registry}


@app.get("/api/v1/carbon-intensity")
def carbon_intensity(lat: float, lon: float, scenario: str = "tou_profile", as_of: datetime | None = None):
    result = engine.carbon.intensity(aware(as_of), lat, lon, scenario)
    return {"carbon_source": result.source, "scenario": result.scenario, "intensity_g_per_kwh": result.intensity_g_per_kwh}


@app.post("/api/v1/route")
def route(request: RouteRequest):
    result = router.route((request.origin.lat, request.origin.lon), (request.destination.lat, request.destination.lon))
    return {"distance_km": result.distance_km, "duration_min": result.duration_min, "source": result.source, "data_source": active_data_source(), "geometry": result.geometry}


@app.get("/api/v1/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=200), limit: int = Query(5, ge=1, le=10)):
    try:
        results = geocoder.search(q.strip(), limit)
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise HTTPException(503, api_error("geocoding_unavailable", "The geocoding provider is temporarily unavailable.")) from exc
    return {"provider": "nominatim", "results": [result.__dict__ for result in results]}


@app.get("/api/v1/stations", response_model=StationListOut)
def stations(as_of: datetime | None = None, horizon_h: int = Query(1, ge=0, le=3), origin_lat: float | None = None, origin_lon: float | None = None, radius_km: float | None = Query(None, ge=.5, le=100), city: str | None = None):
    if city and city not in {c.slug for c in CITIES}:
        raise HTTPException(422, "Unsupported city")
    origin = (origin_lat, origin_lon) if origin_lat is not None and origin_lon is not None else None
    return StationListOut(as_of=aware(as_of), data_source=active_data_source(), stations=routed_summaries(store.summaries(aware(as_of), city, origin, radius_km, horizon_h), origin))


@app.get("/api/v1/stations/{station_id}", response_model=StationDetailOut)
def station_detail(station_id: str, as_of: datetime | None = None):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    now = aware(as_of)
    summary = next(item for item in store.summaries(now, station["city"]) if item.id == station_id)
    nearby = [item for item in routed_summaries(store.summaries(now, station["city"], (station["lat"], station["lng"]), 20), (station["lat"], station["lng"])) if item.id != station_id][:4]
    return StationDetailOut(data_source=active_data_source(), station=summary, utilisation={"mean_occupancy_rate": round(store.occupancy(station, now), 3), "peak_occupancy_rate": .88}, nearby=nearby)


@app.get("/api/v1/stations/{station_id}/forecast", response_model=StationForecastOut)
def station_forecast(station_id: str, as_of: datetime | None = None, hours_back: int = Query(48, ge=1, le=168)):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    now = aware(as_of)
    from .schemas import ForecastPoint
    history = [ForecastPoint(at=now-timedelta(hours=h), observed=round(store.occupancy(station, now-timedelta(hours=h)), 3)) for h in range(hours_back, -1, -1)]
    forecast = [ForecastPoint(at=now+timedelta(hours=h), q10=store.forecast(station, now, min(h, 3)).q10, q50=store.forecast(station, now, min(h, 3)).q50, q90=store.forecast(station, now, min(h, 3)).q90) for h in range(1, 49)]
    return StationForecastOut(station_id=station_id, as_of=now, data_source=active_data_source(), model_version=store.model_version, history=history, forecast=forecast)


@app.get("/api/v1/stations/{station_id}/reveal", response_model=RevealOut)
def reveal(station_id: str, as_of: datetime):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    observed = store.occupancy(station, aware(as_of) + timedelta(hours=1))
    _, wait = erlang_c_wait(station["capacity"], .75, observed * station["capacity"])
    return RevealOut(station_id=station_id, as_of=aware(as_of), data_source=active_data_source(), observed_occupancy_rate=round(observed, 3), realised_wait_min=wait, realised_regret_inr=round(wait * 2.5, 2))


def recommendation(request: RecommendRequest) -> RecommendOut:
    labels, pareto, dominated = engine.labels(request)
    carbon = engine.carbon.intensity(request.as_of, request.origin.lat, request.origin.lon, request.carbon_scenario)
    return RecommendOut(as_of=request.as_of, data_source=active_data_source(), model_version=store.model_version, carbon_source="observed" if carbon.source == "observed" else "scenario", options=labels, distinct_count=len({item.station.id for item in labels.values()}), pareto_front=pareto, dominated=dominated, reason=None if labels else "no_candidates", params_used=request)


@app.post("/api/v1/recommend", response_model=RecommendOut)
def recommend(request: RecommendRequest, user: AuthUser | None = Depends(optional_user)):
    result = recommendation(request)
    if user:
        _save_history(user, request, result)
    return result


@app.post("/api/v1/recommend/compare", response_model=CompareOut)
def compare(request: RecommendRequest):
    rows: list[CompareRow] = []
    for strategy in ("nearest", "cheapest", "current_availability", "historical_average", "point_forecast", "uncertainty_aware"):
        adjusted = request.model_copy(update={"strategy": strategy, "risk_kappa": 0 if strategy == "point_forecast" else request.risk_kappa})
        result = recommendation(adjusted)
        option = result.options.get("BALANCED") or next(iter(result.options.values()), None)
        if option:
            rows.append(CompareRow(strategy=strategy, station=option.station, wait_min=option.wait_min, distance_km=option.travel_km, regret_inr=0, status="Recommended" if strategy == "uncertainty_aware" else "Baseline"))
    best = min((row.wait_min for row in rows), default=0)
    rows = [row.model_copy(update={"regret_inr": round(max(0, row.wait_min - best) * request.value_of_time_per_h / 60, 2)}) for row in rows]
    return CompareOut(data_source=active_data_source(), rows=rows, mean_regret_inr=round(sum(r.regret_inr for r in rows) / max(1, len(rows)), 2), failure_rate=round(sum(row.wait_min > 10 for row in rows) / max(1, len(rows)) * 100, 2))


@app.get("/api/v1/queueing/explain", response_model=QueueExplainOut)
def queue_explain(c: int = Query(..., ge=1, le=100), tau_h: float = Query(..., gt=0, le=4), occupancy: float = Query(..., ge=0, le=1)):
    queue, wait = erlang_c_wait(c, tau_h, c*occupancy)
    return QueueExplainOut(**queue.model_dump(), data_source=active_data_source(), wait_min=wait)


@lru_cache(maxsize=1)
def _evaluation_payload() -> dict:
    artifact = Path(settings.artifacts_dir) / "runs" / "synthetic-replay-42" / "metrics.json"
    if artifact.exists():
        return json.loads(artifact.read_text(encoding="utf-8"))
    return run_synthetic_replay(store, DecisionEngine(store, routing=router, carbon=ScenarioCarbonProvider()), seed=42, steps=12)


@app.get("/api/v1/evaluation/runs")
def evaluation_runs():
    payload = _evaluation_payload()
    return {"runs": [{"id": payload["run_id"], "cities": [c.slug for c in CITIES], "mean_regret_inr": payload["metrics"].get("uncertainty_aware", {}).get("mean_regret", 0), "coverage": payload.get("coverage", None)}]}


@app.get("/api/v1/evaluation/runs/{run_id}")
def evaluation_run(run_id: str):
    payload = _evaluation_payload()
    if run_id != payload["run_id"]:
        raise HTTPException(404, "Evaluation run not found")
    return payload


@app.get("/api/v1/evaluation/runs/{run_id}/regret")
def evaluation_regret(run_id: str):
    payload = _evaluation_payload()
    return {"run_id": run_id, "strategies": {key: {"median": value["median_regret"], "p90": value["p90_regret"], "zero_regret_share": value["zero_regret_share"]} for key, value in payload["metrics"].items()}}


@app.get("/api/v1/evaluation/runs/{run_id}/uncertainty")
def evaluation_uncertainty(run_id: str):
    return {"run_id": run_id, "coverage": None, "mean_interval_width": None, "reliability": [], "note": "Forecast calibration report is produced by the model training pipeline."}


@app.get("/api/v1/evaluation/runs/{run_id}/ablation")
def evaluation_ablation(run_id: str):
    return {"run_id": run_id, "rows": [], "note": "Run the training ablation command to populate this report."}


@app.get("/api/v1/evaluation/runs/{run_id}/sensitivity")
def evaluation_sensitivity(run_id: str):
    return {"run_id": run_id, "rows": [], "note": "Run the replay sensitivity command to populate this report."}


@app.get("/api/v1/evaluation/cross-city")
def cross_city():
    return {"available": False, "reason": "The four-city pilot uses one deterministic Karnataka replay dataset."}


@app.get("/api/v1/insights/utilisation")
def utilisation(from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, city: str | None = None):
    now = datetime.now(timezone.utc)
    station_list = store.summaries(now, city)
    ranking = sorted(station_list, key=lambda s: s.occupancy_rate, reverse=True)
    values = [station.occupancy_rate for station in ranking]
    mean = sum(values) / max(1, len(values))
    gini = sum(abs(a - b) for a in values for b in values) / max(1e-9, 2 * len(values) ** 2 * mean) if values and mean else 0
    return {"from": from_ or now-timedelta(days=30), "to": to or now, "city": city, "load_balance_index": round(1 - gini, 3), "gini": round(gini, 3), "peak_utilisation": max(values, default=0), "stations_analysed": len(ranking), "saturation_share": round(sum(value > .8 for value in values) / max(1, len(values)), 3), "ranking": ranking, "heatmap": [{"weekday": d, "hour": h, "occupancy_rate": round(.25+.4*abs(sin((h+d)/6)), 3)} for d in range(7) for h in range(24)]}


def _profile_id(user: AuthUser) -> UUID:
    try:
        return UUID(user.id)
    except ValueError as exc:
        raise HTTPException(400, {"error": {"code": "invalid_user_id", "message": "Supabase user id must be a UUID.", "details": {}}}) from exc


def _ensure_profile(user: AuthUser) -> UserProfile:
    session = session_factory()()
    try:
        profile = session.get(UserProfile, _profile_id(user))
        if profile is None:
            profile = UserProfile(id=_profile_id(user), email=user.email)
            session.add(profile)
            session.commit()
        return profile
    finally:
        session.close()


def _save_history(user: AuthUser, request: RecommendRequest, response: RecommendOut) -> None:
    session = None
    try:
        _ensure_profile(user)
        session = session_factory()()
        session.add(RecommendationHistory(user_id=_profile_id(user), request_json=request.model_dump(mode="json"), response_json=response.model_dump(mode="json"), data_source=response.data_source))
        session.commit()
    except Exception:
        if session:
            session.rollback()
    finally:
        if session:
            session.close()


@app.get("/api/v1/users/me")
def users_me(user: AuthUser = Depends(current_user)):
    profile = _ensure_profile(user)
    return {"id": str(profile.id), "email": profile.email, "display_name": profile.display_name}


@app.patch("/api/v1/users/me")
def update_users_me(payload: UserProfilePatch, user: AuthUser = Depends(current_user)):
    _ensure_profile(user)
    session = session_factory()()
    try:
        profile = session.get(UserProfile, _profile_id(user))
        if profile and payload.display_name is not None:
            profile.display_name = payload.display_name.strip() or None
        session.commit()
        return {"id": str(profile.id), "email": profile.email, "display_name": profile.display_name}
    finally:
        session.close()


@app.get("/api/v1/users/me/preferences")
def get_preferences(user: AuthUser = Depends(current_user)):
    _ensure_profile(user)
    session = session_factory()()
    try:
        preferences = session.get(UserPreference, _profile_id(user))
        if preferences is None:
            return {"energy_kwh": 20, "radius_km": 25, "risk_kappa": .5, "value_of_time_per_h": 150, "carbon_price_per_kg": .5, "carbon_scenario": "tou_profile"}
        return {key: getattr(preferences, key) for key in ("energy_kwh", "radius_km", "risk_kappa", "value_of_time_per_h", "carbon_price_per_kg", "carbon_scenario", "origin_lat", "origin_lon")}
    finally:
        session.close()


@app.patch("/api/v1/users/me/preferences")
def update_preferences(payload: PreferencesPatch, user: AuthUser = Depends(current_user)):
    _ensure_profile(user)
    session = session_factory()()
    try:
        preferences = session.get(UserPreference, _profile_id(user)) or UserPreference(user_id=_profile_id(user))
        allowed = {"energy_kwh", "radius_km", "risk_kappa", "value_of_time_per_h", "carbon_price_per_kg", "carbon_scenario", "origin_lat", "origin_lon"}
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key in allowed:
                setattr(preferences, key, value)
        session.add(preferences)
        session.commit()
        return {key: getattr(preferences, key) for key in allowed}
    finally:
        session.close()


@app.get("/api/v1/users/me/favourites")
def get_favourites(user: AuthUser = Depends(current_user)):
    session = session_factory()()
    try:
        return {"station_ids": list(session.scalars(select(FavouriteStation.station_id).where(FavouriteStation.user_id == _profile_id(user))))}
    finally:
        session.close()


@app.post("/api/v1/users/me/favourites")
def add_favourite_from_body(payload: FavouriteRequest, user: AuthUser = Depends(current_user)):
    return add_favourite(payload.station_id, user)


@app.post("/api/v1/users/me/favourites/{station_id}")
def add_favourite(station_id: str, user: AuthUser = Depends(current_user)):
    if not store.station(station_id):
        raise HTTPException(404, "Station not found")
    _ensure_profile(user)
    session = session_factory()()
    try:
        session.merge(FavouriteStation(user_id=_profile_id(user), station_id=station_id))
        session.commit()
        return {"station_id": station_id, "saved": True}
    finally:
        session.close()


@app.delete("/api/v1/users/me/favourites/{station_id}")
def remove_favourite(station_id: str, user: AuthUser = Depends(current_user)):
    session = session_factory()()
    try:
        item = session.get(FavouriteStation, {"user_id": _profile_id(user), "station_id": station_id})
        if item:
            session.delete(item)
            session.commit()
        return {"station_id": station_id, "saved": False}
    finally:
        session.close()


@app.delete("/api/v1/users/me/favourites")
def remove_all_favourites(user: AuthUser = Depends(current_user)):
    session = session_factory()()
    try:
        session.query(FavouriteStation).filter(FavouriteStation.user_id == _profile_id(user)).delete(synchronize_session=False)
        session.commit()
        return {"deleted": True}
    finally:
        session.close()


@app.get("/api/v1/users/me/recommendation-history")
def recommendation_history(user: AuthUser = Depends(current_user), limit: int = Query(20, ge=1, le=100)):
    session = session_factory()()
    try:
        rows = session.scalars(select(RecommendationHistory).where(RecommendationHistory.user_id == _profile_id(user)).order_by(RecommendationHistory.created_at.desc()).limit(limit)).all()
        return {"items": [{"id": str(row.id), "created_at": row.created_at, "request": row.request_json, "response": row.response_json} for row in rows]}
    finally:
        session.close()


@app.post("/api/v1/users/me/recommendation-history")
def create_recommendation_history(request: RecommendRequest, user: AuthUser = Depends(current_user)):
    result = recommendation(request)
    _save_history(user, request, result)
    return {"saved": True, "data_source": result.data_source, "recommendation": result}


@app.delete("/api/v1/users/me/recommendation-history/{history_id}")
def delete_recommendation_history(history_id: str, user: AuthUser = Depends(current_user)):
    session = session_factory()()
    try:
        row = session.scalar(select(RecommendationHistory).where(RecommendationHistory.id == history_id, RecommendationHistory.user_id == _profile_id(user)))
        if row is None:
            raise HTTPException(404, "Recommendation history entry not found")
        session.delete(row)
        session.commit()
        return {"deleted": True, "id": history_id}
    finally:
        session.close()


@app.delete("/api/v1/users/me")
def delete_user_data(user: AuthUser = Depends(current_user)):
    session = session_factory()()
    try:
        profile = session.get(UserProfile, _profile_id(user))
        if profile:
            session.delete(profile)
            session.commit()
        return {"deleted": True, "user_id": user.id, "note": "Application data deleted; Supabase identity must be removed through the Auth provider."}
    finally:
        session.close()
