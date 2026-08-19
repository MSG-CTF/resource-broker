import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.api.admin_auth import (
    AdminAuthHttpError,
    admin_auth_http_error_handler,
    require_admin,
    router as admin_auth_router,
)
from app.api.admin_agent_enrollments import router as admin_agent_enrollments_router
from app.api.admin_bootstrap_jobs import router as admin_bootstrap_jobs_router
from app.api.admin_provider_accounts import router as admin_provider_accounts_router
from app.api.admin_resource_targets import router as admin_resource_targets_router
from app.api.agent_enrollments import router as agent_enrollments_router
from app.api.agent_bootstrap import router as agent_bootstrap_router
from app.api.agent_bootstrap_enrollments import router as agent_bootstrap_enrollments_router
from app.api.agent_observations import router as agent_observations_router
from app.api.candidates import router as candidates_router
from app.api.health import router as health_router
from app.api.reservations import router as reservations_router
from app.api.scheduler_auth import (
    SchedulerAuthHttpError,
    scheduler_auth_http_error_handler,
)
from app.services.bootstrap_worker import run_bootstrap_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = asyncio.Event()
    worker = asyncio.create_task(run_bootstrap_worker(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await worker


def create_app() -> FastAPI:
    app = FastAPI(
        title="MSG Broker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.add_exception_handler(
        AdminAuthHttpError,
        admin_auth_http_error_handler,
    )
    app.add_exception_handler(
        SchedulerAuthHttpError,
        scheduler_auth_http_error_handler,
    )
    app.include_router(admin_auth_router)
    app.include_router(admin_agent_enrollments_router)
    app.include_router(admin_bootstrap_jobs_router)
    app.include_router(admin_provider_accounts_router)
    app.include_router(admin_resource_targets_router)
    app.include_router(agent_enrollments_router)
    app.include_router(agent_bootstrap_router)
    app.include_router(agent_bootstrap_enrollments_router)
    app.include_router(agent_observations_router)
    app.include_router(candidates_router)
    app.include_router(reservations_router)
    app.include_router(health_router)

    @app.get(
        "/v1/admin/openapi.json",
        include_in_schema=False,
        dependencies=[Depends(require_admin)],
    )
    def admin_openapi() -> JSONResponse:
        return JSONResponse(app.openapi())

    return app


app = create_app()
