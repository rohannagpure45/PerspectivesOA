# PerspectivesOA Endpoint Catalog

Two layers of endpoints are documented here:

1. **Public PerspectivesOA endpoints** — what callers of this service hit.
2. **Upstream SimplePractice endpoints** — the reverse-engineered
   `/frontend/*` JSON:API surface we depend on.

---

## 1. Public service endpoints

All routes are mounted under `/api/v1` and respond with JSON.

### `GET /api/v1/healthz`

Liveness probe. Returns `{"status":"ok"}`.

---

### `GET /api/v1/patients/{hashed_id}/extract`

The canonical extract for Task 2. Resolves the URL hash to a numeric SP
client id, fetches the timeline + every appointment, classifies each
progress note's format, parses the BPS admission assessment, and returns
one structured document.

Query params:

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `refresh` | bool | `false` | If true, bypass the cache and re-fetch from SP/fixtures |

Response (truncated for readability):

```json
{
  "patient": {
    "hashed_id": "0c39dadff6972e0f",
    "numeric_id": "106612410",
    "name": "Jamie D. Appleseed",
    "preferred_name": "Jamie",
    "dob": "2025-10-31",
    "phone": "(609) 375-6850",
    "email": "nagpure.r@northeastern.edu",
    "address": {"line1": "...", "city": "...", "state": "...", "postal_code": "..."},
    "contacts": [
      {"name": "Karen Appleseed", "relationship": "Family Member", "phone": "(310) 555-1212", "email": "kappleseed@htn.mmd"}
    ],
    "diagnoses": [
      {"code": "F41.9", "description": "Anxiety disorder, unspecified"},
      {"code": "F43.22", "description": "Adjustment disorder with anxiety"}
    ],
    "measured_scores": [
      {"title": "GAD-7", "score": 12, "max_score": 21, "severity": "moderate", "administered_at": "2026-05-02T..."}
    ]
  },
  "admission_assessment": {
    "source_note_id": "925838931",
    "title": "Admission Assessment: Biopsychosocial (BPS) Intake",
    "noted_at": "2026-05-01T17:52:00-05:00",
    "sections": {
      "history_of_present_illness": "...",
      "substance_use_history": "...",
      "initial_risk_screening": {
        "si_hi": "...",
        "medical_risk": "...",
        "fall_risk": "Low",
        "living_environment": "...",
        "treatment_readiness": "..."
      }
    },
    "raw_text": "<verbatim BPS body>"
  },
  "timeline": [
    {"date": "2026-05-07", "type": "scored_measure", "title": "GAD-7", "note_id": "925740429"},
    {"date": "2026-05-05", "type": "appointment", "appointment_id": "3505428553", "progress_note": {"format": "DAP", "...": "..."}},
    {"date": "2026-05-04", "type": "appointment", "appointment_id": "3505428542", "progress_note": {"format": "SOAP", "...": "..."}},
    {"date": "2026-05-03", "type": "appointment", "appointment_id": "3505428529", "progress_note": {"format": "DSAP", "...": "..."}},
    {"date": "2026-05-02", "type": "scored_measure", "title": "GAD-7", "note_id": "925740426"},
    {"date": "2026-05-01", "type": "admission_assessment_ref", "note_id": "925838931"}
  ],
  "diagnoses": [...],
  "extracted_at": "2026-05-06T16:05:00-05:00",
  "source": "fixture"
}
```

`source` is `"live"` when the request hit SimplePractice and `"fixture"` when
the offline `FixtureBackend` served the data.

---

### `GET /api/v1/patients/{hashed_id}/demographics`

Returns the `patient` sub-document only (everything in the example above
inside the `patient` key).

---

### `GET /api/v1/patients/{hashed_id}/admission-assessment`

Returns the `admission_assessment` sub-document only. 404 if the chart has no
`thisType=Chart` note that begins with `"Admission Assessment"`.

---

### `GET /api/v1/patients/{hashed_id}/timeline`

Returns the timeline array directly. Newest entries first, sorted by
appointment start time / note `noted_at`. Each entry is one of:

| `type` | Carries |
| --- | --- |
| `appointment` | `appointment_id`, `start`, `end`, `progress_note`, `psychotherapy_note` |
| `chart_note` | `note_id`, `body`, `metadata` |
| `scored_measure` | `note_id`, `title`, `link`, `metadata` |
| `admission_assessment_ref` | `note_id` (back-pointer to the BPS note) |

---

### `POST /api/v1/patients/{hashed_id}/asam`

Runs the ASAM 4th-edition rule engine over the patient's chart text and
returns scores + recommended Level of Care.

Request body (optional):

```json
{ "include_text_spans": true }
```

Response:

```json
{
  "asam_edition": "4th",
  "rules_version": "asam-4e-rules-v1",
  "dimensions": [
    {
      "id": 1,
      "name": "Acute Intoxication and/or Withdrawal Potential",
      "risk_rating": 2,
      "raw_score": 2,
      "rationale": "Mild withdrawal markers (mild morning tremors, physiological dependence)...",
      "evidence": [
        {"source_note_id": "925740422", "matched_phrase": "mild morning tremors and nausea on days...", "tier": "mild"},
        {"source_note_id": "925838931", "matched_phrase": "physiological dependence", "tier": "mild"}
      ]
    },
    { "id": 2, "name": "Biomedical Conditions and Complications", "risk_rating": 0, "...": "..." },
    { "id": 3, "name": "Emotional, Behavioral, or Cognitive Conditions and Complications", "risk_rating": 3, "...": "..." },
    { "id": 4, "name": "Readiness to Change", "risk_rating": 2, "...": "..." },
    { "id": 5, "name": "Relapse, Continued Use, or Continued Problem Potential", "risk_rating": 2, "...": "..." },
    { "id": 6, "name": "Recovery/Living Environment", "risk_rating": 1, "...": "..." }
  ],
  "recommended_level_of_care": {
    "code": "2.1",
    "name": "Intensive Outpatient (IOP)",
    "rationale": "Mild withdrawal risk (Dim 1=2) plus moderate emotional load (Dim 3=3) and active relapse pattern (Dim 5=2) maps to IOP-level structure."
  },
  "computed_at": "2026-05-06T16:05:00-05:00"
}
```

See [docs/ASAM_RULES.md](ASAM_RULES.md) for the rules + LoC matrix.

---

### `POST /api/v1/patients/{hashed_id}/tjc-audit`

Runs the curated TJC CTS audit. Response:

```json
{
  "framework": "TJC CTS — Behavioral Health",
  "rules_version": "tjc-cts-bh-curated-v1",
  "findings": [
    {
      "standard": "CTS.02.01.01",
      "ep": "EP1",
      "title": "Comprehensive assessment within timeframe",
      "verdict": "pass",
      "rationale": "Admission Assessment is dated and precedes the first scheduled appointment.",
      "evidence": [{"note_id": "925838931", "span": "Date of Assessment: May 1, 2026"}]
    },
    { "standard": "CTS.02.02.01", "ep": "EP2", "verdict": "fail", "rationale": "...", "evidence": [] },
    { "standard": "CTS.03.01.03", "ep": "EP2", "verdict": "fail", "rationale": "DTP has goal=null...", "evidence": [...] }
  ],
  "summary": {"passed": 6, "failed": 2, "insufficient_data": 0},
  "computed_at": "2026-05-06T16:05:00-05:00"
}
```

See [docs/TJC_RULES.md](TJC_RULES.md) for the EP set we audit.

---

## 2. Upstream SimplePractice endpoints (reverse engineered)

All requests go to `https://secure.simplepractice.com` with the headers
listed in the README. The HAR at
`/Users/rohan/Downloads/secure.simplepractice.com.har` confirmed every shape
below; representative fixtures live in `fixtures/`.

### `GET /frontend/treatable-clients/{hashed_id}?filter[findByHashedId]=true`

Resolves a URL hash (e.g. `0c39dadff6972e0f`) to a numeric client id (e.g.
`106612410`) plus a stub of the treatable-client record. The numeric id is
required by every other call.

### `GET /frontend/clients/{numeric_id}`

With `include=emails,phones,addresses,clientRelationships.relatedClient.emails,clientRelationships.relatedClient.phones,insuranceInfos,insuranceInfos.insurancePlan,upcomingAppointments,clientBillingOverview`,
returns the patient profile and the full graph of contacts and insurance.
Used for demographics + family-member contact extraction.

### `GET /frontend/overview-items?filter[clientId]={numeric_id}`

The timeline endpoint. With
`include=progressNote.notable,psychotherapyNote,notable.diagnosisTreatmentPlanOverview,...`
returns the ordered `data: [{type:"notes"|"appointments", id}]` array plus an
`included` array carrying full note bodies, intake notes, diagnosis treatment
plans, and the client itself.

This is the single richest payload — many extracts only need this plus the
client record.

### `GET /frontend/appointments/{appointment_id}`

With
`include=progressNote.noteSignatureOverview,psychotherapyNote.noteSignatureOverview,diagnosisTreatmentPlan.globalDsmCodes,diagnosisTreatmentPlan.note.notable,complexNote.intakeQuestionnaire,treatmentProgress.diagnosisTreatmentPlan.globalDsmCodes,office,client.phones,client.emails`,
returns one appointment with its progress note, psychotherapy note, and DTP.
Hit when a timeline entry references an appointment but the overview-items
`included` array doesn't carry the full note body.

### `GET /frontend/notes/{note_id}`

Direct note fetch (the `links.self` returned by the timeline). Rarely needed
because notes generally come pre-populated in the overview-items `included`.

### Resource discrimination

`notes.attributes.thisType` discriminates the unified `notes` bag:

| `thisType` | Meaning |
| --- | --- |
| `IntakeNote` (+ `isMeasure: "true"`) | Scored measure (GAD-7, PHQ-9, …) |
| `Chart` | Free-text chart note. The BPS Admission Assessment is one of these. |
| `Progress` (+ `notable.type: "appointments"`) | SOAP / DAP / DSAP attached to an appointment |
| `Psychotherapy` | Psychotherapy note (separate confidentiality tier) |
| `DiagnosisTreatmentPlan` | Wrapper around the actual `diagnosisTreatmentPlans/{id}` resource |
