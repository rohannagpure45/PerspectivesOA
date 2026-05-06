# TJC CTS Audit — Curated EP Set

The Joint Commission's Comprehensive Accreditation Manual for Behavioral
Health Care contains hundreds of Elements of Performance under the Care,
Treatment, and Services chapter. This service audits a curated subset that
together represent the highest-yield "did the chart actually do this?"
checks against an admission record.

The curated set is hand-chosen to match the assessment example: the BPS
intake should fail CTS.02.02.01 EP2 because spiritual orientation isn't
documented, and CTS.03.01.03 EP2 should fail when the DiagnosisTreatmentPlan
has `goal: null`.

All rule logic lives in `app/intelligence/tjc/rules.yaml`. This document is
the human-readable companion.

---

## 1. Verdict semantics

Each EP audit returns one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| `pass` | The required evidence was found — citation included. |
| `fail` | The target document exists but the required evidence is missing. |
| `insufficient_data` | The target document is not present at all (e.g. there is no admission assessment to audit). |

The engine never returns "warn" or "borderline". TJC accreditation surveys
operate on yes/no, and we mirror that.

---

## 2. Rule shape

Every rule in `rules.yaml` has:

```yaml
- standard: CTS.02.02.01
  ep: EP2
  title: "Assessment includes spiritual/cultural orientation"
  target: admission_assessment      # which extracted artefact to inspect
  evidence:                         # all of these must match for pass
    - r"spiritual"
    - r"cultural|religion|faith"
  any_of:                           # optional: any group passes
    - patterns: [r"si/?hi"]
  require_all:                      # optional: structural booleans
    - "dtp_goal_nonempty"
  failure_rationale_template: >
    The Biopsychosocial intake omits any spiritual or cultural assessment
    domain. The Joint Commission requires this orientation be documented.
  pass_rationale_template: >
    The intake addresses the patient's spiritual / cultural orientation.
```

Targets the engine knows about:

| Target | What gets searched |
| --- | --- |
| `admission_assessment` | The full BPS body (raw_text) |
| `diagnoses_and_dtp` | Whether diagnoses array and DTP record both exist |
| `dtp` | Structural booleans on the DiagnosisTreatmentPlan |
| `timeline_measures` | Count + identity of scored measures in the timeline |

Structural booleans currently supported by the engine (kept narrow on
purpose — adding more is a one-line change):

| Boolean | Meaning |
| --- | --- |
| `diagnoses_present` | `len(extract.diagnoses) > 0` |
| `dtp_present` | A DTP resource was surfaced from overview-items |
| `dtp_goal_nonempty` | `dtp.goal` and/or `dtp.formattedGoal` is non-null and non-empty |
| `scored_measure_count_at_least_2` | `>= 2` timeline entries of `type: scored_measure` |

---

## 3. Curated EP set

| Standard | EP | What it asserts | Pinned-test verdict (Jamie) |
| --- | --- | --- | --- |
| `CTS.02.01.01` | EP1 | Comprehensive assessment dated within timeframe | pass |
| `CTS.02.02.01` | EP2 | Assessment includes spiritual/cultural orientation | **fail** |
| `CTS.02.02.01` | EP4 | Substance use history with quantity + frequency | pass |
| `CTS.03.01.01` | EP1 | Treatment plan addresses identified problems | pass |
| `CTS.03.01.03` | EP2 | Treatment plan includes measurable goals | **fail** |
| `CTS.04.03.01` | EP1 | Suicide and homicide risk assessed | pass |
| `CTS.04.03.05` | EP1 | Lethal means inquiry (firearms / weapons) | pass |
| `CTS.05.01.01` | EP3 | Use of standardized outcomes measurement | pass |

Six pass / two fail / zero insufficient-data is the snapshot pinned in
`tests/test_tjc_jamie.py`.

### Why these eight

* **CTS.02.x** covers the assessment chapter — the BPS intake is the largest
  artefact in the chart, so we extract several distinct EPs from it
  (timeliness, spiritual/cultural, substance-use detail, SI/HI inquiry,
  lethal means).
* **CTS.03.x** covers treatment planning — diagnoses present + DTP present +
  measurable goals are the three highest-yield questions a surveyor asks.
* **CTS.05.01.01 EP3** covers outcomes measurement — easy to score from
  scored-measure timeline entries.

---

## 4. Why CTS.02.02.01 EP2 fails for Jamie

The BPS body is searched for both `spiritual` and `cultural|religion|faith`.
Jamie's intake (the verbatim text in `notes/925838931`) contains domains
for HPI, Substance Use History, Past Medical History, Mental Status Exam,
Risk Screening, and Plan — but no spiritual or cultural domain at all.

So the engine emits:

```json
{
  "standard": "CTS.02.02.01",
  "ep": "EP2",
  "verdict": "fail",
  "rationale": "The Biopsychosocial intake omits any spiritual or cultural assessment domain. The Joint Commission requires this orientation be documented.",
  "evidence": []
}
```

This is the exact behaviour described in the assessment ("CTS.02.02.01 EP2
failed because spiritual orientation was not documented") — verbatim.

---

## 5. Why CTS.03.01.03 EP2 fails for Jamie

The DTP resource `diagnosisTreatmentPlans/44188318` carries
top-level `attributes.goal` and `attributes.formattedGoal` fields that are
both `null`. The engine surfaces a citation pointing at the structured
location of the failure:

```json
{
  "standard": "CTS.03.01.03",
  "ep": "EP2",
  "verdict": "fail",
  "rationale": "The Diagnosis Treatment Plan record has goal=null and formattedGoal=null, so there is no measurable, documented treatment goal.",
  "evidence": [{"note_id": "DTP/44188318", "span": "\"goal\": null, \"formattedGoal\": null"}]
}
```

---

## 6. Adding new EPs

1. Append a new entry to `rules.yaml` — give it a `standard`, `ep`,
   `title`, `target`, and either `evidence`/`any_of` (regex) or
   `require_all` (structural).
2. If you need a new structural boolean, add it to
   `_STRUCTURAL_PREDICATES` in `app/intelligence/tjc/engine.py`.
3. Bump `version:` at the top of `rules.yaml`.
4. Add a `tests/test_tjc_*.py` line asserting the verdict on the fixture.

The engine never imports rule logic from Python — adding a new EP is a
YAML edit plus, occasionally, one new boolean.

---

## 7. Sources

* The Joint Commission, *Comprehensive Accreditation Manual for Behavioral
  Health Care* (latest edition).
* The CTS chapter (Care, Treatment, and Services) — particularly
  CTS.02 (Assessment), CTS.03 (Treatment Planning), CTS.04 (Specific
  Care/Risk), and CTS.05 (Outcomes).

EP titles in this repo paraphrase the published wording for engineering
readability — validate against the binding manual before applying these
audits to live patients.
