"""Store Windows RDP endpoints and execution lifecycle events."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261027_0014"
down_revision = "20261026_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "windows_vm_connections",
        sa.Column("resource_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resource_targets.resource_target_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("address_source", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("state", sa.String(16), nullable=False, server_default="READY"),
        sa.Column("current_execution_id", postgresql.UUID(as_uuid=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="windows_vm_connection_port"),
        sa.CheckConstraint("address_source IN ('public_ip', 'private_ip')", name="windows_vm_address_source"),
        sa.CheckConstraint("state IN ('READY', 'RUNNING', 'RESET_REQUIRED')", name="windows_vm_state"),
    )
    op.create_table(
        "windows_vm_executions",
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resource_target_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("windows_vm_connections.resource_target_id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(16)),
        sa.Column("exit_code", sa.BigInteger()),
        sa.CheckConstraint("result IS NULL OR result IN ('succeeded', 'failed', 'timeout', 'unreachable')", name="windows_vm_execution_result"),
        sa.CheckConstraint("(finished_at IS NULL AND result IS NULL AND exit_code IS NULL) OR (finished_at IS NOT NULL AND result IS NOT NULL)", name="windows_vm_execution_finished"),
    )
    op.create_index("ix_windows_vm_executions_resource_target_id", "windows_vm_executions", ["resource_target_id"])


def downgrade():
    op.drop_table("windows_vm_executions")
    op.drop_table("windows_vm_connections")
