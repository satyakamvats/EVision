from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, exp, pi, sin, sqrt
import json
from pathlib import Path
import random
from .schemas import ForecastBand, QueueOut, StationSummary


@dataclass(frozen=True)
class CitySeed:
    slug: str
    name: str
    lat: float
    lng: float


CITIES = [
    CitySeed("bengaluru", "Bengaluru", 12.9716, 77.5946),
    CitySeed("mysuru", "Mysuru", 12.2958, 76.6394),
    CitySeed("hubballi-dharwad", "Hubballi–Dharwad", 15.3647, 75.1240),
    CitySeed("belagavi", "Belagavi", 15.8497, 74.4977),
]


class ReplayStore:
    model_version = "synthetic-karnataka-v1"

    def __init__(self, seed: int = 20261024):
        self.seed = seed
        self.stations = self._make_stations()

    def _make_stations(self) -> list[dict]:
        stations: list[dict] = []
        names = ["ChargeGrid", "Ather Grid", "Voltway", "EVision Hub", "GreenCharge", "RapidPlug"]
        for city_index, city in enumerate(CITIES):
            rng = random.Random(self.seed + city_index)
            for index in range(8):
                capacity = rng.choice([4, 6, 8, 10, 12])
                stations.append({
                    "id": f"{city.slug}-{index + 1:02d}", "city": city.slug,
                    "name": f"{names[index % len(names)]} {city.name} {index + 1}",
                    "address": f"{city.name}, Karnataka", "lat": city.lat + rng.uniform(-0.045, 0.045),
                    "lng": city.lng + rng.uniform(-0.05, 0.05), "capacity": capacity,
                    "chargers": capacity, "power_kw": float(rng.choice([7.2, 22, 60, 120, 150])),
                    "connectors": ["CCS2", "Type 2"] if index % 3 else ["CCS2", "CHAdeMO"],
                    "price": round(rng.uniform(12, 22), 2), "rating": round(rng.uniform(4.1, 4.9), 1),
                    "attribution": "Location metadata © Open Charge Map contributors; operational values are synthetic.",
                })
        return stations

    @staticmethod
    def _haversine(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
        r = 6371.0
        dlat, dlng = pi / 180 * (b_lat - a_lat), pi / 180 * (b_lng - a_lng)
        q = sin(dlat / 2) ** 2 + cos(a_lat * pi / 180) * cos(b_lat * pi / 180) * sin(dlng / 2) ** 2
        return 2 * r * asin(sqrt(q))

    def occupancy(self, station: dict, at: datetime) -> float:
        hour = at.astimezone(timezone.utc).hour
        weekday = at.weekday()
        station_hash = sum(ord(c) for c in station["id"])
        commute = 0.20 * exp(-((hour - 9) / 3) ** 2) + 0.25 * exp(-((hour - 19) / 3) ** 2)
        weekday_effect = 0.08 if weekday < 5 else -0.03
        wave = 0.08 * sin((at.timestamp() / 3600 + station_hash) * 0.37)
        return min(0.96, max(0.05, 0.30 + commute + weekday_effect + wave + (station_hash % 17) / 100))

    def forecast(self, station: dict, as_of: datetime, horizon_h: int = 1) -> ForecastBand:
        q50 = self.occupancy(station, as_of + timedelta(hours=horizon_h))
        width = min(0.34, 0.12 + horizon_h * 0.035)
        confidence = 1 - width
        return ForecastBand(horizon_h=horizon_h, q10=round(max(0, q50 - width / 2), 3), q50=round(q50, 3), q90=round(min(1, q50 + width / 2), 3), confidence=round(confidence, 3), confidence_label="high" if confidence >= .8 else "medium" if confidence >= .65 else "low")

    def summaries(self, as_of: datetime, city: str | None = None, origin: tuple[float, float] | None = None, radius_km: float | None = None, horizon_h: int = 1) -> list[StationSummary]:
        result = []
        for station in self.stations:
            if city and station["city"] != city:
                continue
            distance = self._haversine(*origin, station["lat"], station["lng"]) if origin else None
            if radius_km and distance is not None and distance > radius_km:
                continue
            current = self.occupancy(station, as_of)
            result.append(StationSummary(id=station["id"], city=station["city"], name=station["name"], address=station["address"], lat=station["lat"], lng=station["lng"], capacity=station["capacity"], chargers=station["chargers"], power_kw=station["power_kw"], connectors=station["connectors"], price_inr_per_kwh=station["price"], rating=station["rating"], distance_km=round(distance, 2) if distance is not None else None, eta_min=round(distance / 30 * 60, 1) if distance is not None else None, occupancy_rate=round(current, 3), available_count=max(0, round((1-current)*station["capacity"])), forecast=self.forecast(station, as_of, horizon_h), attribution=station["attribution"]))
        return sorted(result, key=lambda item: item.distance_km if item.distance_km is not None else item.name)

    def station(self, station_id: str) -> dict | None:
        return next((s for s in self.stations if s["id"] == station_id), None)


class ScenarioReplayStore(ReplayStore):
    """Replay-compatible store backed by the four-city scenario artifact."""
    model_version = "karnataka-scenario-reference-v1"
    data_source = "scenario_estimate"

    def __init__(self, root: str | Path):
        self.seed = 0
        folder = Path(root)
        rows = json.loads((folder / "stations.json").read_text(encoding="utf-8"))
        self.stations = [{"id": row["station_id"], "city": row["city"], "name": row["name"], "address": row.get("address") or row["city"], "lat": row["lat"], "lng": row["lon"], "capacity": row["capacity"], "chargers": row["capacity"], "power_kw": row.get("power_kw") or 22.0, "connectors": row.get("connectors") or ["Unknown"], "price": row.get("price", 16.0), "rating": 4.5, "attribution": row.get("attribution", "Open Charge Map station metadata; occupancy is a reference-calibrated scenario estimate.")} for row in rows]
        self._observations: dict[str, list[dict]] = {}
        self.forecast_provider = None
        with (folder / "observations.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                row["_dt"] = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                self._observations.setdefault(row["station_id"], []).append(row)

    def set_forecast_provider(self, provider) -> None:
        self.forecast_provider = provider
        self.model_version = provider.version

    def occupancy(self, station: dict, at: datetime) -> float:
        rows = self._observations.get(station["id"], [])
        if not rows:
            return super().occupancy(station, at)
        target_hour = at.astimezone(timezone.utc).hour
        matches = [row for row in rows if datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")).hour == target_hour]
        row = matches[0] if matches else rows[0]
        return float(row["occupancy_rate"])

    def forecast(self, station: dict, as_of: datetime, horizon_h: int = 1) -> ForecastBand:
        if self.forecast_provider is not None:
            return self.forecast_provider.forecast(station, self._observations.get(station["id"], []), as_of, horizon_h)
        q50 = self.occupancy(station, as_of + timedelta(hours=horizon_h))
        width = min(0.34, 0.14 + horizon_h * 0.04)
        confidence = 1 - width
        return ForecastBand(horizon_h=horizon_h, q10=round(max(0, q50 - width / 2), 3), q50=round(q50, 3), q90=round(min(1, q50 + width / 2), 3), confidence=round(confidence, 3), confidence_label="high" if confidence >= .8 else "medium" if confidence >= .65 else "low")


def erlang_c(capacity: int, tau_h: float, occupied: float) -> tuple[QueueOut, float]:
    offered = max(0.0, min(float(occupied), capacity * .98))
    rho = offered / max(1, capacity)
    if offered == 0:
        return QueueOut(c=capacity, tau_h=tau_h, lambda_per_h=0, rho=0, p_wait=0, saturated=False), 0.0
    term, total = 1.0, 1.0
    for k in range(1, capacity):
        term *= offered / k
        total += term
    tail = term * offered / capacity / max(1e-9, 1 - rho)
    p_wait = tail / (total + tail)
    wait = min(60.0, 60 * p_wait * tau_h / max(1e-9, capacity - offered))
    return QueueOut(c=capacity, tau_h=tau_h, lambda_per_h=round(offered / tau_h, 3), rho=round(rho, 3), p_wait=round(p_wait, 3), saturated=offered >= capacity * .98), round(wait, 2)
