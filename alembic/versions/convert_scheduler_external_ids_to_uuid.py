from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260821_0008"
down_revision: str | None = "20260819_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_reservations_active_instance",
        table_name="reservations",
    )
    op.drop_constraint(
        op.f("ck_reservations_team_id_positive"),
        "reservations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_reservations_challenge_id_positive"),
        "reservations",
        type_="check",
    )

    # Preserve legacy integer identity without deleting reservation history.
    # Team and challenge values use separate deterministic UUID namespaces.
    op.alter_column(
        "reservations",
        "team_id",
        existing_type=sa.Integer(),
        type_=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
        postgresql_using=(
            "('10000000-0000-5000-8000-' "
            "|| lpad(to_hex(team_id), 12, '0'))::uuid"
        ),
    )
    op.alter_column(
        "reservations",
        "challenge_id",
        existing_type=sa.Integer(),
        type_=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
        postgresql_using=(
            "('20000000-0000-5000-8000-' "
            "|| lpad(to_hex(challenge_id), 12, '0'))::uuid"
        ),
    )

    op.create_index(
        "uq_reservations_active_instance",
        "reservations",
        ["team_id", "instance_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('HELD', 'COMMITTED')"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Scheduler team_id/challenge_id UUID migration is intentionally "
        "irreversible because arbitrary UUIDs cannot be converted safely "
        "back to positive integers."
    )
