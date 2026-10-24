"""initial EVision PostGIS schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-24
"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table("cities", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("slug", sa.String(64), nullable=False, unique=True), sa.Column("name", sa.String(128), nullable=False), sa.Column("aliases", postgresql.JSONB(), nullable=False), sa.Column("boundary_geojson", postgresql.JSONB(), nullable=False))
    op.create_table("ingestion_runs", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("kind", sa.String(32), nullable=False), sa.Column("source", sa.String(128), nullable=False), sa.Column("source_hash", sa.String(128), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("metadata_json", postgresql.JSONB(), nullable=False))
    op.create_table("stations", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("city_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cities.id"), nullable=False), sa.Column("external_id", sa.String(128), nullable=False, unique=True), sa.Column("name", sa.String(256), nullable=False), sa.Column("address", sa.Text()), sa.Column("operator", sa.String(256)), sa.Column("capacity", sa.Integer(), nullable=False), sa.Column("rated_power_kw", sa.Float()), sa.Column("location", Geography("POINT", srid=4326), nullable=False), sa.Column("provider", sa.String(128), nullable=False), sa.Column("attribution", sa.Text(), nullable=False), sa.Column("source_payload", postgresql.JSONB(), nullable=False))
    op.create_index("ix_stations_city_id", "stations", ["city_id"])
    op.create_table("station_connectors", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False), sa.Column("connector_type", sa.String(64), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("power_kw", sa.Float()))
    op.create_table("station_observations", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("occupied_count", sa.Integer(), nullable=False), sa.Column("occupancy_rate", sa.Float(), nullable=False), sa.Column("duration_h", sa.Float(), nullable=False), sa.Column("volume_kwh", sa.Float(), nullable=False), sa.Column("electricity_price_inr", sa.Float(), nullable=False), sa.Column("service_price_inr", sa.Float(), nullable=False), sa.UniqueConstraint("station_id", "observed_at", name="uq_station_observed_at"))
    op.create_table("weather_observations", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("city_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cities.id", ondelete="CASCADE"), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("temperature_c", sa.Float(), nullable=False), sa.Column("precipitation_mm", sa.Float(), nullable=False), sa.Column("humidity_pct", sa.Float(), nullable=False), sa.Column("source", sa.String(64), nullable=False), sa.UniqueConstraint("city_id", "observed_at", name="uq_city_weather_at"))
    op.create_table("station_edges", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False), sa.Column("neighbour_station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False), sa.Column("distance_km", sa.Float(), nullable=False), sa.UniqueConstraint("station_id", "neighbour_station_id", name="uq_station_neighbour"))
    op.create_table("forecasts", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False), sa.Column("as_of", sa.DateTime(timezone=True), nullable=False), sa.Column("horizon_h", sa.Integer(), nullable=False), sa.Column("q10", sa.Float(), nullable=False), sa.Column("q50", sa.Float(), nullable=False), sa.Column("q90", sa.Float(), nullable=False), sa.Column("model_version", sa.String(128), nullable=False), sa.UniqueConstraint("station_id", "as_of", "horizon_h", "model_version", name="uq_forecast_version"))
    op.create_table("evaluation_runs", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_key", sa.String(128), nullable=False, unique=True), sa.Column("config_json", postgresql.JSONB(), nullable=False), sa.Column("seed", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("evaluation_metrics", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False), sa.Column("strategy", sa.String(64), nullable=False), sa.Column("metric", sa.String(64), nullable=False), sa.Column("value", sa.Float(), nullable=False), sa.Column("ci_low", sa.Float()), sa.Column("ci_high", sa.Float()))
    op.create_table("evaluation_decisions", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False), sa.Column("station_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stations.id"), nullable=False), sa.Column("strategy", sa.String(64), nullable=False), sa.Column("as_of", sa.DateTime(timezone=True), nullable=False), sa.Column("predicted_wait_min", sa.Float(), nullable=False), sa.Column("realised_wait_min", sa.Float(), nullable=False), sa.Column("regret_inr", sa.Float(), nullable=False))


def downgrade() -> None:
    for table in ("evaluation_decisions", "evaluation_metrics", "evaluation_runs", "forecasts", "station_edges", "weather_observations", "station_observations", "station_connectors", "stations", "ingestion_runs", "cities"):
        op.drop_table(table)
