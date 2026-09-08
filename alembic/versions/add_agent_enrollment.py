from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260805_0003"
down_revision: str | None = "20260803_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_enrollment_tokens",
        sa.Column(
            "enrollment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            name=op.f(
                "fk_agent_enrollment_tokens_resource_target_id_"
                "resource_targets"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "enrollment_id",
            name=op.f("pk_agent_enrollment_tokens"),
        ),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_agent_enrollment_tokens_token_hash",
        ),
    )
    op.create_index(
        "ix_agent_enrollment_tokens_resource_target_id",
        "agent_enrollment_tokens",
        ["resource_target_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_enrollment_tokens_expires_at",
        "agent_enrollment_tokens",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "agent_certificates",
        sa.Column(
            "certificate_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "enrollment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "issuer_fingerprint_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("serial_number", sa.String(length=64), nullable=False),
        sa.Column(
            "fingerprint_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "not_before",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "not_after",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["agent_enrollment_tokens.enrollment_id"],
            name=op.f(
                "fk_agent_certificates_enrollment_id_"
                "agent_enrollment_tokens"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            name=op.f(
                "fk_agent_certificates_resource_target_id_resource_targets"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "certificate_id",
            name=op.f("pk_agent_certificates"),
        ),
        sa.UniqueConstraint(
            "enrollment_id",
            name="uq_agent_certificates_enrollment_id",
        ),
        sa.UniqueConstraint(
            "fingerprint_sha256",
            name="uq_agent_certificates_fingerprint_sha256",
        ),
        sa.UniqueConstraint(
            "issuer_fingerprint_sha256",
            "serial_number",
            name="uq_agent_certificates_issuer_serial",
        ),
    )
    op.create_index(
        "ix_agent_certificates_resource_target_id",
        "agent_certificates",
        ["resource_target_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_certificates_not_after",
        "agent_certificates",
        ["not_after"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_certificates_not_after",
        table_name="agent_certificates",
    )
    op.drop_index(
        "ix_agent_certificates_resource_target_id",
        table_name="agent_certificates",
    )
    op.drop_table("agent_certificates")
    op.drop_index(
        "ix_agent_enrollment_tokens_expires_at",
        table_name="agent_enrollment_tokens",
    )
    op.drop_index(
        "ix_agent_enrollment_tokens_resource_target_id",
        table_name="agent_enrollment_tokens",
    )
    op.drop_table("agent_enrollment_tokens")
