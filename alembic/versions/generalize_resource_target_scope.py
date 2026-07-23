from collections.abc import Sequence

from alembic import op


revision: str = "20260723_0003"
down_revision: str | None = "20260721_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_resource_targets_account_project",
        table_name="resource_targets",
    )
    op.alter_column(
        "resource_targets",
        "provider_project_id",
        new_column_name="provider_scope_id",
    )
    op.create_index(
        "ix_resource_targets_account_scope",
        "resource_targets",
        ["account_id", "provider_scope_id"],
        unique=False,
    )
    op.drop_constraint(
        "uq_resource_targets_account_provider_instance",
        "resource_targets",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_resource_targets_account_scope_instance",
        "resource_targets",
        ["account_id", "provider_scope_id", "provider_instance_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_resource_targets_account_scope_instance",
        "resource_targets",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_resource_targets_account_provider_instance",
        "resource_targets",
        ["account_id", "provider_instance_id"],
    )
    op.drop_index(
        "ix_resource_targets_account_scope",
        table_name="resource_targets",
    )
    op.alter_column(
        "resource_targets",
        "provider_scope_id",
        new_column_name="provider_project_id",
    )
    op.create_index(
        "ix_resource_targets_account_project",
        "resource_targets",
        ["account_id", "provider_project_id"],
        unique=False,
    )
