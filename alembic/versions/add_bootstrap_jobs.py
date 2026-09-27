from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260811_0005"
down_revision: str | None = "20260806_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bootstrap_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=5), nullable=False),
        sa.Column("action", sa.String(length=7), nullable=False),
        sa.Column(
            "status",
            sa.String(length=9),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("bootstrap_version", sa.String(length=32), nullable=False),
        sa.Column("k3s_version", sa.String(length=64), nullable=True),
        sa.Column("agent_image", sa.String(length=512), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("runner_sha256", sa.String(length=64), nullable=False),
        sa.Column("enrollment_audience", sa.String(length=1024), nullable=False),
        sa.Column("provider_job_id", sa.String(length=255), nullable=True),
        sa.Column("temporary_label_key", sa.String(length=63), nullable=False),
        sa.Column("temporary_label_value", sa.String(length=63), nullable=False),
        sa.Column("enrollment_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('AWS', 'GCP', 'AZURE')",
            name=op.f("ck_bootstrap_jobs_bootstrap_job_provider"),
        ),
        sa.CheckConstraint(
            "action IN ('INSTALL', 'UPDATE', 'CHECK', 'REMOVE')",
            name=op.f("ck_bootstrap_jobs_bootstrap_action"),
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'APPLYING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name=op.f("ck_bootstrap_jobs_bootstrap_job_status"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "ix_bootstrap_jobs_resource_created",
        "bootstrap_jobs",
        ["resource_target_id", "created_at"],
    )
    op.create_index(
        "ix_bootstrap_jobs_status_updated",
        "bootstrap_jobs",
        ["status", "updated_at"],
    )
    op.create_index(
        "uq_bootstrap_jobs_one_active_target",
        "bootstrap_jobs",
        ["resource_target_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'APPLYING', 'RUNNING')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_bootstrap_jobs_one_active_target",
        table_name="bootstrap_jobs",
    )
    op.drop_index("ix_bootstrap_jobs_status_updated", table_name="bootstrap_jobs")
    op.drop_index("ix_bootstrap_jobs_resource_created", table_name="bootstrap_jobs")
    op.drop_table("bootstrap_jobs")
