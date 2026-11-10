"""Persist Windows VM recovery notifications until Runtime accepts them."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261028_0017"
down_revision = "20261027_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("windows_vm_executions", sa.Column("recovery_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("windows_vm_executions", sa.Column("recovery_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("windows_vm_executions", sa.Column("recovery_delivered_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("windows_vm_executions", "recovery_delivered_at")
    op.drop_column("windows_vm_executions", "recovery_retry_at")
    op.drop_column("windows_vm_executions", "recovery_event_id")
