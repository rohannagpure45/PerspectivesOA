"""Deterministic Joint Commission CTS audit engine.

Reads :mod:`app.intelligence.tjc.rules.yaml`, walks the curated set of CTS
Elements of Performance, and emits a per-EP verdict (``pass`` / ``fail`` /
``insufficient_data``) with a citation-bearing rationale.

Inputs: ``PatientExtract``. No external dependencies, no LLM, no clock.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PatientExtract

_RULES_PATH = Path(__file__).resolve().parent / "rules.yaml"

Verdict = Literal["pass", "fail", "insufficient_data"]


class AuditEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str | None = None
    span: str
    matched_phrase: str | None = None


class AuditFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: str
    ep: str
    title: str
    verdict: Verdict
    rationale: str
    evidence: list[AuditEvidence] = Field(default_factory=list)


class AuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int
    failed: int
    insufficient_data: int


class TjcAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str
    rules_version: str
    findings: list[AuditFinding]
    summary: AuditSummary
    computed_at: datetime


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, Any]:
    with _RULES_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if s.startswith('r"') and s.endswith('"'):
        return s[2:-1]
    if s.startswith("r'") and s.endswith("'"):
        return s[2:-1]
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _excerpt(text: str, start: int, end: int, width: int = 80) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    return re.sub(r"\s+", " ", text[a:b].strip())


def _admission_text(extract: PatientExtract) -> tuple[str, str | None]:
    if not extract.admission_assessment:
        return "", None
    return (
        extract.admission_assessment.raw_text or "",
        extract.admission_assessment.source_note_id,
    )


# ---------------------------------------------------------------------------
# Per-target evaluators
# ---------------------------------------------------------------------------
def _eval_admission_patterns(
    rule: dict[str, Any], extract: PatientExtract
) -> tuple[Verdict, str, list[AuditEvidence]]:
    text, note_id = _admission_text(extract)
    if not text:
        return ("insufficient_data", "No admission assessment is on file.", [])

    matches: list[AuditEvidence] = []
    if "evidence" in rule:
        all_ok = True
        for raw in rule["evidence"]:
            pat = re.compile(_strip_quotes(raw), re.IGNORECASE)
            m = pat.search(text)
            if m:
                matches.append(
                    AuditEvidence(
                        note_id=note_id,
                        matched_phrase=m.group(0),
                        span=_excerpt(text, m.start(), m.end()),
                    )
                )
            else:
                all_ok = False
        if all_ok:
            return ("pass", str(rule.get("pass_rationale_template", "")).strip(), matches)
        return (
            "fail",
            str(rule.get("failure_rationale_template", "Required evidence not found.")).strip(),
            matches,
        )

    if "any_of" in rule:
        for group in rule["any_of"]:
            group_patterns = group.get("patterns") or []
            local_matches: list[AuditEvidence] = []
            ok = True
            for raw in group_patterns:
                pat = re.compile(_strip_quotes(raw), re.IGNORECASE)
                m = pat.search(text)
                if not m:
                    ok = False
                    break
                local_matches.append(
                    AuditEvidence(
                        note_id=note_id,
                        matched_phrase=m.group(0),
                        span=_excerpt(text, m.start(), m.end()),
                    )
                )
            if ok:
                return ("pass", str(rule.get("pass_rationale_template", "")).strip(), local_matches)
        return (
            "fail",
            str(rule.get("failure_rationale_template", "No alternative pattern matched.")).strip(),
            [],
        )

    return ("insufficient_data", "Rule has no evidence patterns configured.", [])


def _eval_diagnoses_and_dtp(
    rule: dict[str, Any], extract: PatientExtract
) -> tuple[Verdict, str, list[AuditEvidence]]:
    diagnoses_present = bool(extract.diagnoses)
    dtp_present = any(e.type == "diagnosis_treatment_plan" for e in extract.timeline)
    if diagnoses_present and dtp_present:
        evidence: list[AuditEvidence] = []
        for d in extract.diagnoses:
            evidence.append(
                AuditEvidence(
                    note_id=None,
                    matched_phrase=f"{d.code} - {d.description or ''}".strip(" -"),
                    span=f"Diagnosis recorded: {d.code} {d.description or ''}".strip(),
                )
            )
        return (
            "pass",
            str(rule.get("pass_rationale_template", "")).strip(),
            evidence,
        )
    return (
        "fail",
        str(rule.get("failure_rationale_template", "")).strip(),
        [],
    )


def _eval_dtp_goal(rule: dict[str, Any], extract: PatientExtract) -> tuple[Verdict, str, list[AuditEvidence]]:
    dtp_entries = [e for e in extract.timeline if e.type == "diagnosis_treatment_plan"]
    if not dtp_entries:
        return (
            "insufficient_data",
            "No DiagnosisTreatmentPlan record on file.",
            [],
        )
    entry = dtp_entries[0]
    goal = entry.metadata.get("dtp_goal")
    formatted_goal = entry.metadata.get("dtp_formatted_goal")
    dtp_id = entry.metadata.get("dtp_resource_id")

    def _has_text(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    goal_present = _has_text(goal) or _has_text(formatted_goal)
    if goal_present:
        return (
            "pass",
            str(rule.get("pass_rationale_template", "")).strip(),
            [
                AuditEvidence(
                    note_id=f"DTP/{dtp_id}" if dtp_id else entry.note_id,
                    span=f"goal={goal!r}; formattedGoal={formatted_goal!r}",
                    matched_phrase="goal",
                )
            ],
        )
    span = json.dumps({"goal": goal, "formattedGoal": formatted_goal})
    return (
        "fail",
        str(rule.get("failure_rationale_template", "")).strip(),
        [
            AuditEvidence(
                note_id=f"DTP/{dtp_id}" if dtp_id else entry.note_id,
                span=span,
                matched_phrase=None,
            )
        ],
    )


def _eval_timeline_measures(
    rule: dict[str, Any], extract: PatientExtract
) -> tuple[Verdict, str, list[AuditEvidence]]:
    measures = [e for e in extract.timeline if e.type == "scored_measure"]
    if len(measures) >= 2:
        evidence = [
            AuditEvidence(
                note_id=m.note_id,
                span=f"{m.title} on {m.date.isoformat() if m.date else 'unknown date'}",
                matched_phrase=m.title,
            )
            for m in measures
        ]
        return (
            "pass",
            str(rule.get("pass_rationale_template", "")).strip(),
            evidence,
        )
    return (
        "fail",
        str(rule.get("failure_rationale_template", "")).strip(),
        [
            AuditEvidence(
                note_id=m.note_id,
                span=f"{m.title} on {m.date.isoformat() if m.date else 'unknown date'}",
                matched_phrase=m.title,
            )
            for m in measures
        ],
    )


_TARGET_DISPATCH = {
    "admission_assessment": _eval_admission_patterns,
    "diagnoses_and_dtp": _eval_diagnoses_and_dtp,
    "dtp": _eval_dtp_goal,
    "timeline_measures": _eval_timeline_measures,
}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class TjcEngine:
    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self.rules = rules or _load_rules()

    def audit(self, extract: PatientExtract) -> TjcAuditReport:
        findings: list[AuditFinding] = []
        for rule in self.rules.get("rules", []) or []:
            target = rule.get("target")
            evaluator = _TARGET_DISPATCH.get(target)
            if evaluator is None:
                findings.append(
                    AuditFinding(
                        standard=str(rule.get("standard", "?")),
                        ep=str(rule.get("ep", "?")),
                        title=str(rule.get("title", "")),
                        verdict="insufficient_data",
                        rationale=f"Unknown rule target {target!r}.",
                    )
                )
                continue
            verdict, rationale, evidence = evaluator(rule, extract)
            findings.append(
                AuditFinding(
                    standard=str(rule.get("standard", "?")),
                    ep=str(rule.get("ep", "?")),
                    title=str(rule.get("title", "")),
                    verdict=verdict,
                    rationale=rationale or "",
                    evidence=evidence,
                )
            )
        summary = AuditSummary(
            passed=sum(1 for f in findings if f.verdict == "pass"),
            failed=sum(1 for f in findings if f.verdict == "fail"),
            insufficient_data=sum(1 for f in findings if f.verdict == "insufficient_data"),
        )
        return TjcAuditReport(
            framework=str(self.rules.get("framework", "TJC CTS")),
            rules_version=str(self.rules.get("version", "tjc-rules")),
            findings=findings,
            summary=summary,
            computed_at=datetime.now(tz=UTC),
        )


__all__ = [
    "AuditFinding",
    "AuditSummary",
    "TjcAuditReport",
    "TjcEngine",
]
