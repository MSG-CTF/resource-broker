from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db_session


router = APIRouter(tags=["health"])


@router.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "UP"}


@router.get("/health/ready")
def health_ready(
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "DOWN"},
        ) from error
    return {"status": "UP"}
