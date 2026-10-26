from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSqlUuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WindowsVmConnectionTable(Base):
    __tablename__ = "windows_vm_connections"
    __table_args__ = (
        CheckConstraint("port BETWEEN 1 AND 65535", name="windows_vm_connection_port"),
        CheckConstraint("address_source IN ('public_ip', 'private_ip')", name="windows_vm_address_source"),
        CheckConstraint("state IN ('READY', 'RUNNING', 'RESET_REQUIRED')", name="windows_vm_state"),
    )

    resource_target_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True), ForeignKey("resource_targets.resource_target_id", ondelete="CASCADE"), primary_key=True,
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=3389)
    address_source: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="READY", server_default="READY")
    current_execution_id: Mapped[UUID | None] = mapped_column(PostgreSqlUuid(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class WindowsVmExecutionTable(Base):
    __tablename__ = "windows_vm_executions"
    __table_args__ = (
        CheckConstraint("result IS NULL OR result IN ('succeeded', 'failed', 'timeout', 'unreachable')", name="windows_vm_execution_result"),
        CheckConstraint("(finished_at IS NULL AND result IS NULL AND exit_code IS NULL) OR (finished_at IS NOT NULL AND result IS NOT NULL)", name="windows_vm_execution_finished"),
    )

    execution_id: Mapped[UUID] = mapped_column(PostgreSqlUuid(as_uuid=True), primary_key=True)
    resource_target_id: Mapped[UUID] = mapped_column(
        PostgreSqlUuid(as_uuid=True), ForeignKey("windows_vm_connections.resource_target_id", ondelete="CASCADE"), nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(16))
    exit_code: Mapped[int | None] = mapped_column(BigInteger)
