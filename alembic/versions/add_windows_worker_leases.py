from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261007_0011"
down_revision: str | None = "20260901_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "windows_worker_pools",
        sa.Column(
            "pool_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=63), nullable=False),
        sa.Column("zone", sa.String(length=128), nullable=False),
        sa.Column(
            "managed_instance_group",
            sa.String(length=63),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=255)),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.String(length=255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("pool_id"),
        sa.UniqueConstraint(
            "project_id",
            "zone",
            "managed_instance_group",
            name="uq_windows_worker_pools_gcp_mig",
        ),
    )
    op.create_index(
        "ix_windows_worker_pools_last_synced_at",
        "windows_worker_pools",
        ["last_synced_at"],
    )

    op.create_table(
        "windows_workers",
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "pool_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("instance_name", sa.String(length=255), nullable=False),
        sa.Column("instance_id", sa.String(length=255)),
        sa.Column("private_ip", sa.String(length=64)),
        sa.Column(
            "status",
            sa.String(length=12),
            server_default="PROVISIONING",
            nullable=False,
        ),
        sa.Column(
            "recycle_request_id",
            postgresql.UUID(as_uuid=True),
        ),
        sa.Column("recycle_source_instance_id", sa.String(length=255)),
        sa.Column("recycle_started_at", sa.DateTime(timezone=True)),
        sa.Column("recreate_requested_at", sa.DateTime(timezone=True)),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.String(length=255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PROVISIONING', 'READY', 'LEASED', "
            "'RECREATING', 'FAILED')",
            name=op.f("ck_windows_workers_windows_worker_status"),
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["windows_worker_pools.pool_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("worker_id"),
        sa.UniqueConstraint(
            "pool_id",
            "instance_name",
            name="uq_windows_workers_pool_instance_name",
        ),
    )
    op.create_index(
        "ix_windows_workers_status",
        "windows_workers",
        ["status"],
    )

    op.create_table(
        "windows_worker_leases",
        sa.Column(
            "lease_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "worker_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=9),
            server_default="LEASED",
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('LEASED', 'COMPLETED', 'EXPIRED')",
            name=op.f(
                "ck_windows_worker_leases_windows_worker_lease_status"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["windows_workers.worker_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("lease_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_windows_worker_leases_idempotency_key",
        ),
    )
    op.create_index(
        "ix_windows_worker_leases_status_expires",
        "windows_worker_leases",
        ["status", "expires_at"],
    )
    op.create_index(
        "uq_windows_worker_leases_active_worker",
        "windows_worker_leases",
        ["worker_id"],
        unique=True,
        postgresql_where=sa.text("status = 'LEASED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_windows_worker_leases_active_worker",
        table_name="windows_worker_leases",
    )
    op.drop_index(
        "ix_windows_worker_leases_status_expires",
        table_name="windows_worker_leases",
    )
    op.drop_table("windows_worker_leases")
    op.drop_index(
        "ix_windows_workers_status",
        table_name="windows_workers",
    )
    op.drop_table("windows_workers")
    op.drop_index(
        "ix_windows_worker_pools_last_synced_at",
        table_name="windows_worker_pools",
    )
    op.drop_table("windows_worker_pools")
