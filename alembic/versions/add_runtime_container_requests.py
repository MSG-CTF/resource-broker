from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260806_0004"
down_revision: str | None = "20260805_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_containers",
        sa.Column("cpu_request_millicores", sa.Integer(), nullable=True),
    )
    op.add_column(
        "runtime_containers",
        sa.Column("memory_request_mib", sa.Integer(), nullable=True),
    )
    op.add_column(
        "runtime_containers",
        sa.Column(
            "ephemeral_storage_request_mib",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_runtime_containers_cpu_request_non_negative"),
        "runtime_containers",
        "cpu_request_millicores IS NULL OR cpu_request_millicores >= 0",
    )
    op.create_check_constraint(
        op.f("ck_runtime_containers_memory_request_non_negative"),
        "runtime_containers",
        "memory_request_mib IS NULL OR memory_request_mib >= 0",
    )
    op.create_check_constraint(
        op.f("ck_runtime_containers_ephemeral_storage_request_non_negative"),
        "runtime_containers",
        (
            "ephemeral_storage_request_mib IS NULL "
            "OR ephemeral_storage_request_mib >= 0"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_runtime_containers_ephemeral_storage_request_non_negative"),
        "runtime_containers",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_containers_memory_request_non_negative"),
        "runtime_containers",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_containers_cpu_request_non_negative"),
        "runtime_containers",
        type_="check",
    )
    op.drop_column("runtime_containers", "ephemeral_storage_request_mib")
    op.drop_column("runtime_containers", "memory_request_mib")
    op.drop_column("runtime_containers", "cpu_request_millicores")
