"""TJC CTS audit tests against Jamie's chart."""

from __future__ import annotations

from app.domain.extraction import build_patient_extract
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
