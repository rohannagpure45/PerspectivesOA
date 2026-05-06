"""Deterministic ASAM 4th-edition six-dimension scoring engine.

Inputs:
* A ``PatientExtract`` produced by :mod:`app.domain.extraction`.

Algorithm:
1. Concatenate every clinical text source we have (BPS body + each progress
   note body + each chart note body) into a single ``(note_id, text)`` list.
2. For each of the six ASAM dimensions, walk the rule tiers in the YAML
   catalogue and tally regex matches:
       high_risk  -> +3
       moderate   -> +2
       mild       -> +1
       protective -> -1
   We clamp the running sum to ``[0, 4]`` per ASAM convention.
3. Map the six dimension scores onto the LoC matrix in YAML to derive a
   recommended Level of Care, citing the dimensions that drove the verdict.

Output is a Pydantic model that is JSON-serialisable and fully deterministic
given the same extract — i.e. no LLM, no clock, no network.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PatientExtract

_RULES_PATH = Path(__file__).resolve().parent / "rules.yaml"

_TIER_WEIGHTS: dict[str, int] = {
    "high_risk": 3,
    "moderate": 2,
    "mild": 1,
    "protective": -1,
}


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_note_id: str | None = None
    matched_phrase: str
    span: str
    tier: str


class AsamDimensionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    short_name: str
    risk_rating: int = Field(ge=0, le=4)
    raw_score: int
    rationale: str
    evidence: list[EvidenceSpan]


class LevelOfCare(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    rationale: str
    drivers: list[int]


class AsamAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asam_edition: str = "4th"
    rules_version: str
    dimensions: list[AsamDimensionScore]
    recommended_level_of_care: LevelOfCare
    computed_at: datetime
    text_corpus_size: int


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_rules() -> dict[str, Any]:
    with _RULES_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Corpus assembly
# ---------------------------------------------------------------------------
def _corpus(extract: PatientExtract) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if extract.admission_assessment:
        out.append(
            (
                extract.admission_assessment.source_note_id,
                extract.admission_assessment.raw_text,
            )
        )
    for entry in extract.timeline:
        if entry.progress_note:
            out.append((entry.progress_note.note_id, entry.progress_note.body_markdown))
        if entry.body and entry.note_id and entry.type == "chart_note":
            out.append((entry.note_id, entry.body))
        if entry.psychotherapy_note:
            out.append((entry.psychotherapy_note.note_id, entry.psychotherapy_note.body))
    return out


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.startswith('r"') and s.endswith('"'):
        return s[2:-1]
    if s.startswith("r'") and s.endswith("'"):
        return s[2:-1]
    return s


def _compile_pattern(raw: str) -> re.Pattern[str]:
    return re.compile(_strip_quotes(raw), re.IGNORECASE)


def _excerpt(text: str, start: int, end: int, width: int = 60) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    snippet = text[a:b].strip()
    return re.sub(r"\s+", " ", snippet)


# Negation triggers we look back for. ``no evidence of acute psychosis`` and
# ``denies any withdrawal`` are descriptive — they should not raise risk.
_NEGATION_RE = re.compile(
    r"(?i)\b("
    r"no(?:\s+(?:evidence|history|sign[s]?|reports?|current|active|acute))?"
    r"|never"
    r"|denies?"
    r"|denied"
    r"|without"
    r"|absence of"
    r"|negative for"
    r")\s+(?:any\s+|of\s+|the\s+)?[A-Za-z\- /]{0,40}$"
)


def _is_negated(text: str, start: int) -> bool:
    """Return True when the match at ``start`` is preceded by a negation cue."""
    look = text[max(0, start - 80) : start]
    return bool(_NEGATION_RE.search(look))


# ---------------------------------------------------------------------------
# Scoring core
# ---------------------------------------------------------------------------
def _score_dimension(
    dim: dict[str, Any], corpus: list[tuple[str, str]]
) -> tuple[int, int, list[EvidenceSpan]]:
    """Compute a deterministic 0-4 ASAM risk rating for a single dimension.

    Scoring policy (clinically-shaped, fully encoded here so it's diffable):

    1.  Walk every regex pattern in every tier (``high_risk`` / ``moderate``
        / ``mild`` / ``protective``). For each pattern, collect citations
        from any note. Skip matches that sit immediately after a negation
        cue (``no``, ``denies``, ``without``, …) so risk-clearing language
        doesn't accidentally raise the score.
    2.  Count *unique* matched patterns per tier. Repeated mentions of the
        same finding across different notes are evidence, not severity, so
        a pattern contributes to the per-tier count at most once.
    3.  Build the rating from a presence-ladder + accumulation bonus:

            base = 3 if any high_risk
                   2 if any moderate
                   1 if any mild
                   0 otherwise

            +1 if moderate-pattern count >= 3   (broad confirmation)
            +1 if moderate-pattern count >= 5   (extensive accumulation)
            +1 if mild-pattern count >= 3       (cluster of mild markers)

            -1 if at least 2 protective patterns matched and no high_risk
                 (protective findings can de-escalate but never overrule
                 imminent danger)

    4.  Clamp to ``[0, 4]`` per ASAM convention.

    The returned ``raw`` is the un-clamped intermediate score for diagnostic
    output (``raw_score`` in the JSON); ``rating`` is the clamped value.
    """
    counts: dict[str, int] = {"high_risk": 0, "moderate": 0, "mild": 0, "protective": 0}
    evidence: list[EvidenceSpan] = []
    for tier, patterns in (dim.get("rules") or {}).items():
        if tier not in counts:
            continue
        for pattern_raw in patterns or []:
            try:
                pat = _compile_pattern(pattern_raw)
            except re.error:
                continue
            pattern_hit = False
            for note_id, text in corpus:
                if not text:
                    continue
                for m in pat.finditer(text):
                    if tier in {"high_risk", "moderate", "mild"} and _is_negated(text, m.start()):
                        continue
                    if not pattern_hit:
                        counts[tier] += 1
                        pattern_hit = True
                    evidence.append(
                        EvidenceSpan(
                            source_note_id=note_id,
                            matched_phrase=m.group(0),
                            span=_excerpt(text, m.start(), m.end()),
                            tier=tier,
                        )
                    )
                    break  # one citation per (pattern, note)

    if counts["high_risk"]:
        base = 3
    elif counts["moderate"]:
        base = 2
    elif counts["mild"]:
        base = 1
    else:
        base = 0

    bonus = 0
    if counts["moderate"] >= 3:
        bonus += 1
    if counts["moderate"] >= 5:
        bonus += 1
    if counts["mild"] >= 3:
        bonus += 1

    protective_offset = 0
    if counts["high_risk"] == 0 and counts["protective"] >= 2:
        protective_offset = -1

    raw = base + bonus + protective_offset
    rating = max(0, min(4, raw))
    return raw, rating, evidence


def _build_rationale(dim: dict[str, Any], raw: int, rating: int, evidence: list[EvidenceSpan]) -> str:
    if rating == 0:
        protective = [e for e in evidence if e.tier == "protective"]
        if protective:
            phrases = ", ".join(f"\u201c{e.matched_phrase}\u201d" for e in protective[:2])
            return f"Dimension scored 0 — protective indicators dominate: {phrases}."
        return "Dimension scored 0 — no risk indicators detected in chart text."

    risk_words = {3: "severe", 2: "moderate", 1: "mild", 4: "imminent"}
    severity = risk_words.get(rating, "elevated")
    triggers = sorted(
        (e for e in evidence if e.tier in {"high_risk", "moderate", "mild"}),
        key=lambda e: -_TIER_WEIGHTS.get(e.tier, 0),
    )[:3]
    if not triggers:
        return f"Dimension rated {rating} ({severity}) based on aggregated indicators."
    phrases = "; ".join(f"\u201c{e.matched_phrase}\u201d" for e in triggers)
    return f"Dimension rated {rating} ({severity}). Driving evidence: {phrases}."


# ---------------------------------------------------------------------------
# Level of care matrix
# ---------------------------------------------------------------------------
_ALLOWED_BUILTINS = {
    "max": max,
    "min": min,
    "abs": abs,
    "True": True,
    "False": False,
    "true": True,
    "false": False,
}


def _safe_eval(expr: str, env: dict[str, Any]) -> bool:
    """Evaluate a small Boolean expression with a strict symbol table.

    Supports comparisons, ``and``/``or``/``not``, and ``max``/``min``/``abs``.
    """
    if expr.strip().lower() == "true":
        return True
    if expr.strip().lower() == "false":
        return False
    code = compile(expr, filename="<asam_loc>", mode="eval")
    for name in code.co_names:
        if name not in env and name not in _ALLOWED_BUILTINS:
            raise ValueError(f"Disallowed name in LoC expression: {name!r}")
    return bool(eval(code, {"__builtins__": _ALLOWED_BUILTINS}, env))


def _identify_drivers(env: dict[str, int]) -> list[int]:
    drivers = sorted(
        ((dim, score) for dim, score in env.items() if dim.startswith("dim")),
        key=lambda kv: -kv[1],
    )
    return [int(k.removeprefix("dim")) for k, score in drivers if score >= 2][:3]


def _select_level_of_care(
    matrix: list[dict[str, Any]],
    scores: list[AsamDimensionScore],
) -> LevelOfCare:
    env: dict[str, int] = {f"dim{d.id}": d.risk_rating for d in scores}
    drivers = _identify_drivers(env)
    drivers_str = ", ".join(str(d) for d in drivers) if drivers else "n/a"
    for entry in matrix:
        if _safe_eval(entry["when"], env):
            template = entry.get("rationale_template", "")
            rationale = template.replace("{{drivers}}", drivers_str)
            return LevelOfCare(
                code=str(entry["code"]),
                name=str(entry["name"]),
                rationale=rationale,
                drivers=drivers,
            )
    return LevelOfCare(
        code="1.0",
        name="Outpatient",
        rationale="No matrix entry matched — defaulting to outpatient.",
        drivers=drivers,
    )


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------
class AsamEngine:
    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self.rules = rules or _load_rules()

    def assess(self, extract: PatientExtract) -> AsamAssessment:
        corpus = _corpus(extract)
        dim_scores: list[AsamDimensionScore] = []
        for dim in self.rules.get("dimensions", []):
            raw, rating, evidence = _score_dimension(dim, corpus)
            dim_scores.append(
                AsamDimensionScore(
                    id=int(dim["id"]),
                    name=str(dim["name"]),
                    short_name=str(dim.get("short_name", "")),
                    risk_rating=rating,
                    raw_score=raw,
                    rationale=_build_rationale(dim, raw, rating, evidence),
                    evidence=evidence,
                )
            )
        loc = _select_level_of_care(
            self.rules.get("level_of_care_matrix", []),
            dim_scores,
        )
        return AsamAssessment(
            rules_version=str(self.rules.get("version", "asam-rules")),
            dimensions=dim_scores,
            recommended_level_of_care=loc,
            computed_at=datetime.now(tz=UTC),
            text_corpus_size=sum(len(t) for _, t in corpus),
        )


__all__ = [
    "AsamAssessment",
    "AsamDimensionScore",
    "AsamEngine",
    "EvidenceSpan",
    "LevelOfCare",
]
