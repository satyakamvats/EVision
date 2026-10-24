from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class Point(BaseModel):
    lat: float = Field(ge=11, le=17)
    lon: float = Field(ge=74, le=79)


class CityOut(BaseModel):
    slug: str
    name: str


class ForecastBand(BaseModel):
    horizon_h: int
    q10: float
    q50: float
    q90: float
    confidence: float
    confidence_label: Literal["high", "medium", "low"]


class StationSummary(BaseModel):
    id: str
    city: str
    name: str
    address: str
    lat: float
    lng: float
    capacity: int
    chargers: int
    power_kw: float
    connectors: list[str]
    price_inr_per_kwh: float
    rating: float
    distance_km: float | None = None
    eta_min: float | None = None
    occupancy_rate: float
    available_count: int
    forecast: ForecastBand
    attribution: str


class StationListOut(BaseModel):
    as_of: datetime
    data_source: Literal["synthetic_karnataka"] = "synthetic_karnataka"
    stations: list[StationSummary]


class StationDetailOut(BaseModel):
    station: StationSummary
    utilisation: dict[str, float]
    nearby: list[StationSummary]


class ForecastPoint(BaseModel):
    at: datetime
    observed: float | None = None
    q10: float | None = None
    q50: float | None = None
    q90: float | None = None


class StationForecastOut(BaseModel):
    station_id: str
    as_of: datetime
    history: list[ForecastPoint]
    forecast: list[ForecastPoint]


class RecommendRequest(BaseModel):
    origin: Point
    as_of: datetime
    energy_kwh: float = Field(default=20, ge=1, le=150)
    radius_km: float = Field(default=25, ge=0.5, le=100)
    max_delay_h: int = Field(default=2, ge=0, le=2)
    risk_kappa: float = Field(default=0.5, ge=0, le=1)
    value_of_time_per_h: float = Field(default=150, ge=0)
    carbon_price_per_kg: float = Field(default=0.5, ge=0)
    carbon_scenario: Literal["flat", "tou_profile", "low", "high"] = "tou_profile"
    strategy: str = "uncertainty_aware"


class QueueOut(BaseModel):
    c: int
    tau_h: float
    lambda_per_h: float
    rho: float
    p_wait: float
    saturated: bool


class RecommendationOption(BaseModel):
    candidate_id: str
    station: StationSummary
    delay_min: int
    travel_km: float
    travel_min: float
    forecast: ForecastBand
    wait_min: float
    wait_interval_min: tuple[float, float]
    total_time_min: float
    price_total_inr: float
    carbon_kg: float
    generalised_cost_inr: float
    queue: QueueOut
    cost_terms: dict[str, float]
    explanation: str


class RecommendOut(BaseModel):
    as_of: datetime
    data_source: Literal["synthetic_karnataka"] = "synthetic_karnataka"
    model_version: str
    carbon_source: Literal["scenario", "observed"] = "scenario"
    options: dict[str, RecommendationOption]
    distinct_count: int
    pareto_front: list[dict[str, float | str]]
    params_used: RecommendRequest


class RevealOut(BaseModel):
    station_id: str
    as_of: datetime
    observed_occupancy_rate: float
    realised_wait_min: float
    realised_regret_inr: float
    evaluation_only: bool = True


class CompareRow(BaseModel):
    strategy: str
    station: StationSummary
    wait_min: float
    distance_km: float
    regret_inr: float
    status: str


class CompareOut(BaseModel):
    rows: list[CompareRow]
    mean_regret_inr: float
    failure_rate: float


class QueueExplainOut(QueueOut):
    wait_min: float
