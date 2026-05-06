"""Pydantic domain models — the canonical shape returned by ``/extract``."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Aliases keep field names like ``date`` and ``datetime`` legible without
# shadowing the imported types.
date = _dt.date
datetime = _dt.datetime

ProgressNoteFormat = Literal["SOAP", "DAP", "DSAP", "BIRP", "PIRP", "Free", "Unknown"]
TimelineEntryType = Literal[
    "appointment",
    "scored_measure",
    "chart_note",
    "diagnosis_treatment_plan",
    "admission_assessment_ref",
    "psychotherapy_note",
    "intake_form",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


class Address(_Frozen):
    line1: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None


class Contact(_Frozen):
    name: str | None = None
    relationship: str | None = None
    phone: str | None = None
    email: str | None = None
    is_emergency: bool = False


class Diagnosis(_Frozen):
    code: str
    description: str | None = None


class MeasuredScore(_Frozen):
    note_id: str | None = None
    title: str
    score: int | float | None = None
    max_score: int | float | None = None
    severity: str | None = None
    administered_at: datetime | None = None


class PatientProfile(_Frozen):
    hashed_id: str
    numeric_id: str
    name: str
    preferred_name: str | None = None
    legal_name: str | None = None
    status: str | None = None
    dob: _dt.date | None = None
    age_years: int | None = None
    sex: str | None = None
    is_minor: bool | None = None
    phone: str | None = None
    email: str | None = None
    address: Address | None = None
    contacts: list[Contact] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    measured_scores: list[MeasuredScore] = Field(default_factory=list)


class InitialRiskScreening(_Frozen):
    si_hi: str | None = None
    medical_risk: str | None = None
    fall_risk: str | None = None
    living_environment: str | None = None
    treatment_readiness: str | None = None


class AdmissionAssessment(_Frozen):
    source_note_id: str
    noted_at: datetime | None = None
    author: str | None = None
    title: str
    sections: dict[str, Any]
    initial_risk_screening: InitialRiskScreening | None = None
    raw_text: str


class ProgressNoteSections(_Frozen):
    subjective: str | None = None
    objective: str | None = None
    assessment: str | None = None
    plan: str | None = None
    data: str | None = None
    response: str | None = None
    behavior: str | None = None
    intervention: str | None = None
    problem: str | None = None
    other: dict[str, str] = Field(default_factory=dict)


class PsychotherapyNote(_Frozen):
    note_id: str
    title: str | None = None
    body: str
    noted_at: datetime | None = None


class ProgressNote(_Frozen):
    note_id: str
    title: str | None = None
    format: ProgressNoteFormat
    body_markdown: str
    sections: ProgressNoteSections
    noted_at: datetime | None = None
    author: str | None = None


class TimelineEntry(_Frozen):
    date: _dt.date | None = None
    type: TimelineEntryType
    title: str | None = None
    note_id: str | None = None
    appointment_id: str | None = None
    billing_code: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    progress_note: ProgressNote | None = None
    psychotherapy_note: PsychotherapyNote | None = None
    body: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    link: str | None = None


class PatientExtract(_Frozen):
    patient: PatientProfile
    admission_assessment: AdmissionAssessment | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    extracted_at: datetime
    source: Literal["live", "fixture"] = "fixture"
