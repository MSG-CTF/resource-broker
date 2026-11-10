from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import require_admin
from app.db.session import get_db_session
from app.db.tables import GcpCreditObservationTable
from app.schemas.provider_account import (
    ErrorResponse,
    GcpBillingAccountId,
    GcpCreditObservationListResponse,
    GcpCreditObservationResponse,
    GcpCreditObservationUpdateRequest,
)
from app.services.gcp_credit_observation_service import GcpCreditObservationService


router = APIRouter(
    prefix="/v1/admin/gcp-credit-observations",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _response(row: GcpCreditObservationTable) -> GcpCreditObservationResponse:
    return GcpCreditObservationResponse(
        billing_account_id=row.billing_account_id,
        amount=row.amount,
        observed_at=row.observed_at,
    )


def _database_error(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error={"code": "DATABASE_ERROR", "message": message}
        ).model_dump(mode="json"),
    )


@router.get(
    "",
    response_model=GcpCreditObservationListResponse,
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def list_gcp_credit_observations(
    session: Session = Depends(get_db_session),
) -> GcpCreditObservationListResponse | JSONResponse:
    try:
        rows = GcpCreditObservationService(session).list_all()
    except SQLAlchemyError:
        session.rollback()
        return _database_error("The GCP credit observations could not be loaded.")
    return GcpCreditObservationListResponse(items=[_response(row) for row in rows])


@router.put(
    "/{billing_account_id}",
    response_model=GcpCreditObservationResponse,
    responses={
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def save_gcp_credit_observation(
    billing_account_id: GcpBillingAccountId,
    request: GcpCreditObservationUpdateRequest,
    session: Session = Depends(get_db_session),
) -> GcpCreditObservationResponse | JSONResponse:
    try:
        row = GcpCreditObservationService(session).save(
            billing_account_id, request.amount
        )
    except SQLAlchemyError:
        session.rollback()
        return _database_error("The GCP credit observation could not be saved.")
    return _response(row)
