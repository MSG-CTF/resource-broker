import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domain.enums import BootstrapJobStatus
from app.services.bootstrap_artifacts import (
    BootstrapArtifactNotFoundError,
    artifact_for_version,
)
from app.services.bootstrap_job_service import (
    BootstrapJobService,
    BootstrapResourceTargetNotFoundError,
    render_job_runner,
)


router = APIRouter(prefix="/v1/agent", tags=["agent-bootstrap"])
_ARTIFACT = re.compile(
    r"^msg-broker-node-agent-bootstrap-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz$"
)


@router.get(
    "/bootstrap-artifacts/{filename}",
    response_class=FileResponse,
    response_model=None,
    responses={status.HTTP_404_NOT_FOUND: {}},
)
def download_bootstrap_artifact(filename: str) -> FileResponse | Response:
    match = _ARTIFACT.fullmatch(filename)
    if match is None:
        return Response(status_code=404)
    try:
        artifact = artifact_for_version(match.group("version"))
    except BootstrapArtifactNotFoundError:
        return Response(status_code=404)
    return FileResponse(
        artifact.path,
        media_type="application/gzip",
        filename=filename,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get(
    "/bootstrap-jobs/{job_id}/runner.sh",
    response_class=PlainTextResponse,
    response_model=None,
    responses={status.HTTP_404_NOT_FOUND: {}},
)
def download_bootstrap_runner(
    job_id: UUID,
    session: Session = Depends(get_db_session),
) -> PlainTextResponse | Response:
    try:
        job = BootstrapJobService(session).get(job_id)
        if (
            job.status
            not in {BootstrapJobStatus.APPLYING, BootstrapJobStatus.RUNNING}
            or job.deadline_at <= datetime.now(UTC)
        ):
            return Response(status_code=404)
        script = render_job_runner(job)
    except (BootstrapResourceTargetNotFoundError, BootstrapArtifactNotFoundError):
        return Response(status_code=404)
    return PlainTextResponse(
        script,
        media_type="text/x-shellscript",
        headers={"Cache-Control": "private, no-store"},
    )
