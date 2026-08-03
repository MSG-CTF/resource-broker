from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSqlUuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import (
    Architecture,
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
    RuntimeType,
)


def _enum_type(enum_type: type[Any], name: str) -> SqlEnum:
    return SqlEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class ProviderAccountTable(Base):
    __tablename__ = "provider_accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_account_id",
            name="uq_provider_accounts_provider_external_account_id",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    provider: Mapped[Provider] = mapped_column(
        _enum_type(Provider, "provider"),
        nullable=False,
    )
    external_account_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    auth_method: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    credential_reference: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    credential_status: Mapped[CredentialStatus] = mapped_column(
        _enum_type(CredentialStatus, "credential_status"),
        nullable=False,
        default=CredentialStatus.UNKNOWN,
        server_default=CredentialStatus.UNKNOWN.value,
    )
    permission_status: Mapped[PermissionStatus] = mapped_column(
        _enum_type(PermissionStatus, "permission_status"),
        nullable=False,
        default=PermissionStatus.UNKNOWN,
        server_default=PermissionStatus.UNKNOWN.value,
    )
    provider_api_status: Mapped[ProviderApiStatus] = mapped_column(
        _enum_type(ProviderApiStatus, "provider_api_status"),
        nullable=False,
        default=ProviderApiStatus.UNKNOWN,
        server_default=ProviderApiStatus.UNKNOWN.value,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    resource_targets: Mapped[list[ResourceTargetTable]] = relationship(
        back_populates="account",
    )


class ResourceTargetTable(Base):
    __tablename__ = "resource_targets"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "provider_scope_id",
            "provider_instance_id",
            name="uq_resource_targets_account_scope_instance",
        ),
        UniqueConstraint(
            "target_id",
            name="uq_resource_targets_target_id",
        ),
        CheckConstraint(
            (
                "provider_capacity_cpu_millicores IS NULL "
                "OR provider_capacity_cpu_millicores >= 0"
            ),
            name="provider_capacity_cpu_non_negative",
        ),
        CheckConstraint(
            (
                "provider_capacity_memory_mib IS NULL "
                "OR provider_capacity_memory_mib >= 0"
            ),
            name="provider_capacity_memory_non_negative",
        ),
        CheckConstraint(
            (
                "provider_capacity_storage_mib IS NULL "
                "OR provider_capacity_storage_mib >= 0"
            ),
            name="provider_capacity_storage_non_negative",
        ),
        CheckConstraint(
            (
                "allocatable_cpu_millicores IS NULL "
                "OR allocatable_cpu_millicores >= 0"
            ),
            name="allocatable_cpu_non_negative",
        ),
        CheckConstraint(
            (
                "allocatable_memory_mib IS NULL "
                "OR allocatable_memory_mib >= 0"
            ),
            name="allocatable_memory_non_negative",
        ),
        CheckConstraint(
            (
                "allocatable_ephemeral_storage_mib IS NULL "
                "OR allocatable_ephemeral_storage_mib >= 0"
            ),
            name="allocatable_storage_non_negative",
        ),
        Index(
            "ix_resource_targets_account_id",
            "account_id",
        ),
        Index(
            "ix_resource_targets_account_scope",
            "account_id",
            "provider_scope_id",
        ),
        Index(
            "ix_resource_targets_candidate_state",
            "enabled",
            "ready",
            "retired_at",
        ),
    )

    resource_target_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True),
        ForeignKey(
            "provider_accounts.account_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    provider_instance_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    provider_scope_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    instance_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_instance_state: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    provider_machine_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    private_ip: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    public_ip: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    region: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    zone: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    runtime_type: Mapped[RuntimeType | None] = mapped_column(
        _enum_type(RuntimeType, "runtime_type"),
        nullable=True,
    )
    target_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    architecture: Mapped[Architecture | None] = mapped_column(
        _enum_type(Architecture, "architecture"),
        nullable=True,
    )
    provider_capacity_cpu_millicores: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    provider_capacity_memory_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    provider_capacity_storage_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    allocatable_cpu_millicores: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    allocatable_memory_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    allocatable_ephemeral_storage_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    ready: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    runtime_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    runtime_last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped[ProviderAccountTable] = relationship(
        back_populates="resource_targets",
    )

    runtime_containers: Mapped[list[RuntimeContainerTable]] = relationship(
        back_populates="resource_target",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RuntimeContainerTable(Base):
    __tablename__ = "runtime_containers"
    __table_args__ = (
        CheckConstraint(
            (
                "cpu_usage_millicores IS NULL "
                "OR cpu_usage_millicores >= 0"
            ),
            name="cpu_usage_non_negative",
        ),
        CheckConstraint(
            "memory_usage_mib IS NULL OR memory_usage_mib >= 0",
            name="memory_usage_non_negative",
        ),
        CheckConstraint(
            "storage_usage_mib IS NULL OR storage_usage_mib >= 0",
            name="storage_usage_non_negative",
        ),
    )

    resource_target_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True),
        ForeignKey(
            "resource_targets.resource_target_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    container_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    container_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    pod_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    namespace: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    cpu_usage_millicores: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    memory_usage_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    storage_usage_mib: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    resource_target: Mapped[ResourceTargetTable] = relationship(
        back_populates="runtime_containers",
    )
