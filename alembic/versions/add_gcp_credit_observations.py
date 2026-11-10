"""Store the latest observed GCP credit balance per billing account."""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0019"
down_revision = "20261102_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gcp_credit_observations",
        sa.Column("billing_account_id", sa.String(length=20), primary_key=True),
        sa.Column("amount", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gcp_credit_observations")
