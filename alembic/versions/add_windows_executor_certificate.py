"""Store the current Windows executor server certificate."""

from alembic import op
import sqlalchemy as sa


revision = "20261027_0016"
down_revision = "20261027_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("windows_vm_connections", sa.Column("executor_certificate_pem", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("windows_vm_connections", "executor_certificate_pem")
