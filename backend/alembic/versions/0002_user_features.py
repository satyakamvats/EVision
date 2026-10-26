"""user profiles and saved product features"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_user_features"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("user_profiles", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("email", sa.String(320)), sa.Column("display_name", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("user_preferences", sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True), sa.Column("energy_kwh", sa.Float(), nullable=False, server_default="20"), sa.Column("radius_km", sa.Float(), nullable=False, server_default="25"), sa.Column("risk_kappa", sa.Float(), nullable=False, server_default="0.5"), sa.Column("value_of_time_per_h", sa.Float(), nullable=False, server_default="150"), sa.Column("carbon_price_per_kg", sa.Float(), nullable=False, server_default="0.5"), sa.Column("carbon_scenario", sa.String(32), nullable=False, server_default="tou_profile"), sa.Column("origin_lat", sa.Float()), sa.Column("origin_lon", sa.Float()))
    op.create_table("favourite_stations", sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True), sa.Column("station_id", sa.String(128), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("recommendation_history", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("request_json", postgresql.JSONB(), nullable=False), sa.Column("response_json", postgresql.JSONB(), nullable=False), sa.Column("data_source", sa.String(32), nullable=False, server_default="synthetic"))
    op.create_index("ix_recommendation_history_user_id", "recommendation_history", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_history_user_id", table_name="recommendation_history")
    op.drop_table("recommendation_history")
    op.drop_table("favourite_stations")
    op.drop_table("user_preferences")
    op.drop_table("user_profiles")
