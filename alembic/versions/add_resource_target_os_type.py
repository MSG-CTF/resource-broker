"""Persist the OS reported by the Provider without guessing existing VM types."""

from alembic import op
import sqlalchemy as sa

revision = "20261026_0013"
down_revision = "20261024_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource_targets", sa.Column("os_type", sa.String(16), nullable=True))
    op.create_check_constraint(
        "resource_target_os_type", "resource_targets", "os_type IN ('WINDOWS', 'LINUX')"
    )


def downgrade() -> None:
    op.drop_constraint("resource_target_os_type", "resource_targets", type_="check")
    op.drop_column("resource_targets", "os_type")
