from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260803_0002"
down_revision: str | None = "20260801_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resource_targets",
        sa.Column(
            "runtime_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "resource_targets",
        sa.Column(
            "runtime_last_seen_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("resource_targets", "runtime_last_seen_at")
    op.drop_column("resource_targets", "runtime_observed_at")
