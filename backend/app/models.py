from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from geoalchemy2 import Geography


class Base(DeclarativeBase):
    pass


class City(Base):
    __tablename__ = "cities"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[dict] = mapped_column(JSONB, default=dict)
    boundary_geojson: Mapped[dict] = mapped_column(JSONB)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(128))
    source_hash: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[UUID] = mapped_column(ForeignKey("cities.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    address: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(String(256))
    capacity: Mapped[int] = mapped_column(Integer)
    rated_power_kw: Mapped[float | None] = mapped_column(Float)
    location: Mapped[object] = mapped_column(Geography("POINT", srid=4326, spatial_index=True))
    provider: Mapped[str] = mapped_column(String(128), default="Open Charge Map")
    attribution: Mapped[str] = mapped_column(Text)
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class StationConnector(Base):
    __tablename__ = "station_connectors"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    connector_type: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    power_kw: Mapped[float | None] = mapped_column(Float)


class StationObservation(Base):
    __tablename__ = "station_observations"
    __table_args__ = (UniqueConstraint("station_id", "observed_at", name="uq_station_observed_at"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    occupied_count: Mapped[int] = mapped_column(Integer)
    occupancy_rate: Mapped[float] = mapped_column(Float)
    duration_h: Mapped[float] = mapped_column(Float)
    volume_kwh: Mapped[float] = mapped_column(Float)
    electricity_price_inr: Mapped[float] = mapped_column(Float)
    service_price_inr: Mapped[float] = mapped_column(Float)


class WeatherObservation(Base):
    __tablename__ = "weather_observations"
    __table_args__ = (UniqueConstraint("city_id", "observed_at", name="uq_city_weather_at"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[UUID] = mapped_column(ForeignKey("cities.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    temperature_c: Mapped[float] = mapped_column(Float)
    precipitation_mm: Mapped[float] = mapped_column(Float, default=0)
    humidity_pct: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64), default="synthetic")


class StationEdge(Base):
    __tablename__ = "station_edges"
    __table_args__ = (UniqueConstraint("station_id", "neighbour_station_id", name="uq_station_neighbour"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    neighbour_station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    distance_km: Mapped[float] = mapped_column(Float)


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (UniqueConstraint("station_id", "as_of", "horizon_h", "model_version", name="uq_forecast_version"),)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    station_id: Mapped[UUID] = mapped_column(ForeignKey("stations.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    horizon_h: Mapped[int] = mapped_column(Integer)
    q10: Mapped[float] = mapped_column(Float)
    q50: Mapped[float] = mapped_column(Float)
    q90: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(128))


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    energy_kwh: Mapped[float] = mapped_column(Float, default=20)
    radius_km: Mapped[float] = mapped_column(Float, default=25)
    risk_kappa: Mapped[float] = mapped_column(Float, default=.5)
    value_of_time_per_h: Mapped[float] = mapped_column(Float, default=150)
    carbon_price_per_kg: Mapped[float] = mapped_column(Float, default=.5)
    carbon_scenario: Mapped[str] = mapped_column(String(32), default="tou_profile")
    origin_lat: Mapped[float | None] = mapped_column(Float)
    origin_lon: Mapped[float | None] = mapped_column(Float)


class FavouriteStation(Base):
    __tablename__ = "favourite_stations"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationHistory(Base):
    __tablename__ = "recommendation_history"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    response_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_source: Mapped[str] = mapped_column(String(32), default="synthetic")


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    model_version: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    model_type: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(128))
    data_hash: Mapped[str] = mapped_column(String(128))
    split_hash: Mapped[str] = mapped_column(String(128))
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    library_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationAblation(Base):
    __tablename__ = "evaluation_ablations"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(128))
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class EvaluationSensitivity(Base):
    __tablename__ = "evaluation_sensitivities"
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True)
    parameter: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict)
