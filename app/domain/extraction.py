"""Build the canonical ``PatientExtract`` from a SimplePractice backend.

Pipeline:

1. Resolve ``hashed_id`` -> ``numeric_id`` via ``treatable-clients``.
2. Fetch ``/clients/{numeric_id}`` for demographics + contacts.
3. Fetch ``/overview-items?filter[clientId]=...`` for the entire timeline
   (it returns a ``data`` ordered newest-first, plus an ``included`` array
   carrying every progress / psychotherapy / chart note body that the
   timeline cards reference).
4. For each appointment in ``data``, also fetch ``/appointments/{id}`` so
   we get the full progress + psychotherapy + DTP payload (the timeline
   ``included`` already has a Progress note for each appointment, so the
   per-appointment fetch is mostly belt-and-suspenders + DTP enrichment).
5. Walk every note we have collected to:
     - identify the BPS Admission Assessment (a ``thisType: "Chart"`` note
       whose body starts with ``Admission Assessment``);
     - classify each progress note's format (SOAP / DAP / DSAP / etc.) by
       the markdown ``###`` headings present in the body;
     - parse the diagnosis-treatment-plan ``structure`` JSON for diagnoses;
     - collapse all entries into a date-ordered ``TimelineEntry`` list.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, date, datetime, time
from typing import Any

from dateutil import parser as dateparser

from app.domain.models import (
    Address,
    AdmissionAssessment,
    Contact,
    Diagnosis,
    InitialRiskScreening,
    MeasuredScore,
    PatientExtract,
    PatientProfile,
    ProgressNote,
    ProgressNoteFormat,
    ProgressNoteSections,
    PsychotherapyNote,
    TimelineEntry,
)
from app.simplepractice.client import SimplePracticeBackend
from app.simplepractice.fixture_backend import FixtureBackend
from app.simplepractice.jsonapi import Document, Resource

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"(?im)^\s*###\s+([A-Za-z][A-Za-z &/]+?)\s*$", re.MULTILINE)


def _extract_headings(text: str) -> list[str]:
    return [m.group(1).strip().lower() for m in _HEADING_RE.finditer(text or "")]


def _slugify(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")


_FORMAT_RECIPES: list[tuple[ProgressNoteFormat, list[str]]] = [
    ("DSAP", ["data", "subjective", "assessment", "plan"]),
    ("SOAP", ["subjective", "objective", "assessment", "plan"]),
    ("DAP", ["data", "assessment", "plan"]),
    ("DAP", ["data", "assessment_and_response", "plan"]),
    ("BIRP", ["behavior", "intervention", "response", "plan"]),
    ("PIRP", ["problem", "intervention", "response", "plan"]),
]


def classify_progress_note_format(text: str) -> ProgressNoteFormat:
    headings = [_slugify(h) for h in _extract_headings(text)]
    if not headings:
        return "Free"
    # The DAP template SimplePractice ships uses "Assessment and Response" —
    # treat that as the assessment slot.
    headings_set = set(headings)
    for fmt, recipe in _FORMAT_RECIPES:
        if all(any(slot in h for h in headings_set) for slot in recipe):
            return fmt
    return "Unknown"


def _split_sections(text: str) -> dict[str, str]:
    """Return ``{slug: body}`` for each ``### Heading`` block in the note text."""
    if not text:
        return {}
    matches = list(_HEADING_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        slug = _slugify(m.group(1))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # Strip leading echo of the heading (the SP UI sometimes prints the
        # heading word again at the start of the body).
        first_line, _, rest = body.partition("\n")
        if first_line.strip().lower() == m.group(1).strip().lower() and rest.strip():
            body = rest.strip()
        sections[slug] = body
    return sections


def _build_progress_sections(slug_map: dict[str, str]) -> ProgressNoteSections:
    canonical = {
        "subjective": ["subjective"],
        "objective": ["objective"],
        "assessment": ["assessment", "assessment_and_response"],
        "plan": ["plan"],
        "data": ["data"],
        "response": ["response"],
        "behavior": ["behavior", "behaviour"],
        "intervention": ["intervention"],
        "problem": ["problem"],
    }
    out: dict[str, Any] = {"other": {}}
    used: set[str] = set()
    for canon, aliases in canonical.items():
        for alias in aliases:
            if alias in slug_map:
                out[canon] = slug_map[alias]
                used.add(alias)
                break
    for slug, body in slug_map.items():
        if slug not in used:
            out["other"][slug] = body
    return ProgressNoteSections(**out)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------
def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except (ValueError, TypeError):
        return None


def _parse_date(s: str | None) -> date | None:
    dt = _parse_dt(s)
    return dt.date() if dt else None


def _age_from_dob(dob: date | None) -> int | None:
    if not dob:
        return None
    today = datetime.now(tz=UTC).date()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(years, 0)


# ---------------------------------------------------------------------------
# Patient profile
# ---------------------------------------------------------------------------
def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("true", "yes", "1"):
            return True
        if value.lower() in ("false", "no", "0"):
            return False
    return None


def _format_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw


def build_patient_profile(
    hashed_id: str,
    treatable: Document,
    client_doc: Document,
) -> PatientProfile:
    treatable_attrs = treatable.primary().attributes
    client = client_doc.primary()
    client_attrs = client.attributes

    numeric_id = treatable.primary().id

    # phones: prefer the client_doc relationships (rich), fall back to defaultPhone.
    primary_phone: str | None = None
    primary_email: str | None = None
    address: Address | None = None
    contacts: list[Contact] = []

    phone_refs = client.rel_refs("phones")
    for ref in phone_refs:
        res = client_doc.included.by_ref(ref)
        if res:
            number = _coerce_str(res.attr("number"))
            if number:
                primary_phone = primary_phone or _format_phone(number)
                break

    email_refs = client.rel_refs("emails")
    for ref in email_refs:
        res = client_doc.included.by_ref(ref)
        if res:
            addr = _coerce_str(res.attr("address"))
            if addr:
                primary_email = primary_email or addr
                break

    addr_refs = client.rel_refs("addresses")
    for ref in addr_refs:
        res = client_doc.included.by_ref(ref)
        if res:
            address = Address(
                line1=_coerce_str(res.attr("street")),
                city=_coerce_str(res.attr("city")),
                state=_coerce_str(res.attr("state")),
                postal_code=_coerce_str(res.attr("zip")),
            )
            break

    # Contacts: SimplePractice exposes ``clientRelationships`` resources in
    # ``included`` that point back to the primary client and forward to a
    # ``clientContacts`` resource. The primary client doesn't always carry a
    # forward relationship, so iterate ``included`` directly.
    relationship_resources: list[Resource] = client_doc.included.of_type("clientRelationships")
    for rel_res in relationship_resources:
        client_back_ref = rel_res.rel_ref("client")
        if client_back_ref and client_back_ref[1] != client.id:
            continue  # belongs to a different client (shouldn't happen in our flow)
        relationship = _coerce_str(rel_res.attr("relationshipName"))
        emergency = _coerce_bool(rel_res.attr("emergencyContact")) or False
        related_ref = rel_res.rel_ref("relatedClient")
        contact_name: str | None = None
        contact_phone: str | None = None
        contact_email: str | None = None
        if related_ref:
            related = client_doc.included.by_ref(related_ref)
            if related:
                contact_name = _coerce_str(related.attr("name") or related.attr("preferredName"))
                for pref in related.rel_refs("phones"):
                    pres = client_doc.included.by_ref(pref)
                    if pres:
                        n = _coerce_str(pres.attr("number"))
                        if n:
                            contact_phone = _format_phone(n)
                            break
                if not contact_phone:
                    contact_phone = _format_phone(_coerce_str(related.attr("defaultPhoneNumber")))
                for eref in related.rel_refs("emails"):
                    eres = client_doc.included.by_ref(eref)
                    if eres:
                        addr = _coerce_str(eres.attr("address"))
                        if addr:
                            contact_email = addr
                            break
                if not contact_email:
                    contact_email = _coerce_str(related.attr("defaultEmailAddress"))
        contacts.append(
            Contact(
                name=contact_name,
                relationship=relationship.replace("_", " ").title() if relationship else None,
                phone=contact_phone,
                email=contact_email,
                is_emergency=emergency,
            )
        )

    # measured_scores from treatable-client.attributes.measuredScores. The
    # SimplePractice payload encodes this as a JSON-stringified array, so
    # decode it first when needed.
    measured: list[MeasuredScore] = []
    raw_scores = treatable_attrs.get("measuredScores")
    if isinstance(raw_scores, str):
        try:
            raw_scores = json.loads(raw_scores)
        except (TypeError, ValueError):
            raw_scores = []
    for entry in raw_scores or []:
        if not isinstance(entry, dict):
            continue
        measured.append(
            MeasuredScore(
                note_id=_coerce_str(entry.get("noteId")),
                title=_coerce_str(entry.get("title")) or "Measure",
                score=entry.get("score"),
                max_score=entry.get("maxScore"),
                severity=_coerce_str(entry.get("severityState")),
                administered_at=_parse_dt(entry.get("createdAt")),
            )
        )

    dob = _parse_date(treatable_attrs.get("birthDate") or client_attrs.get("birthDate"))
    name = _coerce_str(treatable_attrs.get("name")) or _coerce_str(client_attrs.get("name")) or "Unknown"

    if not primary_phone:
        primary_phone = _format_phone(_coerce_str(treatable_attrs.get("defaultPhoneNumber")))
    if not primary_email:
        primary_email = _coerce_str(treatable_attrs.get("defaultEmailAddress"))

    return PatientProfile(
        hashed_id=hashed_id,
        numeric_id=str(numeric_id),
        name=name,
        preferred_name=_coerce_str(treatable_attrs.get("preferredName")),
        legal_name=_coerce_str(treatable_attrs.get("legalName")),
        status=_coerce_str(treatable_attrs.get("status")),
        dob=dob,
        age_years=_age_from_dob(dob),
        sex=_coerce_str(treatable_attrs.get("sex")),
        is_minor=_coerce_bool(treatable_attrs.get("isMinor")),
        phone=primary_phone,
        email=primary_email,
        address=address,
        contacts=contacts,
        measured_scores=measured,
    )


# ---------------------------------------------------------------------------
# BPS Admission Assessment detection + parsing
# ---------------------------------------------------------------------------
_BPS_HEADERS = [
    "history of present illness",
    "substance use history",
    "initial risk screening",
    "biopsychosocial",
    "treatment readiness",
]


def _looks_like_bps(text: str) -> bool:
    if not text:
        return False
    lower = text.lstrip().lower()
    if lower.startswith("admission assessment"):
        return True
    matches = sum(1 for h in _BPS_HEADERS if h in lower)
    return matches >= 3


_BPS_SECTION_RE = re.compile(
    r"^(?P<title>[A-Z][A-Za-z /]+?(?:Illness|History|Screening|Environment|Risk|Readiness|Ideation))\s*:\s*",
    re.MULTILINE,
)


def _split_bps_sections(text: str) -> dict[str, str]:
    """Walk a BPS body and return ``{section: body}``.

    BPS notes use ``Title: ...`` patterns rather than markdown headings, with
    nested ``Subtitle:`` lines under the Initial Risk Screening section.
    """
    out: dict[str, str] = {}
    matches = list(_BPS_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        slug = _slugify(m.group("title"))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        out[slug] = body
    return out


def _build_initial_risk_screening(
    sections: dict[str, str],
) -> InitialRiskScreening | None:
    """Construct the IRS sub-record from the BPS section map.

    The BPS regex splits ``Initial Risk Screening:`` and each of its
    sub-sections (``Suicidal/Homicidal Ideation:``, ``Medical Risk:``,
    ``Fall Risk:``, ``Living Environment:``, ``Treatment Readiness:``) as
    siblings. We rebuild the structured IRS by picking the sub-section
    slugs out of the section map.
    """
    keys = (
        "suicidal_homicidal_ideation",
        "medical_risk",
        "fall_risk",
        "living_environment",
        "treatment_readiness",
    )
    parts = {k: sections.get(k) for k in keys if sections.get(k)}
    if not parts:
        return None
    return InitialRiskScreening(
        si_hi=parts.get("suicidal_homicidal_ideation"),
        medical_risk=parts.get("medical_risk"),
        fall_risk=parts.get("fall_risk"),
        living_environment=parts.get("living_environment"),
        treatment_readiness=parts.get("treatment_readiness"),
    )


def build_admission_assessment(note: Resource) -> AdmissionAssessment:
    text = (note.attr("text") or "").strip()
    sections = _split_bps_sections(text)
    irs = _build_initial_risk_screening(sections)

    # The SP title attribute is "Chart Note" for BPS chart notes — pull a more
    # informative title from the first non-empty line of the body when the
    # attribute is a generic placeholder.
    title = _coerce_str(note.attr("title"))
    if not title or title.lower() in {"chart note", "note", "chart"}:
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
        if first_line:
            title = first_line
    if not title:
        title = "Admission Assessment"

    return AdmissionAssessment(
        source_note_id=note.id,
        noted_at=_parse_dt(note.attr("notedAt")),
        author=_coerce_str(note.attr("updatedBy")),
        title=title,
        sections=sections,
        initial_risk_screening=irs,
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Progress / psychotherapy notes
# ---------------------------------------------------------------------------
def build_progress_note(note: Resource) -> ProgressNote:
    text = note.attr("text") or ""
    fmt = classify_progress_note_format(text)
    sections_map = _split_sections(text)
    return ProgressNote(
        note_id=note.id,
        title=_coerce_str(note.attr("title")),
        format=fmt,
        body_markdown=text,
        sections=_build_progress_sections(sections_map),
        noted_at=_parse_dt(note.attr("notedAt")),
        author=_coerce_str(note.attr("updatedBy")),
    )


def build_psychotherapy_note(note: Resource) -> PsychotherapyNote:
    return PsychotherapyNote(
        note_id=note.id,
        title=_coerce_str(note.attr("title")),
        body=note.attr("text") or "",
        noted_at=_parse_dt(note.attr("notedAt")),
    )


# ---------------------------------------------------------------------------
# Diagnosis Treatment Plan
# ---------------------------------------------------------------------------
def _diagnoses_from_dtp(dtp: Resource) -> list[Diagnosis]:
    structure = dtp.attr("structure")
    if isinstance(structure, str):
        try:
            structure = json.loads(structure)
        except (TypeError, ValueError):
            structure = None
    if not isinstance(structure, dict):
        return []
    out: list[Diagnosis] = []
    for entry in structure.get("diagnoses", []) or []:
        if not isinstance(entry, dict):
            continue
        code = _coerce_str(entry.get("code"))
        if not code:
            continue
        out.append(Diagnosis(code=code, description=_coerce_str(entry.get("description"))))
    return out


# ---------------------------------------------------------------------------
# Timeline assembly
# ---------------------------------------------------------------------------
def _resolve_appointment_in_overview(
    overview: Document, appointment_id: str, appointment_doc: Document | None
) -> Resource | None:
    for r in overview.primary_list():
        if r.type == "appointments" and r.id == appointment_id:
            return r
    if appointment_doc:
        return appointment_doc.primary()
    return None


def _link_for_intake(note_id: str, hashed_id: str, intake_id: str | None) -> str | None:
    if not intake_id:
        return None
    return f"/clients/{hashed_id}/intake_notes/{intake_id}"


async def build_patient_extract(
    backend: SimplePracticeBackend,
    hashed_id: str,
    *,
    page_size: int = 20,
) -> PatientExtract:
    treatable_doc = await _fetch_treatable(backend, hashed_id)
    fixture_hash = treatable_doc.primary().attr("hashedId")
    if fixture_hash and str(fixture_hash) != hashed_id:
        raise FileNotFoundError(f"No client found for hashed_id={hashed_id}")
    numeric_id = treatable_doc.primary().id or await backend.resolve_hashed_id(hashed_id)
    client_doc = await backend.get_client(numeric_id)
    overview = await backend.get_overview_items(numeric_id, page_size=page_size)

    profile = build_patient_profile(hashed_id, treatable_doc, client_doc)

    appointment_docs: dict[str, Document] = {}
    for r in overview.primary_list():
        if r.type == "appointments" and r.id and r.id not in appointment_docs:
            try:
                appointment_docs[r.id] = await backend.get_appointment(r.id)
            except FileNotFoundError as exc:
                log.warning("Missing fixture for appointment %s: %s", r.id, exc)

    # Build a unified pool of all notes we have access to.
    all_notes: dict[str, Resource] = {}
    for res in overview.all_resources():
        if res.type == "notes" and res.id:
            all_notes[res.id] = res
    for doc in appointment_docs.values():
        for res in doc.all_resources():
            if res.type == "notes" and res.id and res.id not in all_notes:
                all_notes[res.id] = res

    # Locate BPS admission assessment.
    bps_note: Resource | None = None
    for note in all_notes.values():
        if note.attr("thisType") == "Chart" and _looks_like_bps(note.attr("text") or ""):
            # Prefer the longest body in case there are multiple chart notes.
            if bps_note is None or len(note.attr("text") or "") > len(bps_note.attr("text") or ""):
                bps_note = note
    admission = build_admission_assessment(bps_note) if bps_note else None

    # Diagnoses from DTP resources (timeline included).
    diagnoses: list[Diagnosis] = []
    for r in overview.included.of_type("diagnosisTreatmentPlans"):
        diagnoses.extend(_diagnoses_from_dtp(r))
    # de-duplicate by code while preserving order
    seen_codes: set[str] = set()
    uniq_diag: list[Diagnosis] = []
    for d in diagnoses:
        if d.code not in seen_codes:
            seen_codes.add(d.code)
            uniq_diag.append(d)
    diagnoses = uniq_diag
    profile = profile.model_copy(update={"diagnoses": diagnoses})

    # Build timeline.
    timeline: list[TimelineEntry] = []
    seen_note_ids: set[str] = set()

    for resource in overview.primary_list():
        if resource.type == "appointments":
            apt_doc = appointment_docs.get(resource.id)
            apt_resource = _resolve_appointment_in_overview(overview, resource.id, apt_doc)
            if apt_resource is None:
                continue
            timeline.append(_appointment_entry(apt_resource, apt_doc, all_notes, seen_note_ids))
        elif resource.type == "notes":
            note_entry = _note_entry(resource, hashed_id, overview, bps_note, seen_note_ids)
            if note_entry is not None:
                timeline.append(note_entry)

    # Sort newest first (start time / noted_at / date) but keep structure if equal.
    def _entry_sort_key(e: TimelineEntry) -> tuple[float, int]:
        ts: datetime | None = e.start or _entry_dt(e)
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (-(ts.timestamp() if ts else 0.0), 0)

    timeline.sort(key=_entry_sort_key)

    source: str = "fixture" if isinstance(backend, FixtureBackend) else "live"

    return PatientExtract(
        patient=profile,
        admission_assessment=admission,
        timeline=timeline,
        diagnoses=diagnoses,
        extracted_at=datetime.now(tz=UTC),
        source=source,
    )


def _entry_dt(entry: TimelineEntry) -> datetime | None:
    if entry.start:
        return entry.start
    if entry.progress_note and entry.progress_note.noted_at:
        return entry.progress_note.noted_at
    if entry.psychotherapy_note and entry.psychotherapy_note.noted_at:
        return entry.psychotherapy_note.noted_at
    if entry.date:
        return datetime.combine(entry.date, time.max, tzinfo=UTC)
    return None


async def _fetch_treatable(backend: SimplePracticeBackend, hashed_id: str) -> Document:
    """Backend-agnostic fetch of the treatable-client document.

    Live ``SimplePracticeClient`` exposes the underlying call via
    ``resolve_hashed_id`` but doesn't return the doc. ``FixtureBackend``
    has the file already. We hit the same path on both.
    """
    # Both backends have ``_fetch_treatable_doc``? No — call resolve_hashed_id
    # for live, and read the fixture for offline. Simplest: have the backend
    # implement a resolve+return. We add a small adapter:
    if isinstance(backend, FixtureBackend):
        return backend._load("treatable-client.json")
    # Live: the client method ``resolve_hashed_id`` returns just the id.
    # Re-issue the call here using its private ``_api_get``.
    from app.simplepractice.client import SimplePracticeClient

    if isinstance(backend, SimplePracticeClient):
        return await backend._api_get(
            f"/frontend/treatable-clients/{hashed_id}",
            params={"filter[findByHashedId]": "true"},
        )
    raise TypeError(f"Unknown backend type: {type(backend).__name__}")


# ---------------------------------------------------------------------------
# Per-resource timeline entry builders
# ---------------------------------------------------------------------------
def _appointment_entry(
    apt: Resource,
    apt_doc: Document | None,
    all_notes: dict[str, Resource],
    seen_note_ids: set[str],
) -> TimelineEntry:
    start = _parse_dt(apt.attr("startTime"))
    end = _parse_dt(apt.attr("endTime"))
    cpt = apt.attr("cptCodes") or []
    billing_code: str | None = None
    if isinstance(cpt, list) and cpt:
        first = cpt[0]
        if isinstance(first, dict):
            billing_code = _coerce_str(first.get("code"))

    progress: ProgressNote | None = None
    psychotherapy: PsychotherapyNote | None = None

    progress_ref = apt.rel_ref("progressNote")
    if progress_ref and progress_ref[1] in all_notes:
        note = all_notes[progress_ref[1]]
        progress = build_progress_note(note)
        seen_note_ids.add(note.id)

    psych_ref = apt.rel_ref("psychotherapyNote")
    if psych_ref and psych_ref[1] in all_notes:
        note = all_notes[psych_ref[1]]
        psychotherapy = build_psychotherapy_note(note)
        seen_note_ids.add(note.id)

    return TimelineEntry(
        date=start.date() if start else None,
        type="appointment",
        title=_coerce_str(apt.attr("title")),
        appointment_id=apt.id,
        billing_code=billing_code,
        start=start,
        end=end,
        progress_note=progress,
        psychotherapy_note=psychotherapy,
        metadata={
            "attendance_status": apt.attr("attendanceStatus"),
            "duration_minutes": apt.attr("duration"),
            "rank_num": apt.attr("rankNum"),
        },
    )


def _note_entry(
    note: Resource,
    hashed_id: str,
    overview: Document,
    bps_note: Resource | None,
    seen_note_ids: set[str],
) -> TimelineEntry | None:
    if note.id in seen_note_ids:
        return None
    seen_note_ids.add(note.id)

    this_type = note.attr("thisType")
    is_measure = note.attr("isMeasure")
    if isinstance(is_measure, str):
        is_measure = is_measure.lower() == "true"

    noted_at = _parse_dt(note.attr("notedAt"))
    metadata: dict[str, Any] = {
        "author": note.attr("updatedBy"),
        "is_measure": bool(is_measure) if is_measure is not None else False,
    }

    if this_type == "IntakeNote":
        intake_ref = note.rel_ref("notable")
        intake_id = intake_ref[1] if intake_ref else None
        return TimelineEntry(
            date=noted_at.date() if noted_at else None,
            type="scored_measure",
            title=_coerce_str(note.attr("title")) or "Measure",
            note_id=note.id,
            metadata=metadata,
            link=_link_for_intake(note.id, hashed_id, intake_id),
        )

    if this_type == "DiagnosisTreatmentPlan":
        notable_ref = note.rel_ref("notable")
        dtp_resource = overview.included.by_ref(notable_ref)
        if notable_ref:
            metadata["dtp_resource_id"] = notable_ref[1]
        if dtp_resource:
            metadata.update(
                {
                    "dtp_goal": dtp_resource.attr("goal"),
                    "dtp_formatted_goal": dtp_resource.attr("formattedGoal"),
                    "dtp_objective": dtp_resource.attr("objective"),
                    "dtp_formatted_objective": dtp_resource.attr("formattedObjective"),
                }
            )
        return TimelineEntry(
            date=noted_at.date() if noted_at else None,
            type="diagnosis_treatment_plan",
            title=_coerce_str(note.attr("title")) or "Diagnosis and treatment plan",
            note_id=note.id,
            body=note.attr("text"),
            metadata=metadata,
        )

    if this_type == "Chart":
        if bps_note is not None and note.id == bps_note.id:
            return TimelineEntry(
                date=noted_at.date() if noted_at else None,
                type="admission_assessment_ref",
                title=_coerce_str(note.attr("title")) or "Admission Assessment",
                note_id=note.id,
                metadata=metadata,
            )
        return TimelineEntry(
            date=noted_at.date() if noted_at else None,
            type="chart_note",
            title=_coerce_str(note.attr("title")) or "Chart Note",
            note_id=note.id,
            body=note.attr("text"),
            metadata=metadata,
        )

    if this_type == "Psychotherapy":
        return TimelineEntry(
            date=noted_at.date() if noted_at else None,
            type="psychotherapy_note",
            title=_coerce_str(note.attr("title")) or "Psychotherapy Note",
            note_id=note.id,
            body=note.attr("text"),
            metadata=metadata,
        )

    # Progress notes that are attached to an appointment have already been
    # captured via _appointment_entry; include any orphans as their own entry.
    if this_type == "Progress":
        notable_ref = note.rel_ref("notable")
        if notable_ref and notable_ref[0] == "appointments":
            return None
        return TimelineEntry(
            date=noted_at.date() if noted_at else None,
            type="chart_note",
            title=_coerce_str(note.attr("title")) or "Progress Note",
            note_id=note.id,
            body=note.attr("text"),
            metadata=metadata,
        )

    return None
