from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.tables import ResourceTargetTable
from app.db.windows_runtime import WindowsVmConnectionTable, WindowsVmExecutionTable
from app.domain.enums import Provider
from app.repositories.windows_runtime import WindowsRuntimeRepository


class WindowsRuntimeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class ExecutionOutcome:
    connection: WindowsVmConnectionTable
    execution: WindowsVmExecutionTable
    replayed: bool


class WindowsRuntimeService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = WindowsRuntimeRepository(session)

    def _target(self, resource_target_id: UUID, *, for_update: bool = False):
        target = self.repository.target(resource_target_id, for_update=for_update)
        if target is None or target.provider != Provider.AZURE or target.os_type != "WINDOWS":
            raise WindowsRuntimeError("WINDOWS_VM_NOT_FOUND", "The Azure Windows VM was not found.")
        return target

    def _available(self, target: ResourceTargetTable, connection: WindowsVmConnectionTable):
        account = self.repository.account(target.account_id)
        if not connection.enabled:
            raise WindowsRuntimeError("WINDOWS_CONNECTION_DISABLED", "Windows Runtime access is disabled.")
        if account is None or not account.enabled or not target.enabled or target.retired_at is not None or target.provider_instance_state != "RUNNING":
            raise WindowsRuntimeError("WINDOWS_VM_UNAVAILABLE", "The VM must be registered, running, and belong to an enabled account.")
        if connection.state != "READY":
            raise WindowsRuntimeError("WINDOWS_VM_NOT_READY", "The VM is running an execution or requires a clean reset.")

    def configured_connection(self, resource_target_id: UUID):
        self._target(resource_target_id)
        connection = self.repository.connection(resource_target_id)
        if connection is None:
            raise WindowsRuntimeError("WINDOWS_CONNECTION_NOT_CONFIGURED", "Verified Windows RDP connection information has not been configured.")
        return connection

    def _host(self, target: ResourceTargetTable, connection: WindowsVmConnectionTable):
        raw_host = target.public_ip if connection.address_source == "public_ip" else target.private_ip
        try:
            host = ipaddress.ip_address(raw_host)
        except (ValueError, TypeError) as error:
            raise WindowsRuntimeError("WINDOWS_VM_ADDRESS_UNAVAILABLE", "The selected VM address is unavailable.") from error
        if host.is_loopback or host.is_unspecified or host.is_multicast or host.is_link_local:
            raise WindowsRuntimeError("WINDOWS_VM_ADDRESS_UNAVAILABLE", "The selected VM address is not usable.")
        return str(host)

    def connection(self, resource_target_id: UUID):
        target = self._target(resource_target_id)
        connection = self.configured_connection(resource_target_id)
        self._available(target, connection)
        return target, connection, self._host(target, connection)

    def available(self, *, after: UUID | None, limit: int):
        rows = self.repository.available(after=after, limit=limit)
        items = []
        for target, connection in rows[:limit]:
            try:
                host = self._host(target, connection)
            except WindowsRuntimeError:
                continue
            items.append((target, connection, host))
        next_cursor = rows[limit - 1][0].resource_target_id if len(rows) > limit else None
        return items, next_cursor

    def configure(self, resource_target_id: UUID, *, username: str, port: int, address_source: str, enabled: bool, reset_execution_id: UUID | None):
        self._target(resource_target_id, for_update=True)
        connection = self.repository.connection(resource_target_id)
        if connection is None:
            if reset_execution_id is not None:
                raise WindowsRuntimeError("WINDOWS_RESET_CONFLICT", "There is no completed execution to reset.")
            connection = WindowsVmConnectionTable(resource_target_id=resource_target_id, state="READY")
            self.repository.add(connection)
        if connection.state == "RUNNING":
            raise WindowsRuntimeError("WINDOWS_VM_BUSY", "Connection settings cannot change during an execution.")
        if reset_execution_id is not None:
            if connection.state != "RESET_REQUIRED" or connection.current_execution_id != reset_execution_id:
                raise WindowsRuntimeError("WINDOWS_RESET_CONFLICT", "The reset confirmation must match the execution awaiting reset.")
            connection.state = "READY"
            connection.current_execution_id = None
        connection.username = username
        connection.port = port
        connection.address_source = address_source
        connection.enabled = enabled
        connection.updated_at = datetime.now(UTC)
        self.session.commit()
        return connection

    def event(self, resource_target_id: UUID, *, execution_id: UUID, event: str, result: str | None, exit_code: int | None):
        target = self._target(resource_target_id, for_update=True)
        connection = self.configured_connection(resource_target_id)
        execution = self.repository.execution(execution_id)
        if execution is not None and execution.resource_target_id != resource_target_id:
            raise WindowsRuntimeError("WINDOWS_EXECUTION_CONFLICT", "This execution ID belongs to another VM.")
        now = datetime.now(UTC)
        if event == "started":
            if execution is not None:
                if execution.finished_at is not None or connection.current_execution_id != execution_id:
                    raise WindowsRuntimeError("WINDOWS_EXECUTION_FINISHED", "This execution has already finished and cannot restart.")
                return ExecutionOutcome(connection, execution, True)
            self._available(target, connection)
            execution = WindowsVmExecutionTable(execution_id=execution_id, resource_target_id=resource_target_id, started_at=now)
            self.repository.add(execution)
            connection.current_execution_id = execution_id
            connection.state = "RUNNING"
        else:
            if execution is None:
                raise WindowsRuntimeError("WINDOWS_EXECUTION_NOT_FOUND", "A successful started event is required before finished.")
            if execution.finished_at is not None:
                if execution.result != result or execution.exit_code != exit_code:
                    raise WindowsRuntimeError("WINDOWS_EXECUTION_CONFLICT", "The completed execution has different result data.")
                return ExecutionOutcome(connection, execution, True)
            if connection.current_execution_id != execution_id or connection.state != "RUNNING":
                raise WindowsRuntimeError("WINDOWS_EXECUTION_CONFLICT", "This is not the VM's active execution.")
            execution.finished_at = now
            execution.result = result
            execution.exit_code = exit_code
            connection.state = "RESET_REQUIRED"
        connection.updated_at = now
        self.session.commit()
        return ExecutionOutcome(connection, execution, False)
