from collections.abc import Generator
from datetime import UTC, datetime
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.tables import ProviderAccountTable, ResourceTargetTable
from app.domain.enums import (
    Architecture,
    CredentialStatus,
    PermissionStatus,
    Provider,
    ProviderApiStatus,
    RuntimeType,
)


def _test_database_url() -> str:
    raw_url = os.getenv("TEST_DATABASE_URL", "")
    if not raw_url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL must point to an isolated PostgreSQL test database."
        )
    url = make_url(raw_url)
    if not url.drivername.startswith("postgresql"):
        raise pytest.UsageError("TEST_DATABASE_URL must use PostgreSQL.")
    database_name = url.database or ""
    if not database_name.startswith("msg_broker_test"):
        raise pytest.UsageError(
            "The test database name must start with 'msg_broker_test'."
        )
    return raw_url


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    engine = create_engine(_test_database_url(), pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session_factory(
    db_engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_engine,
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture(autouse=True)
def clean_database(db_engine: Engine) -> Generator[None, None, None]:
    table_names = ", ".join(
        db_engine.dialect.identifier_preparer.quote(table.name)
        for table in reversed(Base.metadata.sorted_tables)
    )
    if table_names:
        with db_engine.begin() as connection:
            connection.execute(
                text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
            )
    yield


@pytest.fixture
def db_session(
    session_factory: sessionmaker[Session],
) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def eligible_resource_target_id(db_session: Session) -> UUID:
    now = datetime.now(UTC)
    account = ProviderAccountTable(
        provider=Provider.GCP,
        external_account_id=f"test-owner-{uuid4()}",
        display_name="Reservation concurrency test",
        auth_method="ADC",
        provider_config={"project_ids": ["msg-broker-test-project"]},
        enabled=True,
        credential_status=CredentialStatus.VALID,
        permission_status=PermissionStatus.SUFFICIENT,
        provider_api_status=ProviderApiStatus.AVAILABLE,
    )
    resource_target_id = uuid4()
    resource = ResourceTargetTable(
        resource_target_id=resource_target_id,
        account=account,
        provider_instance_id=f"instance-{resource_target_id}",
        provider_scope_id="msg-broker-test-project",
        instance_name="reservation-test-vm",
        provider_instance_state="RUNNING",
        provider_machine_type="test-machine",
        region="asia-northeast3",
        zone="asia-northeast3-a",
        runtime_type=RuntimeType.KUBERNETES,
        target_id=f"node-{resource_target_id}",
        architecture=Architecture.AMD64,
        provider_capacity_cpu_millicores=2_000,
        provider_capacity_memory_mib=4_096,
        provider_capacity_storage_mib=20_000,
        allocatable_cpu_millicores=1_000,
        allocatable_memory_mib=1_000,
        allocatable_ephemeral_storage_mib=1_000,
        runtime_cpu_usage_millicores=500,
        runtime_memory_usage_mib=512,
        enabled=True,
        ready=True,
        runtime_observed_at=now,
        runtime_last_seen_at=now,
        observed_at=now,
        last_seen_at=now,
    )
    db_session.add(resource)
    db_session.commit()
    return resource_target_id
