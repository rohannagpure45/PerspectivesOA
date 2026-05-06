"""Extraction service tests pinned against the real HAR fixture."""

from __future__ import annotations

from app.domain.extraction import build_patient_extract
from app.simplepractice import FixtureBackend


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
    # Timeline is ordered newest-first.
    dates = [e.start or e.progress_note.noted_at if e.progress_note else None for e in extract.timeline]
    timestamps = [d for d in dates if d]
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
