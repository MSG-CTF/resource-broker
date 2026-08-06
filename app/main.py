from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.api.admin_auth import (
    AdminAuthHttpError,
    admin_auth_http_error_handler,
    require_admin,
    router as admin_auth_router,
)
from app.api.admin_agent_enrollments import router as admin_agent_enrollments_router
from app.api.admin_provider_accounts import router as admin_provider_accounts_router
from app.api.admin_resource_targets import router as admin_resource_targets_router
from app.api.agent_enrollments import router as agent_enrollments_router
from app.api.agent_observations import router as agent_observations_router
from app.api.candidates import router as candidates_router
from app.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="MSG Broker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_exception_handler(
        AdminAuthHttpError,
        admin_auth_http_error_handler,
    )
    app.include_router(admin_auth_router)
    app.include_router(admin_agent_enrollments_router)
    app.include_router(admin_provider_accounts_router)
    app.include_router(admin_resource_targets_router)
    app.include_router(agent_enrollments_router)
    app.include_router(agent_observations_router)
    app.include_router(candidates_router)
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
