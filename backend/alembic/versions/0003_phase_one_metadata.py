"""model registry and evaluation analysis metadata"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_phase_one_metadata"
down_revision = "0002_user_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("model_registry", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("model_version", sa.String(128), nullable=False, unique=True), sa.Column("model_type", sa.String(64), nullable=False), sa.Column("config_hash", sa.String(128), nullable=False), sa.Column("data_hash", sa.String(128), nullable=False), sa.Column("split_hash", sa.String(128), nullable=False), sa.Column("metrics_json", postgresql.JSONB(), nullable=False), sa.Column("artifact_uri", sa.Text()), sa.Column("library_versions", postgresql.JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_index("ix_model_registry_model_version", "model_registry", ["model_version"])
    op.create_table("evaluation_ablations", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False), sa.Column("variant", sa.String(128), nullable=False), sa.Column("metrics_json", postgresql.JSONB(), nullable=False))
    op.create_index("ix_evaluation_ablations_run_id", "evaluation_ablations", ["run_id"])
    op.create_table("evaluation_sensitivities", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False), sa.Column("parameter", sa.String(128), nullable=False), sa.Column("value", sa.Float(), nullable=False), sa.Column("metrics_json", postgresql.JSONB(), nullable=False))
    op.create_index("ix_evaluation_sensitivities_run_id", "evaluation_sensitivities", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_sensitivities_run_id", table_name="evaluation_sensitivities")
    op.drop_table("evaluation_sensitivities")
    op.drop_index("ix_evaluation_ablations_run_id", table_name="evaluation_ablations")
    op.drop_table("evaluation_ablations")
    op.drop_index("ix_model_registry_model_version", table_name="model_registry")
    op.drop_table("model_registry")
