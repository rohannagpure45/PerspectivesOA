# ASAM 4th Edition Rule Cheatsheet

The ASAM engine is fully data-driven by `app/intelligence/asam/rules.yaml`.
This document is the human-readable companion: it explains how the engine
turns the YAML into a 0–4 risk rating per dimension and into a recommended
Level of Care.

The engine is deliberately a regex+predicate pipeline — **no LLM, no
black-box model, no probabilistic scoring**. Every output line in the JSON
response can be traced back to one or more matched regex patterns and the
exact note text that triggered them.

---

## 1. The six dimensions

The 4th edition keeps the familiar six biopsychosocial dimensions:

| # | Name | What we look for |
| --- | --- | --- |
| 1 | Acute Intoxication / Withdrawal Potential | DTs, withdrawal seizures, tremors, alcohol units/day, "physiological dependence" |
| 2 | Biomedical Conditions and Complications | Unstable vitals, chronic disease, biomedical instability |
| 3 | Emotional / Behavioral / Cognitive | Suicidality, panic attacks, GAD/MDD, functional impairment |
| 4 | Readiness to Change | Pre-contemplation vs ambivalence vs strong internal motivation |
| 5 | Relapse / Continued Use Potential | Failed cut-down attempts, cravings, rebound anxiety, escalating use |
| 6 | Recovery / Living Environment | Housing, supportive partner, firearms, family conflict |

For each dimension, the YAML provides four phrase tiers:

| Tier | Weight | Example |
| --- | --- | --- |
| `high_risk` | +3 base | `acute psychosis`, `delirium tremens` |
| `moderate` | +2 base | `daily panic attacks`, `chronic liver disease` |
| `mild` | +1 base | `mild morning tremors`, `craving` |
| `protective` | -1 offset | `denies SI`, `supportive partner`, `no firearms` |

---

## 2. How a dimension score is computed

Walk every regex in every tier of the dimension. For each pattern:

1. Try to match it against every note in the corpus (BPS + every progress
   note + every psychotherapy note we surfaced from the chart).
2. If the match's start index sits within ~30 characters of a negation cue
   (`no`, `denies`, `without`, `negative for`, …), skip it — that's
   risk-clearing language, not risk-creating language.
3. Otherwise, collect the citation `(note_id, matched phrase, surrounding
   span)`. **A given pattern contributes to the per-tier count at most
   once across the entire corpus** — repeated mentions are evidence, not
   severity.

Then:

```
base = 3 if any high_risk-tier pattern matched
       2 if any moderate-tier pattern matched
       1 if any mild-tier pattern matched
       0 otherwise

bonus +=  1  if moderate-pattern count >= 3   (broad confirmation)
bonus +=  1  if moderate-pattern count >= 5   (extensive accumulation)
bonus +=  1  if mild-pattern count >= 3       (cluster of mild markers)

protective_offset = -1 if (no high_risk patterns) and (>=2 protective patterns)

raw   = base + bonus + protective_offset
score = clamp(raw, 0, 4)
```

The exposed `risk_rating` is the clamped score; `raw_score` is preserved in
the response for diagnostic visibility.

### Why this shape

* The presence ladder mirrors how clinicians actually think — one moderate
  finding is "a 2", one severe finding is "a 3", and accumulation pushes
  things up but never replaces severity.
* Per-pattern de-duplication prevents over-scoring when the SOAP, DAP, and
  DSAP all repeat the same finding.
* Negation handling means clinicians' protective phrasing ("no acute
  psychosis", "denies SI") doesn't accidentally raise the score.
* Protective findings can de-escalate a borderline case but never overrule
  a high-risk finding — exactly how ASAM treats them.

---

## 3. Level of Care matrix

The decision matrix lives at the bottom of `rules.yaml`. It is evaluated
top-to-bottom; **first match wins**:

| Order | LoC | Condition |
| --- | --- | --- |
| 1 | 4.0 Medically Managed Inpatient | `dim1 >= 4 or dim2 >= 4` |
| 2 | 3.7 Medically Monitored Residential | `dim1 >= 3 or dim3 >= 4` |
| 3 | 2.5 Partial Hospitalization | `dim3 >= 3 and dim5 >= 2 and dim4 <= 2 and dim1 <= 1` |
| 4 | 2.1 Intensive Outpatient | `dim1 >= 2 and (dim3 >= 2 or dim5 >= 2)` |
| 5 | 1.0 Outpatient | `max(dim1..dim6) <= 1` |
| 6 | 1.0 Outpatient (default) | `true` |

`dim_n` are the clamped 0–4 dimension scores. The `when:` strings are
evaluated in a sandboxed Python expression context that exposes only those
dim variables and `max`/`min`.

---

## 4. Worked example — Jamie D. Appleseed

Running the engine against the fixture yields:

| Dim | Rating | Drivers |
| --- | --- | --- |
| 1 | 2 | "physiological dependence", "mild morning tremors and nausea", "6 to 8 beers" |
| 2 | 0 | No biomedical instability documented |
| 3 | 3 | "daily panic attacks", "impending doom", "co-occurring GAD", "called out sick four times", "isolating from friends", multiple moderate findings → presence ladder gives 2, two-bonuses add 1 |
| 4 | 2 | "ambivalent" + "uncertainty about tolerating anxiety" — countered partially by "strong internal desire to change" and "good insight" |
| 5 | 2 | "two failed attempts", "intense cravings", "rebound anxiety", "escalating alcohol use" |
| 6 | 1 | "supportive partner" + "no firearms" → protective; "strained relationship" → mild |

`(dim1, dim2, dim3, dim4, dim5, dim6) = (2, 0, 3, 2, 2, 1)`

Walking the matrix:

* Row 1 fails (`dim1 = 2 < 4`, `dim2 = 0 < 4`).
* Row 2 fails (`dim1 = 2 < 3`, `dim3 = 3 < 4`).
* Row 3 fails (requires `dim1 <= 1`; we have `dim1 = 2`).
* **Row 4 matches**: `dim1 = 2 >= 1` and `dim3 = 3 >= 2` and `dim5 = 2 >= 2`.

Recommended LoC: **2.1 Intensive Outpatient (IOP)** — which exactly matches
the disposition the practice's own SOAP note recommends on 2026-05-04. That's
the test we pin in `tests/test_asam_jamie.py`.

---

## 5. Citing your rules

When you want to add or change a rule:

1. Edit `app/intelligence/asam/rules.yaml`. Bump `version:`.
2. Add a representative phrase to one of the test snapshots so future
   regressions are caught.
3. The engine's response carries `rules_version`, so any audit can be
   traced back to the exact YAML that produced it.

Sources behind the published phrasing:

* **ASAM Criteria, 4th Edition (2023)** — six-dimensional biopsychosocial
  framework, retitled dimensions, expanded protective-factor language.
* **ASAM Continuum levels of care** — 1.0 OP, 2.1 IOP, 2.5 PHP, 3.x
  residential subtiers, 4.0 medically managed inpatient.

The wording in this repo paraphrases those for engineering clarity — when
applying these rules to live patients, validate against the published
manual.
