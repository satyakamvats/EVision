from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite
from typing import Literal
from zoneinfo import ZoneInfo

from .carbon import CarbonProvider, ScenarioCarbonProvider
from .replay import ReplayStore
from .routing import FallbackRoutingProvider, RouteResult, RoutingProvider
from .schemas import ForecastBand, QueueOut, RecommendationOption, RecommendRequest, StationSummary


@dataclass(frozen=True)
class Candidate:
    station: StationSummary
    delay_h: int
    route: RouteResult
    forecast: ForecastBand


def erlang_c_wait(capacity: int, tau_h: float, occupied: float) -> tuple[QueueOut, float]:
    """Return stable Erlang-C queue values for an M/M/c system."""
    c = max(1, int(capacity))
    tau = max(.25, min(3.0, float(tau_h)))
    offered = max(0.0, min(float(occupied), c * .98))
    rho = offered / c
    if offered == 0:
        return QueueOut(c=c, tau_h=tau, lambda_per_h=0, rho=0, p_wait=0, saturated=False), 0.0
    term = 1.0
    total = term
    for k in range(1, c):
        term *= offered / k
        total += term
    tail = term * offered / c / max(1e-12, 1 - rho)
    p_wait = max(0.0, min(1.0, tail / (total + tail)))
    wait_min = min(60.0, 60.0 * p_wait * tau / max(1e-9, c - offered))
    return QueueOut(c=c, tau_h=round(tau, 3), lambda_per_h=round(offered / tau, 3), rho=round(rho, 3), p_wait=round(p_wait, 3), saturated=occupied >= c * .98), round(wait_min, 2)


def confidence_label(confidence: float) -> Literal["high", "medium", "low"]:
    return "high" if confidence >= .75 else "medium" if confidence >= .5 else "low"


class DecisionEngine:
    def __init__(self, store: ReplayStore, routing: RoutingProvider | None = None, carbon: CarbonProvider | None = None):
        self.store = store
        self.routing = FallbackRoutingProvider(routing)
        self.carbon = carbon or ScenarioCarbonProvider()

    def _forecast(self, station: dict, as_of: datetime, horizon: int) -> ForecastBand:
        band = self.store.forecast(station, as_of, max(0, min(3, horizon)))
        confidence = max(0.0, min(1.0, 1 - (band.q90 - band.q10)))
        return ForecastBand(horizon_h=horizon, q10=band.q10, q50=band.q50, q90=band.q90, confidence=round(confidence, 3), confidence_label=confidence_label(confidence))

    @staticmethod
    def _time_aware_route(route: RouteResult, station: dict, request: RecommendRequest, delay_h: int) -> RouteResult:
        """Apply reproducible scenario congestion to routed travel time.

        OSRM supplies geography and a baseline route, not live traffic.  The
        local scenario therefore adds a deterministic time-of-day congestion
        estimate so the recommendation can change as the forecast horizon
        moves.  The route remains labelled as OSRM/fallback in the response.
        """
        at = request.as_of + timedelta(hours=max(delay_h, request.forecast_horizon_h))
        local_hour = at.astimezone(ZoneInfo("Asia/Kolkata")).hour
        peak = max(0.0, 1.0 - abs(local_hour - 9) / 3.0) if 6 <= local_hour <= 12 else max(0.0, 1.0 - abs(local_hour - 18) / 4.0) if 14 <= local_hour <= 22 else 0.0
        station_variation = (sum(ord(char) for char in station["id"]) % 7 - 3) / 100
        corridor_wave = ((sum(ord(char) for char in station["id"]) % 11) - 5) / 100
        factor = max(.85, 1.0 + peak * (0.16 + station_variation) + corridor_wave * peak)
        return RouteResult(route.distance_km, round(route.duration_min * factor, 1), route.source, route.geometry)

    def candidates(self, request: RecommendRequest) -> list[Candidate]:
        summaries = self.store.summaries(request.as_of, origin=(request.origin.lat, request.origin.lon), radius_km=request.radius_km)
        result: list[Candidate] = []
        # Route a bounded nearest-candidate set.  This keeps interactive
        # origin changes responsive when the configured provider is public
        # OSRM, while preserving the documented top-k candidate behaviour.
        for summary in summaries[:12]:
            raw = self.store.station(summary.id)
            if raw is None:
                continue
            base_route = self.routing.route((request.origin.lat, request.origin.lon), (raw["lat"], raw["lng"]))
            for delay_h in range(request.max_delay_h + 1):
                route = self._time_aware_route(base_route, raw, request, delay_h)
                travel_horizon = ceil(route.duration_min / 60)
                horizon = max(1, min(3, request.forecast_horizon_h or travel_horizon))
                if delay_h + travel_horizon > 3:
                    continue
                forecast_at = request.as_of + timedelta(hours=max(delay_h, request.forecast_horizon_h))
                result.append(Candidate(summary, delay_h, route, self._forecast(raw, forecast_at, horizon)))
        return result

    def _option(self, candidate: Candidate, request: RecommendRequest, intensity: float, nearest: Candidate | None) -> RecommendationOption:
        station = candidate.station
        raw = self.store.station(station.id)
        assert raw is not None
        forecast = candidate.forecast
        occupancy = forecast.q50 + request.risk_kappa * (forecast.q90 - forecast.q50)
        queue, wait = erlang_c_wait(station.capacity, .75, occupancy * station.capacity)
        _, low_wait = erlang_c_wait(station.capacity, .75, forecast.q10 * station.capacity)
        _, high_wait = erlang_c_wait(station.capacity, .75, forecast.q90 * station.capacity)
        travel = candidate.route.duration_min
        total = travel + candidate.delay_h * 60 + wait
        # Scenario tariffs vary by station and time-of-use period.  This is
        # an estimate, not a live tariff feed, and is surfaced as such by the
        # response's scenario data label.
        hour = request.as_of.astimezone().hour
        time_factor = 1.12 if 18 <= hour <= 22 else .92 if 0 <= hour < 6 else 1.0
        tariff_variation = 1 + (((sum(ord(char) for char in station.id) % 5) - 2) / 100) * (1 if time_factor != 1 else .35)
        price = request.energy_kwh * station.price_inr_per_kwh * time_factor * tariff_variation
        carbon_kg = round(((request.energy_kwh + candidate.route.distance_km * .18) * intensity / 1000) + wait * .004, 2)
        terms = {"time": round(request.value_of_time_per_h * total / 60, 2), "price": round(price, 2), "carbon": round(request.carbon_price_per_kg * carbon_kg, 2)}
        explanation = f"Forecast occupancy is {forecast.q50:.0%} with {forecast.confidence_label} confidence; the route is {candidate.route.source.replace('_', ' ')}."
        if queue.saturated:
            explanation += " The station is near its saturation guard."
        return RecommendationOption(candidate_id=f"{station.id}@{candidate.delay_h}", station=station, delay_min=candidate.delay_h * 60, travel_km=round(candidate.route.distance_km, 2), travel_min=travel, forecast=forecast, wait_min=wait, wait_interval_min=(low_wait, high_wait), total_time_min=round(total, 2), price_total_inr=round(price, 2), carbon_kg=carbon_kg, generalised_cost_inr=round(sum(terms.values()), 2), queue=queue, cost_terms=terms, explanation=explanation, route_source=candidate.route.source)

    @staticmethod
    def _dominates(left: RecommendationOption, right: RecommendationOption) -> bool:
        left_values = (left.total_time_min, left.price_total_inr, left.carbon_kg)
        right_values = (right.total_time_min, right.price_total_inr, right.carbon_kg)
        return all(a <= b for a, b in zip(left_values, right_values)) and any(a < b for a, b in zip(left_values, right_values))

    def recommend(self, request: RecommendRequest) -> tuple[list[RecommendationOption], list[RecommendationOption]]:
        candidates = self.candidates(request)
        if not candidates:
            return [], []
        carbon = self.carbon.intensity(request.as_of, request.origin.lat, request.origin.lon, request.carbon_scenario)
        nearest = candidates[0]
        options = [self._option(item, request, carbon.intensity_g_per_kwh, nearest) for item in candidates]
        front = [item for item in options if not any(self._dominates(other, item) for other in options)]
        front.sort(key=lambda item: (item.total_time_min, item.station.id, item.delay_min))
        return front, [item for item in options if item not in front]

    def labels(self, request: RecommendRequest) -> tuple[dict[str, RecommendationOption], list[dict[str, float | str]], list[dict[str, float | str]]]:
        front, dominated = self.recommend(request)
        if not front:
            return {}, [], []
        all_options = front + dominated
        # Prefer immediate departures for the three visible cards.  Delayed
        # candidates remain available to the strategy/evaluation APIs, but a
        # driver-facing alternative should not unexpectedly become a 2-hour
        # arrival merely to make the station names different.
        visible_options = [item for item in all_options if item.delay_min == 0] or all_options
        fastest_ranked = sorted(visible_options, key=lambda item: (item.total_time_min, item.station.id, item.delay_min))
        balanced_ranked = sorted(visible_options, key=lambda item: (item.generalised_cost_inr, item.total_time_min, item.station.id, item.delay_min))
        greenest_ranked = sorted(visible_options, key=lambda item: (item.carbon_kg, item.total_time_min, item.station.id, item.delay_min))

        # Each card is a driver-facing alternative.  Keep each strategy's
        # metric ordering, but avoid rendering the same station three times
        # when enough candidates exist.  If only one station is available,
        # duplication remains the honest result.
        used_stations: set[str] = set()

        def distinct_pick(ranked: list[RecommendationOption]) -> RecommendationOption:
            choice = next((item for item in ranked if item.station.id not in used_stations), ranked[0])
            used_stations.add(choice.station.id)
            return choice

        fastest = distinct_pick(fastest_ranked)
        balanced = distinct_pick(balanced_ranked)
        greenest = distinct_pick(greenest_ranked)

        if request.strategy == "nearest":
            balanced = min(all_options, key=lambda item: (item.travel_km, item.delay_min, item.station.id))
        elif request.strategy == "cheapest":
            balanced = min(all_options, key=lambda item: (item.price_total_inr, item.total_time_min, item.station.id))
        elif request.strategy == "current_availability":
            balanced = min(all_options, key=lambda item: (item.station.occupancy_rate, item.total_time_min, item.station.id))
        elif request.strategy == "historical_average":
            balanced = min(all_options, key=lambda item: (item.forecast.q50, item.total_time_min, item.station.id))
        elif request.strategy == "point_forecast":
            balanced = min(all_options, key=lambda item: (item.generalised_cost_inr, item.total_time_min, item.station.id))
        def point(item: RecommendationOption) -> dict[str, float | str]:
            return {"candidate_id": item.candidate_id, "station_id": item.station.id, "total_time_min": item.total_time_min, "price_total_inr": item.price_total_inr, "carbon_kg": item.carbon_kg}
        return {"FASTEST": fastest, "BALANCED": balanced, "GREENEST": greenest}, [point(item) for item in front[:30]], [point(item) for item in dominated[:50]]
