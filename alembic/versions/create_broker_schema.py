from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260801_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


provider_enum = sa.Enum(
    "AWS",
    "GCP",
    "AZURE",
    name="provider",
    native_enum=False,
    create_constraint=True,
)
credential_status_enum = sa.Enum(
    "VALID",
    "INVALID",
    "UNKNOWN",
    name="credential_status",
    native_enum=False,
    create_constraint=True,
)
permission_status_enum = sa.Enum(
    "SUFFICIENT",
    "INSUFFICIENT",
    "UNKNOWN",
    name="permission_status",
    native_enum=False,
    create_constraint=True,
)
provider_api_status_enum = sa.Enum(
    "AVAILABLE",
    "UNAVAILABLE",
    "UNKNOWN",
    name="provider_api_status",
    native_enum=False,
    create_constraint=True,
)
runtime_type_enum = sa.Enum(
    "KUBERNETES",
    name="runtime_type",
    native_enum=False,
    create_constraint=True,
)
architecture_enum = sa.Enum(
    "AMD64",
    "ARM64",
    name="architecture",
    native_enum=False,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "provider_accounts",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column(
            "external_account_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "auth_method",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "credential_reference",
            sa.String(length=1024),
            nullable=True,
        ),
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "credential_status",
            credential_status_enum,
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column(
            "permission_status",
            permission_status_enum,
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column(
            "provider_api_status",
            provider_api_status_enum,
            server_default="UNKNOWN",
            nullable=False,
        ),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            name=op.f("pk_provider_accounts"),
        ),
        sa.UniqueConstraint(
            "provider",
            "external_account_id",
            name="uq_provider_accounts_provider_external_account_id",
        ),
    )

    op.create_table(
        "resource_targets",
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "provider_instance_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "provider_scope_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "instance_name",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "provider_instance_state",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "provider_machine_type",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "private_ip",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "public_ip",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("zone", sa.String(length=128), nullable=True),
        sa.Column("runtime_type", runtime_type_enum, nullable=True),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("architecture", architecture_enum, nullable=True),
        sa.Column(
            "provider_capacity_cpu_millicores",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "provider_capacity_memory_mib",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "provider_capacity_storage_mib",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "allocatable_cpu_millicores",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "allocatable_memory_mib",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "allocatable_ephemeral_storage_mib",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "ready",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "retired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "provider_capacity_cpu_millicores IS NULL "
                "OR provider_capacity_cpu_millicores >= 0"
            ),
            name=op.f(
                "ck_resource_targets_provider_capacity_cpu_non_negative"
            ),
        ),
        sa.CheckConstraint(
            (
                "provider_capacity_memory_mib IS NULL "
                "OR provider_capacity_memory_mib >= 0"
            ),
            name=op.f(
                "ck_resource_targets_provider_capacity_memory_non_negative"
            ),
        ),
        sa.CheckConstraint(
            (
                "provider_capacity_storage_mib IS NULL "
                "OR provider_capacity_storage_mib >= 0"
            ),
            name=op.f(
                "ck_resource_targets_provider_capacity_storage_non_negative"
            ),
        ),
        sa.CheckConstraint(
            (
                "allocatable_cpu_millicores IS NULL "
                "OR allocatable_cpu_millicores >= 0"
            ),
            name=op.f(
                "ck_resource_targets_allocatable_cpu_non_negative"
            ),
        ),
        sa.CheckConstraint(
            (
                "allocatable_memory_mib IS NULL "
                "OR allocatable_memory_mib >= 0"
            ),
            name=op.f(
                "ck_resource_targets_allocatable_memory_non_negative"
            ),
        ),
        sa.CheckConstraint(
            (
                "allocatable_ephemeral_storage_mib IS NULL "
                "OR allocatable_ephemeral_storage_mib >= 0"
            ),
            name=op.f(
                "ck_resource_targets_allocatable_storage_non_negative"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["provider_accounts.account_id"],
            name=op.f(
                "fk_resource_targets_account_id_provider_accounts"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "resource_target_id",
            name=op.f("pk_resource_targets"),
        ),
        sa.UniqueConstraint(
            "account_id",
            "provider_scope_id",
            "provider_instance_id",
            name="uq_resource_targets_account_scope_instance",
        ),
        sa.UniqueConstraint(
            "target_id",
            name="uq_resource_targets_target_id",
        ),
    )
    op.create_index(
        "ix_resource_targets_account_id",
        "resource_targets",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_targets_account_scope",
        "resource_targets",
        ["account_id", "provider_scope_id"],
        unique=False,
    )
    op.create_index(
        "ix_resource_targets_candidate_state",
        "resource_targets",
        ["enabled", "ready", "retired_at"],
        unique=False,
    )

    op.create_table(
        "runtime_containers",
        sa.Column(
            "resource_target_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "container_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "container_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("pod_name", sa.String(length=255), nullable=True),
        sa.Column("namespace", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "cpu_usage_millicores",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("memory_usage_mib", sa.Integer(), nullable=True),
        sa.Column("storage_usage_mib", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cpu_usage_millicores IS NULL OR cpu_usage_millicores >= 0",
            name=op.f("ck_runtime_containers_cpu_usage_non_negative"),
        ),
        sa.CheckConstraint(
            "memory_usage_mib IS NULL OR memory_usage_mib >= 0",
            name=op.f("ck_runtime_containers_memory_usage_non_negative"),
        ),
        sa.CheckConstraint(
            "storage_usage_mib IS NULL OR storage_usage_mib >= 0",
            name=op.f("ck_runtime_containers_storage_usage_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_target_id"],
            ["resource_targets.resource_target_id"],
            name=op.f(
                "fk_runtime_containers_resource_target_id_resource_targets"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "resource_target_id",
            "container_id",
            name=op.f("pk_runtime_containers"),
        ),
    )


def downgrade() -> None:
    op.drop_table("runtime_containers")
    op.drop_index(
        "ix_resource_targets_candidate_state",
        table_name="resource_targets",
    )
    op.drop_index(
        "ix_resource_targets_account_scope",
        table_name="resource_targets",
    )
    op.drop_index(
        "ix_resource_targets_account_id",
        table_name="resource_targets",
    )
    op.drop_table("resource_targets")
    op.drop_table("provider_accounts")
