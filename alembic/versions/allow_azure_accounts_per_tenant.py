"""Allow separate Azure accounts for subscriptions in one Entra tenant."""

from alembic import op
import sqlalchemy as sa


revision = "20261102_0018"
down_revision = "20261028_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_provider_accounts_provider_external_account_id", "provider_accounts", type_="unique")
    op.create_index(
        "uq_provider_accounts_provider_external_account_id_non_azure",
        "provider_accounts",
        ["provider", "external_account_id"],
        unique=True,
        postgresql_where=sa.text("provider <> 'AZURE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_accounts_provider_external_account_id_non_azure", table_name="provider_accounts")
    op.create_unique_constraint(
        "uq_provider_accounts_provider_external_account_id", "provider_accounts", ["provider", "external_account_id"]
    )
