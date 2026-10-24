from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260901_0010"
down_revision: str | None = "20260821_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "k3s_credentials",
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("encrypted_kubeconfig", sa.LargeBinary(), nullable=False),
        sa.Column("kubeconfig_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "encryption_scheme",
            sa.String(length=32),
            server_default="fernet-v1",
            nullable=False,
        ),
        sa.Column("server_url", sa.String(length=128), nullable=False),
        sa.Column(
            "client_certificate_fingerprint_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "client_certificate_not_after",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kubeconfig_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_k3s_credentials_kubeconfig_sha256_format"),
        ),
        sa.CheckConstraint(
            (
                "client_certificate_fingerprint_sha256 "
                "~ '^[0-9a-f]{64}$'"
            ),
            name=op.f(
                "ck_k3s_credentials_"
                "client_certificate_fingerprint_sha256_format"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"],
            ["bootstrap_jobs.job_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("resource_target_id"),
        sa.UniqueConstraint("source_job_id"),
    )
    op.create_index(
        "ix_k3s_credentials_uploaded_at",
        "k3s_credentials",
        ["uploaded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_k3s_credentials_uploaded_at",
        table_name="k3s_credentials",
    )
    op.drop_table("k3s_credentials")
