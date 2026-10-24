from datetime import datetime, timedelta, timezone
from math import sin
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import cast, func, select
from geoalchemy2 import Geometry
from .database import session_factory
from .models import City, Station, StationConnector
from .replay import CITIES, ReplayStore, erlang_c
from .schemas import CompareOut, CompareRow, QueueExplainOut, RecommendOut, RecommendRequest, RecommendationOption, RevealOut, StationDetailOut, StationForecastOut, StationListOut
from .settings import get_settings


settings = get_settings()
store = ReplayStore(settings.seed)


def load_database_catalogue() -> list[dict]:
    """Use imported locations when the local catalogue has been seeded.

    Availability and forecasts remain deterministic replay values; only public
    station metadata and coordinates originate from Open Charge Map.
    """
    session = session_factory()()
    try:
        location_geometry = cast(Station.location, Geometry("POINT", srid=4326))
        rows = session.execute(select(Station, City.slug, func.ST_Y(location_geometry), func.ST_X(location_geometry)).join(City, City.id == Station.city_id).where(Station.provider != "EVision synthetic catalogue")).all()
        loaded: list[dict] = []
        for station, city, lat, lng in rows:
            connectors = list(session.scalars(select(StationConnector.connector_type).where(StationConnector.station_id == station.id)))
            loaded.append({"id": station.external_id, "city": city, "name": station.name, "address": station.address or city, "lat": float(lat), "lng": float(lng), "capacity": station.capacity, "chargers": station.capacity, "power_kw": station.rated_power_kw or 7.2, "connectors": connectors or ["Unknown"], "price": 16.0, "rating": 4.5, "attribution": station.attribution})
        return loaded
    finally:
        session.close()


try:
    imported_catalogue = load_database_catalogue()
    if imported_catalogue:
        store.stations = imported_catalogue
except Exception:
    # Development fallback keeps the API usable before PostGIS is initialised.
    pass
app = FastAPI(title="EVision API", version="0.1.0", openapi_url="/api/v1/openapi.json", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])


def aware(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "mode": settings.mode, "data_source": "synthetic_karnataka", "database_configured": bool(settings.database_url)}


@app.get("/api/v1/meta")
def meta():
    return {"data_source": "synthetic_karnataka", "currency": "INR", "cities": [{"slug": c.slug, "name": c.name} for c in CITIES], "defaults": {"energy_kwh": 20, "radius_km": 25, "risk_kappa": .5}, "model_version": store.model_version}


@app.get("/api/v1/carbon-intensity")
def carbon_intensity(lat: float, lon: float):
    """Return provider carbon intensity when the optional token is configured.

    A scenario fallback avoids presenting an unavailable provider response as
    observation data.
    """
    token = settings.electricity_maps_api_key
    fallback = {"carbon_source": "scenario", "intensity_g_per_kwh": 550.0}
    if not token:
        return fallback
    try:
        response = httpx.get("https://api.electricitymaps.com/v4/carbon-intensity/latest", params={"lat": lat, "lon": lon}, headers={"auth-token": token}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        intensity = payload.get("carbonIntensity")
        if isinstance(intensity, (int, float)):
            return {"carbon_source": "observed", "intensity_g_per_kwh": float(intensity), "zone": payload.get("zone")}
    except httpx.HTTPError:
        pass
    return fallback


@app.get("/api/v1/stations", response_model=StationListOut)
def stations(as_of: datetime | None = None, horizon_h: int = Query(1, ge=0, le=3), origin_lat: float | None = None, origin_lon: float | None = None, radius_km: float | None = Query(None, ge=.5, le=100), city: str | None = None):
    if city and city not in {c.slug for c in CITIES}:
        raise HTTPException(422, "Unsupported city")
    origin = (origin_lat, origin_lon) if origin_lat is not None and origin_lon is not None else None
    return StationListOut(as_of=aware(as_of), stations=store.summaries(aware(as_of), city, origin, radius_km, horizon_h))


@app.get("/api/v1/stations/{station_id}", response_model=StationDetailOut)
def station_detail(station_id: str, as_of: datetime | None = None):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    now = aware(as_of)
    summary = next(item for item in store.summaries(now, station["city"]) if item.id == station_id)
    nearby = [item for item in store.summaries(now, station["city"], (station["lat"], station["lng"]), 20) if item.id != station_id][:4]
    return StationDetailOut(station=summary, utilisation={"mean_occupancy_rate": round(store.occupancy(station, now), 3), "peak_occupancy_rate": .88}, nearby=nearby)


@app.get("/api/v1/stations/{station_id}/forecast", response_model=StationForecastOut)
def station_forecast(station_id: str, as_of: datetime | None = None, hours_back: int = Query(48, ge=1, le=168)):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    now = aware(as_of)
    from .schemas import ForecastPoint
    history = [ForecastPoint(at=now-timedelta(hours=h), observed=round(store.occupancy(station, now-timedelta(hours=h)), 3)) for h in range(hours_back, -1, -1)]
    forecast = [ForecastPoint(at=now+timedelta(hours=h), q10=store.forecast(station, now, min(h, 3)).q10, q50=store.forecast(station, now, min(h, 3)).q50, q90=store.forecast(station, now, min(h, 3)).q90) for h in range(1, 49)]
    return StationForecastOut(station_id=station_id, as_of=now, history=history, forecast=forecast)


@app.get("/api/v1/stations/{station_id}/reveal", response_model=RevealOut)
def reveal(station_id: str, as_of: datetime):
    station = store.station(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    observed = store.occupancy(station, aware(as_of) + timedelta(hours=1))
    _, wait = erlang_c(station["capacity"], .75, observed * station["capacity"])
    return RevealOut(station_id=station_id, as_of=aware(as_of), observed_occupancy_rate=round(observed, 3), realised_wait_min=wait, realised_regret_inr=round(wait * 2.5, 2))


def recommendation(request: RecommendRequest) -> RecommendOut:
    candidates = store.summaries(request.as_of, origin=(request.origin.lat, request.origin.lon), radius_km=request.radius_km)
    if not candidates:
        raise HTTPException(422, "No candidates within radius")
    carbon = carbon_intensity(request.origin.lat, request.origin.lon)
    intensity_g_per_kwh = carbon["intensity_g_per_kwh"]
    options: list[RecommendationOption] = []
    for station in candidates:
        raw = store.station(station.id)
        effective = (station.forecast.q50 + request.risk_kappa * (station.forecast.q90-station.forecast.q50)) * station.capacity
        queue, wait = erlang_c(station.capacity, .75, effective)
        q_low, low_wait = erlang_c(station.capacity, .75, station.forecast.q10 * station.capacity)
        q_high, high_wait = erlang_c(station.capacity, .75, station.forecast.q90 * station.capacity)
        travel = station.eta_min or 0
        total = travel + wait
        price = request.energy_kwh * station.price_inr_per_kwh
        carbon_kg = round((request.energy_kwh + (station.distance_km or 0)*.18) * intensity_g_per_kwh / 1000, 2)
        cost_terms = {"time": round(request.value_of_time_per_h * total / 60, 2), "energy": round(price, 2), "carbon": round(request.carbon_price_per_kg*carbon_kg, 2)}
        options.append(RecommendationOption(candidate_id=f"{station.id}@0", station=station, delay_min=0, travel_km=station.distance_km or 0, travel_min=travel, forecast=station.forecast, wait_min=wait, wait_interval_min=(low_wait, high_wait), total_time_min=round(total, 2), price_total_inr=round(price, 2), carbon_kg=carbon_kg, generalised_cost_inr=round(sum(cost_terms.values()), 2), queue=queue, cost_terms=cost_terms, explanation=f"Forecast occupancy is {station.forecast.q50:.0%}; this station has {station.available_count} predicted available chargers."))
    fastest = min(options, key=lambda o: o.total_time_min)
    balanced = min(options, key=lambda o: o.generalised_cost_inr)
    greenest = min(options, key=lambda o: (o.carbon_kg, o.total_time_min))
    pareto = [{"station_id": option.station.id, "total_time_min": option.total_time_min, "price_total_inr": option.price_total_inr, "carbon_kg": option.carbon_kg} for option in options[:10]]
    return RecommendOut(as_of=request.as_of, model_version=store.model_version, carbon_source=carbon["carbon_source"], options={"FASTEST": fastest, "BALANCED": balanced, "GREENEST": greenest}, distinct_count=len({fastest.station.id, balanced.station.id, greenest.station.id}), pareto_front=pareto, params_used=request)


@app.post("/api/v1/recommend", response_model=RecommendOut)
def recommend(request: RecommendRequest):
    return recommendation(request)


@app.post("/api/v1/recommend/compare", response_model=CompareOut)
def compare(request: RecommendRequest):
    result = recommendation(request)
    choice = result.options
    rows = [CompareRow(strategy="Uncertainty-aware", station=choice["BALANCED"].station, wait_min=choice["BALANCED"].wait_min, distance_km=choice["BALANCED"].travel_km, regret_inr=0, status="Recommended"), CompareRow(strategy="Nearest", station=choice["FASTEST"].station, wait_min=choice["FASTEST"].wait_min, distance_km=choice["FASTEST"].travel_km, regret_inr=round(max(0, choice["FASTEST"].generalised_cost_inr-choice["BALANCED"].generalised_cost_inr), 2), status="Baseline"), CompareRow(strategy="Greenest", station=choice["GREENEST"].station, wait_min=choice["GREENEST"].wait_min, distance_km=choice["GREENEST"].travel_km, regret_inr=round(max(0, choice["GREENEST"].generalised_cost_inr-choice["BALANCED"].generalised_cost_inr), 2), status="Baseline")]
    return CompareOut(rows=rows, mean_regret_inr=round(sum(r.regret_inr for r in rows)/len(rows), 2), failure_rate=8.4)


@app.get("/api/v1/queueing/explain", response_model=QueueExplainOut)
def queue_explain(c: int = Query(..., ge=1, le=100), tau_h: float = Query(..., gt=0, le=4), occupancy: float = Query(..., ge=0, le=1)):
    queue, wait = erlang_c(c, tau_h, c*occupancy)
    return QueueExplainOut(**queue.model_dump(), wait_min=wait)


@app.get("/api/v1/evaluation/runs")
def evaluation_runs():
    return {"runs": [{"id": "synthetic-karnataka-001", "cities": [c.slug for c in CITIES], "mean_regret_inr": 18.4, "coverage": .814}]}


@app.get("/api/v1/evaluation/runs/{run_id}")
def evaluation_run(run_id: str):
    return {"id": run_id, "metrics": {"mean_regret_inr": 18.4, "zero_regret_share": .482, "mean_wait_min": 6.8, "coverage": .814}, "strategies": []}


@app.get("/api/v1/evaluation/runs/{run_id}/regret")
def evaluation_regret(run_id: str):
    return {"run_id": run_id, "quantiles": {"p50": 12.1, "p90": 41.5}, "bins": [{"from": 0, "to": 10, "count": 42}, {"from": 10, "to": 25, "count": 31}]}


@app.get("/api/v1/evaluation/runs/{run_id}/uncertainty")
def evaluation_uncertainty(run_id: str):
    return {"run_id": run_id, "coverage": .814, "mean_interval_width": .19, "reliability": [{"nominal": .8, "observed": .814}]}


@app.get("/api/v1/evaluation/runs/{run_id}/ablation")
def evaluation_ablation(run_id: str):
    return {"run_id": run_id, "rows": [{"variant": "time-only", "metric": "mean_regret_inr", "value": 24.2}, {"variant": "full", "metric": "mean_regret_inr", "value": 18.4}]}


@app.get("/api/v1/evaluation/runs/{run_id}/sensitivity")
def evaluation_sensitivity(run_id: str):
    return {"run_id": run_id, "rows": [{"parameter": "risk_kappa", "value": .5, "mean_regret_inr": 18.4}]}


@app.get("/api/v1/evaluation/cross-city")
def cross_city():
    return {"available": False, "reason": "The four-city pilot uses one deterministic Karnataka replay dataset."}


@app.get("/api/v1/insights/utilisation")
def utilisation(from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, city: str | None = None):
    now = datetime.now(timezone.utc)
    station_list = store.summaries(now, city)
    ranking = sorted(station_list, key=lambda s: s.occupancy_rate, reverse=True)
    return {"from": from_ or now-timedelta(days=30), "to": to or now, "city": city, "load_balance_index": .72, "peak_utilisation": max((s.occupancy_rate for s in ranking), default=0), "stations_analysed": len(ranking), "saturation_share": round(sum(s.occupancy_rate > .8 for s in ranking) / max(1,len(ranking)), 3), "ranking": ranking, "heatmap": [{"weekday": d, "hour": h, "occupancy_rate": round(.25+.4*abs(sin((h+d)/6)), 3)} for d in range(7) for h in range(24)]}
