"""Extraction service tests pinned against the real HAR fixture."""

from __future__ import annotations

from datetime import UTC, datetime, time

import httpx
import pytest

from app.api import extraction as extraction_api
from app.domain.extraction import build_patient_extract
from app.domain.models import PatientExtract
from app.settings import Settings
from app.simplepractice import FixtureBackend
from app.simplepractice.client import SimplePracticeClient


async def test_extract_returns_jamie_profile(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")

    assert extract.patient.hashed_id == "0c39dadff6972e0f"
    assert extract.patient.numeric_id == "106612410"
    assert extract.patient.name == "Jamie D. Appleseed"
    assert extract.patient.preferred_name == "Jamie D. Appleseed"
    assert extract.patient.email == "nagpure.r@northeastern.edu"
    assert extract.patient.phone == "(609) 375-6850"
    assert extract.patient.address is not None
    assert extract.patient.address.line1 == "123 Main Street"
    assert extract.patient.address.city == "Anytown"
    assert extract.patient.address.state == "CA"

    # Family member contact card is present.
    assert any(c.relationship and "Family" in c.relationship for c in extract.patient.contacts)

    # Diagnoses come from the DTP `structure` json.
    codes = [d.code for d in extract.diagnoses]
    assert codes == ["F41.9", "F43.22"]

    # Measured scores from treatable-client.measuredScores.
    titles = [m.title for m in extract.patient.measured_scores]
    assert "GAD-7" in titles


async def test_admission_assessment_detected_and_parsed(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")

    bps = extract.admission_assessment
    assert bps is not None
    assert bps.source_note_id == "925838931"
    assert "Biopsychosocial" in bps.title or "Admission" in bps.title
    assert "History of Present Illness" in bps.raw_text
    assert "Substance Use History" in bps.raw_text

    # Sections are split.
    assert "history_of_present_illness" in bps.sections
    assert "substance_use_history" in bps.sections
    assert "initial_risk_screening" in bps.sections
    irs = bps.initial_risk_screening
    assert irs is not None
    assert irs.fall_risk and irs.fall_risk.lower().startswith("low")
    assert irs.living_environment and "supportive partner" in irs.living_environment.lower()
    assert irs.si_hi and "denies" in irs.si_hi.lower()


async def test_progress_note_format_classifier(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")

    appointments = {e.appointment_id: e for e in extract.timeline if e.type == "appointment"}
    assert set(appointments) == {"3505428529", "3505428542", "3505428553"}

    formats = {apt_id: e.progress_note.format for apt_id, e in appointments.items() if e.progress_note}
    # 3505428542 -> SOAP, 3505428553 -> DAP, 3505428529 -> DSAP (Data/Subjective/Assessment/Plan)
    assert formats["3505428542"] == "SOAP"
    assert formats["3505428553"] == "DAP"
    assert formats["3505428529"] == "DSAP"


async def test_timeline_ordering_and_kinds(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")

    assert len(extract.timeline) >= 7
    assert extract.timeline[0].type == "scored_measure"
    assert extract.timeline[0].note_id == "925740429"

    # Timeline is ordered newest-first across appointment starts, note timestamps, and date-only entries.
    def entry_timestamp(entry) -> datetime:
        if entry.start:
            dt = entry.start
        elif entry.progress_note and entry.progress_note.noted_at:
            dt = entry.progress_note.noted_at
        elif entry.psychotherapy_note and entry.psychotherapy_note.noted_at:
            dt = entry.psychotherapy_note.noted_at
        elif entry.date:
            dt = datetime.combine(entry.date, time.max, tzinfo=UTC)
        else:
            dt = datetime.min.replace(tzinfo=UTC)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    timestamps = [entry_timestamp(e) for e in extract.timeline]
    assert timestamps == sorted(timestamps, reverse=True)

    types = [e.type for e in extract.timeline]
    assert "appointment" in types
    assert "scored_measure" in types
    assert "chart_note" in types or "admission_assessment_ref" in types


async def test_extract_endpoint_returns_full_document(client) -> None:
    r = await client.get("/api/v1/patients/0c39dadff6972e0f/extract")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patient"]["name"] == "Jamie D. Appleseed"
    assert body["admission_assessment"]["source_note_id"] == "925838931"
    apt_count = sum(1 for e in body["timeline"] if e["type"] == "appointment")
    assert apt_count == 3


async def test_fixture_backend_rejects_unknown_hash(client) -> None:
    r = await client.get("/api/v1/patients/not-the-real-id/extract?refresh=true")
    assert r.status_code == 404
    assert "not-the-real-id" in r.json()["detail"]


async def test_fixture_backend_rejects_mismatched_numeric_id(fixture_backend: FixtureBackend) -> None:
    with pytest.raises(FileNotFoundError):
        await fixture_backend.get_client("999999999")


async def test_extract_endpoint_uses_cache_when_refresh_false(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    cached_payload = cached_extract.model_dump(mode="json")
    calls = {"read": 0, "build": 0}

    async def fake_read_latest_extraction(hashed_id: str) -> dict:
        calls["read"] += 1
        assert hashed_id == "0c39dadff6972e0f"
        return cached_payload

    async def fail_build_patient_extract(*args, **kwargs) -> PatientExtract:
        calls["build"] += 1
        raise AssertionError("refresh=false should serve a valid cached extraction")

    monkeypatch.setattr(extraction_api, "read_latest_extraction", fake_read_latest_extraction)
    monkeypatch.setattr(extraction_api, "build_patient_extract", fail_build_patient_extract)

    r = await client.get("/api/v1/patients/0c39dadff6972e0f/extract")

    assert r.status_code == 200, r.text
    assert r.json()["patient"]["name"] == "Jamie D. Appleseed"
    assert calls == {"read": 1, "build": 0}


async def test_extract_endpoint_bypasses_cache_when_refresh_true(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls = {"read": 0, "build": 0, "write": 0}

    async def fail_read_latest_extraction(hashed_id: str) -> dict | None:
        calls["read"] += 1
        raise AssertionError("refresh=true should not read the cache")

    async def fake_build_patient_extract(*args, **kwargs) -> PatientExtract:
        calls["build"] += 1
        return fresh_extract

    async def fake_write_extraction(hashed_id: str, payload: dict) -> None:
        calls["write"] += 1
        assert hashed_id == "0c39dadff6972e0f"
        assert payload["patient"]["name"] == "Jamie D. Appleseed"

    monkeypatch.setattr(extraction_api, "read_latest_extraction", fail_read_latest_extraction)
    monkeypatch.setattr(extraction_api, "build_patient_extract", fake_build_patient_extract)
    monkeypatch.setattr(extraction_api, "write_extraction", fake_write_extraction)

    r = await client.get("/api/v1/patients/0c39dadff6972e0f/extract?refresh=true")

    assert r.status_code == 200, r.text
    assert r.json()["patient"]["name"] == "Jamie D. Appleseed"
    assert calls == {"read": 0, "build": 1, "write": 1}


async def test_demographics_endpoint_can_bypass_cache(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls = {"read": 0, "build": 0}

    async def fail_read_latest_extraction(hashed_id: str) -> dict | None:
        calls["read"] += 1
        raise AssertionError("refresh=true should not read the cache")

    async def fake_build_patient_extract(*args, **kwargs) -> PatientExtract:
        calls["build"] += 1
        return fresh_extract

    async def noop_write_extraction(hashed_id: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(extraction_api, "read_latest_extraction", fail_read_latest_extraction)
    monkeypatch.setattr(extraction_api, "build_patient_extract", fake_build_patient_extract)
    monkeypatch.setattr(extraction_api, "write_extraction", noop_write_extraction)

    r = await client.get("/api/v1/patients/0c39dadff6972e0f/demographics?refresh=true")

    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Jamie D. Appleseed"
    assert calls == {"read": 0, "build": 1}


async def test_simplepractice_client_merges_overview_pages() -> None:
    calls: list[int] = []

    def page_payload(page_number: int) -> dict:
        if page_number == 1:
            return {
                "data": [
                    {"type": "notes", "id": "n1", "attributes": {"title": "First"}},
                    {"type": "appointments", "id": "a1", "attributes": {"startTime": "2026-05-05T10:00:00Z"}},
                ],
                "included": [{"type": "notes", "id": "inc1", "attributes": {"title": "Included 1"}}],
            }
        if page_number == 2:
            return {
                "data": [
                    {"type": "appointments", "id": "a1", "attributes": {"startTime": "2026-05-05T10:00:00Z"}},
                    {"type": "notes", "id": "n2", "attributes": {"title": "Second"}},
                ],
                "included": [
                    {"type": "notes", "id": "inc1", "attributes": {"title": "Included 1 duplicate"}},
                    {"type": "notes", "id": "inc2", "attributes": {"title": "Included 2"}},
                ],
            }
        return {"data": [], "included": []}

    def handler(request: httpx.Request) -> httpx.Response:
        page_number = int(request.url.params.get("page[number]", "1"))
        calls.append(page_number)
        return httpx.Response(
            200,
            json=page_payload(page_number),
            headers={"content-type": "application/vnd.api+json"},
        )

    settings = Settings(sp_session_cookie="session")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.sp_base_url,
        headers={
            "Accept": "application/vnd.api+json",
            "api-version": settings.sp_api_version,
        },
    ) as client:
        sp = SimplePracticeClient(settings, client=client)
        sp._csrf_token = "csrf-for-test"
        doc = await sp.get_overview_items("106612410", page_size=2)

    assert calls == [1, 2, 3]
    assert [(r.type, r.id) for r in doc.primary_list()] == [
        ("notes", "n1"),
        ("appointments", "a1"),
        ("notes", "n2"),
    ]
    assert doc.included.get("notes", "inc1") is not None
    assert doc.included.get("notes", "inc2") is not None
