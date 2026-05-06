"""Sanity-check the JSON:API document/index helpers against real HAR fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from app.simplepractice.jsonapi import Document


def test_overview_items_indexes_included(fixture_dir: Path) -> None:
    raw = json.loads((fixture_dir / "overview-items.json").read_text())
    doc = Document.from_dict(raw)

    assert isinstance(doc.data, list)
    assert len(doc.data) == 8

    # Primary entries are a mix of notes + appointments.
    types = {res.type for res in doc.primary_list()}
    assert types == {"notes", "appointments"}

    # Unified find resolves to the BPS chart note (primary data, not included).
    bps = doc.find("notes", "925838931")
    assert bps is not None
    assert bps.attr("thisType") == "Chart"
    assert "Admission Assessment" in (bps.attr("text") or "")

    # SOAP/DAP/DSAP progress notes are accessible by id (in included).
    soap = doc.find("notes", "925740422")
    assert soap is not None
    assert "Subjective" in (soap.attr("text") or "")

    dap = doc.find("notes", "925826179")
    assert dap is not None
    assert "Data" in (dap.attr("text") or "")

    dsap = doc.find("notes", "925740420")
    assert dsap is not None
    assert "data" in (dsap.attr("text") or "").lower()


def test_relationship_traversal(fixture_dir: Path) -> None:
    raw = json.loads((fixture_dir / "overview-items.json").read_text())
    doc = Document.from_dict(raw)

    # Find an appointment in primary data and confirm progressNote rel resolves.
    apt = next(r for r in doc.primary_list() if r.type == "appointments" and r.id == "3505428542")
    progress_ref = apt.rel_ref("progressNote")
    assert progress_ref == ("notes", "925740422")
    progress_note = doc.included.by_ref(progress_ref)
    assert progress_note is not None
    assert "SOAP" in (progress_note.attr("title") or "")
