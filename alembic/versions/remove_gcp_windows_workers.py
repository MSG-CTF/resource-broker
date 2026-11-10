"""Remove the retired GCP Windows worker pools and leases."""

from alembic import op


revision = "20261027_0015"
down_revision = "20261027_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("windows_worker_leases")
    op.drop_table("windows_workers")
    op.drop_table("windows_worker_pools")


def downgrade() -> None:
    raise NotImplementedError("GCP Windows worker records cannot be restored by downgrade")
