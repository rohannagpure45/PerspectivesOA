"""Task 3 — Clinical Intelligence endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.extraction import BackendDep, _get_extract
from app.api.schemas import AuditRequest
from app.db.session import write_asam_audit, write_tjc_audit
from app.intelligence.asam import AsamAssessment, AsamEngine
from app.intelligence.tjc import TjcAuditReport, TjcEngine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/patients", tags=["intelligence"])

_asam_engine = AsamEngine()
_tjc_engine = TjcEngine()


@router.post("/{hashed_id}/asam", response_model=AsamAssessment)
async def post_asam(
    hashed_id: str,
    backend: BackendDep,
    request: AuditRequest | None = None,
) -> AsamAssessment:
    extract = await _get_extract(backend, hashed_id, refresh=False)
    if extract.admission_assessment is None and not extract.timeline:
        raise HTTPException(status_code=422, detail="No clinical text available to score.")
    assessment = _asam_engine.assess(extract)
    if request and not request.include_text_spans:
        for d in assessment.dimensions:
            d.evidence = []
    try:
        await write_asam_audit(hashed_id, assessment.model_dump(mode="json"))
    except Exception:
        log.exception("ASAM audit cache write failed")
    return assessment


@router.post("/{hashed_id}/tjc-audit", response_model=TjcAuditReport)
async def post_tjc_audit(
    hashed_id: str,
    backend: BackendDep,
    request: AuditRequest | None = None,
) -> TjcAuditReport:
    extract = await _get_extract(backend, hashed_id, refresh=False)
    report = _tjc_engine.audit(extract)
    if request and not request.include_text_spans:
        for f in report.findings:
            f.evidence = []
    try:
        await write_tjc_audit(hashed_id, report.model_dump(mode="json"))
    except Exception:
        log.exception("TJC audit cache write failed")
    return report
