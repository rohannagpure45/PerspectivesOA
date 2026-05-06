"""ASAM scoring snapshot test against Jamie D. Appleseed's chart."""

from __future__ import annotations

import pytest

from app.api import intelligence as intelligence_api
from app.domain.extraction import build_patient_extract
from app.domain.models import PatientExtract
from app.intelligence.asam import AsamEngine
from app.simplepractice import FixtureBackend


async def test_jamie_asam_dimension_ratings(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    assessment = AsamEngine().assess(extract)

    by_id = {d.id: d for d in assessment.dimensions}
    assert tuple(by_id[i].risk_rating for i in range(1, 7)) == (2, 0, 3, 2, 2, 1)

    # Mild withdrawal documented in BPS + SOAP -> Dim 1 = mild risk.
    assert by_id[1].risk_rating >= 1
    assert any(
        "morning tremor" in e.matched_phrase.lower() or "physiological dependence" in e.matched_phrase.lower()
        for e in by_id[1].evidence
    )

    # No acute biomedical -> Dim 2 protective dominates.
    assert by_id[2].risk_rating == 0

    # Daily panic attacks + co-occurring GAD/AUD + functional impairment -> Dim 3 high.
    assert by_id[3].risk_rating >= 3

    # Strong desire to change but ambivalent -> Dim 4 moderate (1 or 2).
    assert by_id[4].risk_rating <= 2

    # Two failed cut-down attempts + intense cravings -> Dim 5 elevated.
    assert by_id[5].risk_rating >= 2

    # Supportive (but strained) partner, no firearms -> Dim 6 in low/protective range.
    assert by_id[6].risk_rating <= 2


async def test_jamie_asam_recommends_iop(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    assessment = AsamEngine().assess(extract)

    loc = assessment.recommended_level_of_care
    # ASAM Level 2.1 (IOP) is what the SOAP note itself recommends.
    assert loc.code == "2.1"
    assert "IOP" in loc.name or "Intensive Outpatient" in loc.name


async def test_asam_endpoint_returns_evidence(client) -> None:
    r = await client.post("/api/v1/patients/0c39dadff6972e0f/asam", json={"include_text_spans": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asam_edition"] == "4th"
    assert len(body["dimensions"]) == 6
    assert body["recommended_level_of_care"]["code"] == "2.1"
    # At least one dimension must cite the BPS chart note for evidence quality.
    any_bps_cite = any(
        any(ev.get("source_note_id") == "925838931" for ev in dim.get("evidence", []))
        for dim in body["dimensions"]
    )
    assert any_bps_cite, "expected at least one ASAM citation against the BPS note"


async def test_asam_endpoint_refreshes_extraction_by_default(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls: list[bool] = []

    async def fake_get_extract(*args, refresh: bool) -> PatientExtract:
        calls.append(refresh)
        return extract

    async def noop_write_asam_audit(hashed_id: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(intelligence_api, "_get_extract", fake_get_extract)
    monkeypatch.setattr(intelligence_api, "write_asam_audit", noop_write_asam_audit)

    r = await client.post("/api/v1/patients/0c39dadff6972e0f/asam")

    assert r.status_code == 200, r.text
    assert calls == [True]


async def test_asam_endpoint_allows_cached_extraction_override(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls: list[bool] = []

    async def fake_get_extract(*args, refresh: bool) -> PatientExtract:
        calls.append(refresh)
        return extract

    async def noop_write_asam_audit(hashed_id: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(intelligence_api, "_get_extract", fake_get_extract)
    monkeypatch.setattr(intelligence_api, "write_asam_audit", noop_write_asam_audit)

    r = await client.post("/api/v1/patients/0c39dadff6972e0f/asam?refresh=false")

    assert r.status_code == 200, r.text
    assert calls == [False]
