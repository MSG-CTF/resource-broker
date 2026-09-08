from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_0007"
down_revision: str | None = "20260819_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resource_targets",
        sa.Column(
            "runtime_cpu_usage_millicores",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "resource_targets",
        sa.Column(
            "runtime_memory_usage_mib",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_resource_targets_runtime_cpu_usage_non_negative"),
        "resource_targets",
        (
            "runtime_cpu_usage_millicores IS NULL "
            "OR runtime_cpu_usage_millicores >= 0"
        ),
    )
    op.create_check_constraint(
        op.f("ck_resource_targets_runtime_memory_usage_non_negative"),
        "resource_targets",
        (
            "runtime_memory_usage_mib IS NULL "
            "OR runtime_memory_usage_mib >= 0"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_resource_targets_runtime_memory_usage_non_negative"),
        "resource_targets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_resource_targets_runtime_cpu_usage_non_negative"),
        "resource_targets",
        type_="check",
    )
    op.drop_column("resource_targets", "runtime_memory_usage_mib")
    op.drop_column("resource_targets", "runtime_cpu_usage_millicores")
