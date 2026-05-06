"""TJC CTS audit tests against Jamie's chart."""

from __future__ import annotations

import pytest

from app.api import intelligence as intelligence_api
from app.domain.extraction import build_patient_extract
from app.domain.models import PatientExtract
from app.intelligence.tjc import TjcEngine
from app.simplepractice import FixtureBackend


async def test_jamie_tjc_findings(fixture_backend: FixtureBackend) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    report = TjcEngine().audit(extract)

    by_key = {(f.standard, f.ep): f for f in report.findings}

    # Date of assessment is documented -> CTS.02.01.01 EP1 passes.
    assert by_key[("CTS.02.01.01", "EP1")].verdict == "pass"

    # Spiritual/cultural assessment domain is missing -> EP2 fails.
    spirit = by_key[("CTS.02.02.01", "EP2")]
    assert spirit.verdict == "fail"
    assert "spiritual" in spirit.rationale.lower()

    # Substance use quantity/frequency documented -> EP4 passes.
    assert by_key[("CTS.02.02.01", "EP4")].verdict == "pass"

    # Diagnoses + DTP both present -> CTS.03.01.01 EP1 passes.
    assert by_key[("CTS.03.01.01", "EP1")].verdict == "pass"

    # DTP goal is null in SP -> CTS.03.01.03 EP2 fails.
    goal = by_key[("CTS.03.01.03", "EP2")]
    assert goal.verdict == "fail"
    assert "goal" in goal.rationale.lower()
    assert goal.evidence
    assert goal.evidence[0].note_id == "DTP/44188318"
    assert '"goal": null' in goal.evidence[0].span
    assert '"formattedGoal": null' in goal.evidence[0].span

    # SI/HI screened in BPS -> CTS.04.03.01 EP1 passes.
    assert by_key[("CTS.04.03.01", "EP1")].verdict == "pass"

    # Lethal-means inquiry documented (firearms) -> CTS.04.03.05 EP1 passes.
    assert by_key[("CTS.04.03.05", "EP1")].verdict == "pass"

    # Two GAD-7 administrations -> CTS.05.01.01 EP3 passes.
    assert by_key[("CTS.05.01.01", "EP3")].verdict == "pass"

    # Summary counters add up.
    assert report.summary.passed == 6
    assert report.summary.failed == 2
    assert report.summary.insufficient_data == 0


async def test_tjc_endpoint(client) -> None:
    r = await client.post("/api/v1/patients/0c39dadff6972e0f/tjc-audit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["framework"].startswith("TJC")
    assert body["summary"]["passed"] >= 5
    failed_titles = [f["title"] for f in body["findings"] if f["verdict"] == "fail"]
    assert any("spiritual" in t.lower() or "cultural" in t.lower() for t in failed_titles)


async def test_tjc_endpoint_refreshes_extraction_by_default(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls: list[bool] = []

    async def fake_get_extract(*args, refresh: bool) -> PatientExtract:
        calls.append(refresh)
        return extract

    async def noop_write_tjc_audit(hashed_id: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(intelligence_api, "_get_extract", fake_get_extract)
    monkeypatch.setattr(intelligence_api, "write_tjc_audit", noop_write_tjc_audit)

    r = await client.post("/api/v1/patients/0c39dadff6972e0f/tjc-audit")

    assert r.status_code == 200, r.text
    assert calls == [True]


async def test_tjc_endpoint_allows_cached_extraction_override(
    client,
    fixture_backend: FixtureBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extract = await build_patient_extract(fixture_backend, "0c39dadff6972e0f")
    calls: list[bool] = []

    async def fake_get_extract(*args, refresh: bool) -> PatientExtract:
        calls.append(refresh)
        return extract

    async def noop_write_tjc_audit(hashed_id: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(intelligence_api, "_get_extract", fake_get_extract)
    monkeypatch.setattr(intelligence_api, "write_tjc_audit", noop_write_tjc_audit)

    r = await client.post("/api/v1/patients/0c39dadff6972e0f/tjc-audit?refresh=false")

    assert r.status_code == 200, r.text
    assert calls == [False]
