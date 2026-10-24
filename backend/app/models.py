from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
