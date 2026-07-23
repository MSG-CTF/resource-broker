from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260721_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resource_targets",
        sa.Column("provider_project_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "resource_targets",
        sa.Column("provider_instance_state", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "resource_targets",
        sa.Column("provider_machine_type", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "resource_targets",
        sa.Column("private_ip", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "resource_targets",
        sa.Column("public_ip", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_resource_targets_account_project",
        "resource_targets",
        ["account_id", "provider_project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_targets_account_project",
        table_name="resource_targets",
    )
    op.drop_column("resource_targets", "public_ip")
    op.drop_column("resource_targets", "private_ip")
    op.drop_column("resource_targets", "provider_machine_type")
    op.drop_column("resource_targets", "provider_instance_state")
    op.drop_column("resource_targets", "provider_project_id")
