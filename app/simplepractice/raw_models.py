"""Pydantic models that mirror SimplePractice JSON:API attributes.

These exist so that the rest of the application can consume validated
attribute dicts instead of raw-typed ``dict[str, Any]``. They intentionally
duplicate only the fields we use today; everything else is allowed via
``model_config={'extra': 'allow'}`` so SimplePractice schema drift is
tolerated.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class TreatableClientAttrs(_Permissive):
    hashed_id: str = Field(alias="hashedId")
    name: str | None = None
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    middle_name: str | None = Field(default=None, alias="middleName")
    preferred_name: str | None = Field(default=None, alias="preferredName")
    birth_date: str | None = Field(default=None, alias="birthDate")
    status: str | None = None
    sex: str | None = None
    default_phone_number: str | None = Field(default=None, alias="defaultPhoneNumber")
    default_email_address: str | None = Field(default=None, alias="defaultEmailAddress")
    is_minor: bool | None = Field(default=None, alias="isMinor")
    measured_scores: list[dict[str, Any]] | None = Field(default=None, alias="measuredScores")


class EmailAttrs(_Permissive):
    address: str | None = None
    this_type: str | None = Field(default=None, alias="thisType")


class PhoneAttrs(_Permissive):
    number: str | None = None
    this_type: str | None = Field(default=None, alias="thisType")


class AddressAttrs(_Permissive):
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None


class NoteAttrs(_Permissive):
    this_type: str | None = Field(default=None, alias="thisType")
    title: str | None = None
    text: str | None = None
    formatted_text: str | None = Field(default=None, alias="formattedText")
    noted_at: str | None = Field(default=None, alias="notedAt")
    is_measure: bool | str | None = Field(default=None, alias="isMeasure")
    updated_by: str | None = Field(default=None, alias="updatedBy")


class AppointmentAttrs(_Permissive):
    title: str | None = None
    start_time: str | None = Field(default=None, alias="startTime")
    end_time: str | None = Field(default=None, alias="endTime")
    duration: int | None = None
    attendance_status: str | None = Field(default=None, alias="attendanceStatus")
    cpt_codes: list[dict[str, Any]] | None = Field(default=None, alias="cptCodes")
    rank_num: int | None = Field(default=None, alias="rankNum")


class DiagnosisTreatmentPlanAttrs(_Permissive):
    structure: dict[str, Any] | None = None
    goal: str | None = None
    formatted_goal: str | None = Field(default=None, alias="formattedGoal")
    objective: str | None = None
    formatted_objective: str | None = Field(default=None, alias="formattedObjective")
    problem: str | None = None
    noted_at: str | None = Field(default=None, alias="notedAt")
