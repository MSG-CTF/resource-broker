from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260819_0006"
down_revision: str | None = "20260811_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(length=255), nullable=False),
        sa.Column("cpu_millicores", sa.Integer(), nullable=False),
        sa.Column("memory_mib", sa.Integer(), nullable=False),
        sa.Column(
            "ephemeral_storage_mib",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("architecture", sa.String(length=5), nullable=False),
        sa.Column(
            "status",
            sa.String(length=9),
            server_default="HELD",
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
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
            "architecture IN ('AMD64', 'ARM64')",
            name=op.f("ck_reservations_reservation_architecture"),
        ),
        sa.CheckConstraint(
            "status IN ('HELD', 'COMMITTED', 'RELEASED', 'EXPIRED')",
            name=op.f("ck_reservations_reservation_status"),
        ),
        sa.CheckConstraint(
            "cpu_millicores > 0",
            name=op.f("ck_reservations_cpu_positive"),
        ),
        sa.CheckConstraint(
            "memory_mib > 0",
            name=op.f("ck_reservations_memory_positive"),
        ),
        sa.CheckConstraint(
            "ephemeral_storage_mib > 0",
            name=op.f("ck_reservations_ephemeral_storage_positive"),
        ),
        sa.CheckConstraint(
            "team_id > 0",
            name=op.f("ck_reservations_team_id_positive"),
        ),
        sa.CheckConstraint(
            "challenge_id > 0",
            name=op.f("ck_reservations_challenge_id_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_reservations_idempotency_key",
        ),
    )
    op.create_index(
        "ix_reservations_target_status",
        "reservations",
        ["resource_target_id", "status"],
    )
    op.create_index(
        "ix_reservations_expires_at",
        "reservations",
        ["expires_at"],
    )
    op.create_index(
        "uq_reservations_active_instance",
        "reservations",
        ["team_id", "instance_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('HELD', 'COMMITTED')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reservations_active_instance",
        table_name="reservations",
    )
    op.drop_index(
        "ix_reservations_expires_at",
        table_name="reservations",
    )
    op.drop_index(
        "ix_reservations_target_status",
        table_name="reservations",
    )
    op.drop_table("reservations")
