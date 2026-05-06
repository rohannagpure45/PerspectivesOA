"""Task 2 — Data Extraction API."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.db.session import read_latest_extraction, write_extraction
from app.domain.extraction import build_patient_extract
from app.domain.models import (
    AdmissionAssessment,
    PatientExtract,
    PatientProfile,
    TimelineEntry,
)
from app.settings import get_settings
from app.simplepractice.client import SimplePracticeBackend

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/patients", tags=["extraction"])


async def _get_extract(
    backend: SimplePracticeBackend,
    hashed_id: str,
    refresh: bool,
) -> PatientExtract:
    if not refresh:
        try:
            cached = await read_latest_extraction(hashed_id)
            if cached is not None:
                return PatientExtract.model_validate(cached)
        except Exception:
            log.exception("Failed to read cached extraction (continuing)")

    try:
        extract = await build_patient_extract(
            backend,
            hashed_id,
            page_size=get_settings().sp_overview_page_size,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Best-effort cache write — never block the response on DB failures.
    try:
        await write_extraction(hashed_id, extract.model_dump(mode="json"))
    except Exception:
        log.exception("Failed to persist extraction (continuing)")

    return extract


def get_backend_dep() -> SimplePracticeBackend:  # pragma: no cover — replaced in main
    raise RuntimeError("Backend dependency not configured")


BackendDep = Annotated[SimplePracticeBackend, Depends(get_backend_dep)]


@router.get("/{hashed_id}/extract", response_model=PatientExtract)
async def get_extract(
    hashed_id: str,
    backend: BackendDep,
    refresh: bool = Query(default=False, description="Bypass cache and re-fetch from SP"),
) -> PatientExtract:
    return await _get_extract(backend, hashed_id, refresh)


@router.get("/{hashed_id}/demographics", response_model=PatientProfile)
async def get_demographics(hashed_id: str, backend: BackendDep) -> PatientProfile:
    extract = await _get_extract(backend, hashed_id, refresh=False)
    return extract.patient


@router.get("/{hashed_id}/admission-assessment")
async def get_admission_assessment(hashed_id: str, backend: BackendDep) -> AdmissionAssessment:
    extract = await _get_extract(backend, hashed_id, refresh=False)
    if not extract.admission_assessment:
        raise HTTPException(
            status_code=404,
            detail="No admission assessment chart note found for this patient",
        )
    return extract.admission_assessment


@router.get("/{hashed_id}/timeline", response_model=list[TimelineEntry])
async def get_timeline(hashed_id: str, backend: BackendDep) -> list[TimelineEntry]:
    extract = await _get_extract(backend, hashed_id, refresh=False)
    return extract.timeline


__all__ = ["BackendDep", "_get_extract", "get_backend_dep", "router"]


# Default no-op response so /api/v1 is browsable.
@router.get("/", include_in_schema=False)
async def _index() -> JSONResponse:
    return JSONResponse({"detail": "use /api/v1/patients/{hashed_id}/extract"})
