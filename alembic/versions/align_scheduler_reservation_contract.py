from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0009"
down_revision: str | None = "20260821_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "reservations",
        "idempotency_key",
        existing_type=sa.String(length=128),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.add_column(
        "reservations",
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE reservations SET requested_at = created_at "
        "WHERE requested_at IS NULL"
    )
    op.alter_column(
        "reservations",
        "requested_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    op.add_column(
        "reservations",
        sa.Column("commit_request_id", sa.String(length=255)),
    )
    op.add_column(
        "reservations",
        sa.Column("commit_requested_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "reservations",
        sa.Column("runtime_workload_id", sa.String(length=255)),
    )
    op.add_column(
        "reservations",
        sa.Column("deployed_cpu_millicores", sa.Integer()),
    )
    op.add_column(
        "reservations",
        sa.Column("deployed_memory_mib", sa.Integer()),
    )
    op.add_column(
        "reservations",
        sa.Column("deployed_ephemeral_storage_mib", sa.Integer()),
    )
    op.create_check_constraint(
        op.f("ck_reservations_deployed_cpu_positive"),
        "reservations",
        (
            "deployed_cpu_millicores IS NULL "
            "OR deployed_cpu_millicores > 0"
        ),
    )
    op.create_check_constraint(
        op.f("ck_reservations_deployed_memory_positive"),
        "reservations",
        "deployed_memory_mib IS NULL OR deployed_memory_mib > 0",
    )
    op.create_check_constraint(
        op.f("ck_reservations_deployed_ephemeral_storage_positive"),
        "reservations",
        (
            "deployed_ephemeral_storage_mib IS NULL "
            "OR deployed_ephemeral_storage_mib > 0"
        ),
    )

    op.add_column(
        "reservations",
        sa.Column("release_request_id", sa.String(length=255)),
    )
    op.add_column(
        "reservations",
        sa.Column("release_requested_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "reservations",
        sa.Column("release_reason", sa.String(length=22)),
    )
    op.create_check_constraint(
        op.f("ck_reservations_reservation_release_reason"),
        "reservations",
        (
            "release_reason IS NULL OR release_reason IN "
            "('RUNTIME_CREATE_FAILED', 'DEPLOYED_SPEC_MISMATCH', "
            "'SCHEDULER_CANCELLED')"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_reservations_reservation_release_reason"),
        "reservations",
        type_="check",
    )
    op.drop_column("reservations", "release_reason")
    op.drop_column("reservations", "release_requested_at")
    op.drop_column("reservations", "release_request_id")

    op.drop_constraint(
        op.f("ck_reservations_deployed_ephemeral_storage_positive"),
        "reservations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_reservations_deployed_memory_positive"),
        "reservations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_reservations_deployed_cpu_positive"),
        "reservations",
        type_="check",
    )
    op.drop_column("reservations", "deployed_ephemeral_storage_mib")
    op.drop_column("reservations", "deployed_memory_mib")
    op.drop_column("reservations", "deployed_cpu_millicores")
    op.drop_column("reservations", "runtime_workload_id")
    op.drop_column("reservations", "commit_requested_at")
    op.drop_column("reservations", "commit_request_id")
    op.drop_column("reservations", "requested_at")
    op.alter_column(
        "reservations",
        "idempotency_key",
        existing_type=sa.String(length=255),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
