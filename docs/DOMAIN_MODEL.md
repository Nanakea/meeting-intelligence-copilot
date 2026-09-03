# Domain Model — Meeting Intelligence Copilot

This document is the authoritative description of the domain contracts and the state machine. It is
the reference for `PRODUCT_SPEC.md` (behavioral guarantees) and `ARCHITECTURE.md` (how it is wired).
All types are Pydantic models in `backend/src/copilot/domain/contracts.py` (not yet implemented)
and are mirrored to TypeScript for the frontend.

Design principles: the domain is **pure and deterministic**, speaks only in `TranscriptEvent`
(never platform payloads), preserves evidence, and never treats a model as the source of truth.

API v8 adds `DocumentArtifact`, `EvidenceBundle`, `AssuranceFinding`, ERP metadata, and digital
solution-thread contracts in separate modules. They are not fields of `MeetingState`. Retrieval and
assurance results therefore cannot become transcript evidence or alter fact/gap lifecycle behavior.

---

## 1. Entities

### 1.1 TranscriptEvent

The only input the domain understands. Adapters translate platform/STT payloads into this shape.

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | str | Stable unique id. Reducer dedups on this. |
| `meeting_id` | str | |
| `seq` | int | Monotonic order assigned at ingest. |
| `speaker` | Speaker | `{ id, display_name?, role? }` |
| `text` | str | Utterance text. |
| `lang` | `"ja" \| "en" \| "ko"` | Drives language-specific extraction patterns. |
| `ts_start` | float | Seconds from meeting start. |
| `ts_end` | float | |
| `is_final` | bool | Only finals drive analysis in early slices. |
| `source` | str | Adapter name (e.g. `jsonl_replay`). Informational only. |

### 1.2 MeetingFact

An established piece of information, tied to a **slot** on a pain point.

| Field | Type | Notes |
|-------|------|-------|
| `fact_id` | str | |
| `meeting_id` | str | |
| `pain_id` | str | The pain point this fact informs. |
| `slot` | str | Slot key within the pain template (e.g. `source_of_truth`). |
| `value` | str | Normalized human-readable value (e.g. `EC`, `月末`). |
| `kind` | `evidence \| inference` | **Provenance axis** — see §3. |
| `status` | `active \| superseded \| contradicted` | **Lifecycle axis** — see §4. |
| `evidence_event_ids` | list[str] | Non-empty. Transcript events justifying the fact. |
| `supersedes` | str \| null | `fact_id` this fact replaced, if any. |
| `superseded_by` | str \| null | `fact_id` that replaced this one, if any. |
| `contradicts` | list[str] | `fact_id`s this fact conflicts with, if any. |
| `confidence` | float | 0–1. Rule engine ≈ 1.0 for evidence; lower for inference. |
| `created_seq` | int | `seq` of the event that produced it. |

`kind` and `status` are **orthogonal**: an `inference` fact can be `active`; an `evidence` fact can
be `superseded`.

### 1.3 PainPoint

| Field | Type | Notes |
|-------|------|-------|
| `pain_id` | str | |
| `meeting_id` | str | |
| `template_id` | str | e.g. `data_mismatch`, `integration_failure`, `manual_work`, `process_delay`. |
| `title` | str | Short human label. |
| `status` | `open \| mitigated` | |
| `kind` | `evidence \| inference` | Whether the pain was explicitly stated or inferred. |
| `evidence_event_ids` | list[str] | Non-empty. |
| `created_seq` | int | |

### 1.4 InformationGap

A slot on a pain point that is not currently satisfied by a valid active fact.

| Field | Type | Notes |
|-------|------|-------|
| `gap_id` | str | Deterministic: derived from `(pain_id, slot)` so identity is stable. |
| `meeting_id` | str | |
| `pain_id` | str | |
| `slot` | str | The slot that would resolve it. |
| `status` | `open \| answered` | |
| `resolved_by_fact_id` | str \| null | The active fact currently answering it. |
| `base_priority` | int | Rank from the template slot order (lower = higher priority). |
| `reopen_count` | int | Times it has reopened. For observability, not ranking. |

### 1.5 QuestionSuggestion

| Field | Type | Notes |
|-------|------|-------|
| `suggestion_id` | str | |
| `gap_id` | str | The gap it targets. |
| `role` | `ask_now \| follow_up` | |
| `text` | str | The rendered question (localized to meeting `lang`). |
| `reason` | str | Short private justification. |
| `status` | `active \| retracted` | |
| `priority` | int | Effective priority used for ordering. |

### 1.6 MeetingState

The aggregate pushed to the frontend as a versioned snapshot.

| Field | Type | Notes |
|-------|------|-------|
| `meeting_id` | str | |
| `version` | int | Increments on every applied change. Frontend keeps the latest. |
| `transcript` | list[TranscriptEvent] | Backend-owned log carried in the snapshot so the UI can render the live transcript without computing anything. Grows with the meeting (acceptable for replay fixtures; a bounded/windowed view is future work). |
| `pain_points` | list[PainPoint] | |
| `facts` | list[MeetingFact] | **Includes historical** superseded/contradicted facts. |
| `gaps` | list[InformationGap] | |
| `suggestions` | list[QuestionSuggestion] | Active ones obey the 1 + 2 cap. |
| `last_event_seq` | int | |

The event log itself is retained by the store; `MeetingState` is a fold over it.

---

## 2. Pain templates and slots

A **pain template** declares an **ordered list of slots**. The order **is** the base priority for
that template — there is deliberately **no universal ordering** across templates (rule G8).

Initial defaults (subject to product iteration — these are defaults, not immutable truth):

```
data_mismatch:
  1. source_of_truth
  2. business_impact
  3. owner
  4. affected_scope
  5. frequency
  6. correction_process

integration_failure:
  1. business_impact
  2. failure_point
  3. owner
  4. retry_behavior
  5. error_signal
  6. source_system
  7. target_system

manual_work:
  1. business_impact
  2. frequency
  3. volume
  4. time_cost
  5. owner
  6. reason_manual
  7. success_condition

process_delay:
  1. business_impact
  2. bottleneck
  3. current_lead_time
  4. owner
  5. normal_lead_time
  6. process_step

vague_requirement:
  1. expected_behavior
  2. acceptance_criteria
  3. business_impact
  4. current_problem
  5. owner
  6. priority_level

unclear_ownership:
  1. owner
  2. business_impact
  3. decision_process
  4. stakeholders
  5. escalation_path
  6. current_status
```

**Current deterministic status.** Ten categories are registered in the data-driven template
registry (`app/domain/templates.py`) with ordered slots and JA+EN+KO question wording.
Deterministic guarded rules are implemented in all three languages for **data_mismatch**,
**vague_requirement**, **integration_failure**, **manual_work**, **process_delay**, and
**unclear_ownership**.
The solution-lead extension adds **nonfunctional_requirement**, **architecture_constraint**,
**security_control_gap**, and **dependency_blocker** with the same evidence and gap invariants.
Japanese, English, and Korean integration failures
recognize bounded directional-transfer grammars
(`source の payload を target に operation ... で failure`) and extract explicitly stated
`source_system`, `target_system`, and `failure_point` facts. The English equivalent requires an
explicit `payload operation from source into target fails` relationship. Undirected system mentions
do not fill those slots. English process-delay and unclear-ownership rules require explicit
lead-time/bottleneck or unresolved-owner grammar; generic waiting language does not open a delay pain.
Language is **explicit** on each `TranscriptEvent` (set by source config; no
auto-detection in the domain) and dispatches which rule set runs and which language
the questions render in. These rules are keyword/marker heuristics for synthetic
fixtures — **not unrestricted language understanding, not AI/LLM analysis, and not real-meeting
support.** Contradiction, correction, and scope-qualified facts remain deferred to
the robustness phase.

### 2.1 Governance aggregate

`GovernanceCandidate` is a deterministic, transcript-backed proposal with a stable fingerprint,
exact `evidence_event_ids`, linked problem instances, and recognized glossary entities. It is not a
durable fact. Only explicit confirm-with-optional-edits creates a `GovernanceRecord`.

`GovernanceRecord` has an opaque workspace ID, typed payload, monotonic revision, field-level
provenance, source meetings, evidence references, linked problems/entities, and append-only
`GovernanceEvent` history. Status changes, supersession, links, and provenance removal never erase
surviving history. Connector citations can accompany briefs or impact analysis but cannot confirm a
candidate, fill a meeting gap, or become transcript evidence.

Each slot additionally carries per-language **fill patterns** (keywords/regex) and a **question
template** (JA/EN/KO) used to render a `QuestionSuggestion`. In the first slice these are deterministic
rules; a later `AnalysisEngine` (Ollama) may fill the same slots but the reducer stays authoritative.

---

## 3. Evidence vs. inference (corrected R6 model — explicit)

`MeetingFact.kind` and `PainPoint.kind` capture **provenance** and are first-class, not cosmetic.

- **`evidence`** — the value was **explicitly stated** in the transcript. `evidence_event_ids` point
  at the utterance(s) that literally assert it. Example: 「ECを正としています。」 →
  `source_of_truth = EC`, `kind = evidence`.
- **`inference`** — the value was **derived by the system** (deterministic rule or, later, an LLM)
  and was **not literally stated**. It **still must carry `evidence_event_ids`**: the supporting
  utterances the inference was drawn from. Example: two speakers describing incompatible “正” →
  an inferred pain `authority_ambiguity`, `kind = inference`, citing both utterances.

Rules governing kind:

1. **Every fact and pain point of either kind carries `evidence_event_ids`** (rule G1). Inference is
   never evidence-free; it is a conclusion *about* cited evidence.
2. **The slice-1 rule engine emits only `evidence` facts.** `inference` is reserved for later
   engines that reason beyond literal statements.
3. **Kind never silently upgrades.** An inference does not become evidence; if the same value is
   later explicitly stated, that produces a **new** evidence fact that **supersedes** the inference.
4. **UI must render kind distinctly.** Inference facts are visibly marked as inferred and are weaker:
   evidence supersedes a contradicting inference for the same slot without itself becoming contested.
5. A gap may be answered by an active fact of **either** kind, but an inference-answered gap is
   flagged in the UI as “answered by inference,” signalling it is softer than stated evidence.

`kind` (provenance) is orthogonal to `status` (lifecycle in §4).

---

## 4. Fact lifecycle and non-monotonic state (correction 1)

Meeting truth is **not monotonic**. Later evidence can correct or contradict earlier statements.

### 4.1 Status values

- **`active`** — currently valid; participates in slot occupancy and gap resolution.
- **`superseded`** — replaced by a newer, better-supported fact for the **same slot**. Retained for
  traceability. The replacement is `active`; the slot remains filled.
- **`contradicted`** — conflicts with other evidence and has **no clear replacement**. Retained. If
  contradiction leaves the slot with no active fact, the gap reopens.

**Facts are never deleted** (rule G5). History is always retained in `MeetingState.facts`.

### 4.2 Slot occupancy

> A **slot is filled** if and only if it has **≥ 1 `active` fact**.
> An `InformationGap` for that slot is `answered` iff its slot is filled; otherwise `open`.

### 4.3 Relations carried by analysis results

When the `AnalysisEngine` proposes a fact, it may annotate a **relation** to an existing fact so the
**reducer** (not the model) can apply the deterministic transition:

- `relation = supersede(target_fact_id)` — a correction/refinement of the same slot value.
  Triggered deterministically by correction markers, e.g. JA 「正確には…ではなく」「実際は」,
  EN “actually…”, “correction:”, or an explicit newer authoritative statement of the same slot.
- `relation = contradict(target_fact_id)` — an incompatible assertion for the same slot with **no**
  clear winner (e.g. two different authoritative sources named by different speakers).
- `relation = none` — a fresh fact for a previously empty slot, or corroborating evidence.

The engine only *proposes* the relation; the reducer decides and records the outcome. Models are not
the source of truth (rule G8/G9).

### 4.4 Reducer transitions (deterministic)

For an incoming analysis result targeting slot `S` of pain `P`:

1. **Empty slot, `none`** → insert new `active` fact. If a gap for `S` existed, it becomes
   `answered` with `resolved_by_fact_id`.
2. **Corroboration** (same value as an active fact) → append `evidence_event_ids` to the existing
   active fact. No status change. Gap stays `answered`.
3. **Supersede** → new fact `active`; target fact → `superseded` (`superseded_by` set, new fact
   `supersedes` set). Slot stays filled; gap stays `answered`, `resolved_by_fact_id` updated.
4. **Contradict** → both the incoming and the conflicting active fact(s) are marked `contradicted`
   and cross-linked via `contradicts`. Then re-evaluate slot occupancy (§4.5).

### 4.5 Gap reopening (corrected — explicit, deterministic)

After any transition, for each affected slot the reducer recomputes occupancy:

> If a slot that was `answered` now has **zero `active` facts**, its `InformationGap` transitions
> `answered → open`, `resolved_by_fact_id` is cleared, `reopen_count += 1`, and the gap re-enters
> selection.

Reopening is caused **only** by this state/evidence logic (rule G7). A model may surface a fact or a
contradiction relation, but **a model suggesting “ask this again” never reopens a gap** — only the
absence of a valid active fact does. This keeps reopening auditable.

Worked cases (from `PRODUCT_SPEC.md` §5a):

- 「毎日発生」 then 「正確には…月末だけ」 → **supersede**; `frequency` slot stays filled; **no reopen**.
- 「倉庫側を正」 then 「US側ではNetSuiteが正」 → **contradict**; `source_of_truth` slot has 0 active
  facts → gap **reopens**; ASK NOW returns to clarify the authoritative source.

---

## 5. Suggestion selection

Deterministic function `select(state) -> suggestions`:

1. Candidate gaps = all gaps with `status = open`.
2. Order them by **effective priority** from the `PriorityStrategy` port (§6).
3. The top gap becomes the single `ask_now`; the next up-to-two become `follow_up` (rule G3, cap 1+2).
4. Render each via the slot's question template in the meeting `lang`, with a `reason`.
5. Any previously-active suggestion whose gap is no longer top-3 open, or is now `answered`, becomes
   `retracted` (rule G4). Retraction is explicit and visible to the frontend.

Suggestion **identity is stable** because it derives from `gap_id` (which derives from
`(pain_id, slot)`); this is what prevents flicker and duplicate cards across snapshots.

---

## 6. Priority strategy (correction 2 — clean boundary)

`PriorityStrategy` is a **port**: `rank(open_gaps, state) -> ordered gaps`.

- **Now — `TemplatePriorityStrategy` (deterministic).** Effective priority = the gap's
  `base_priority`, i.e. its position in its pain template's slot order. Ties broken by
  `(pain created_seq, slot order)`. No cross-template universal ranking exists.
- **Later — `ContextualPriorityStrategy` (deterministic, not yet built).** Wraps the template
  strategy and may **adjust** priority using **explicit signals only**, e.g. active business
  interruption, shipment blocked, billing blocked, accounting-close impact, security risk, customer
  impact, explicit urgency. It must remain deterministic and auditable; it is **out of scope now**.

The seam exists so contextual ranking can be added without touching the reducer, the selector, or
the contracts.

---

## 7. Invariants (machine-checkable)

- I1: every `MeetingFact` / `PainPoint` has non-empty `evidence_event_ids`.
- I2: at most one `active` `ask_now` suggestion; at most two `active` `follow_up`.
- I3: no `active` suggestion targets an `answered` gap.
- I4: a slot with an `answered` gap has ≥1 `active` fact; a slot with 0 active facts has no
  `answered` gap.
- I5: superseded/contradicted facts are never removed from `MeetingState.facts`.
- I6: `gap_id` is a pure function of `(pain_id, slot)`.
- I7: reducer is idempotent under duplicate `event_id`.
- I8: gap `reopen_count` only ever increases, and only via §4.5 logic.
---

## 8. Documentation consistency aggregate (API v13)

Documentation/system consistency is deliberately outside the meeting-state fold. A confirmed
`DocumentClaim` and a read-only `ObservedSystemFact` can produce a `ConsistencyFinding` only when
their canonical subject, property, environment, and version scopes are compatible. Every finding
contains both citations and source revisions. Ambiguity is surfaced rather than resolved by guess.

Attribution is neutral by default. A revisioned `AuthorityPolicy` may classify a disagreement as
documentation, implementation, configuration, or release drift. An `ApprovedException` is
append-only and expires; it does not change either source. Reviews use expected revisions, findings
are superseded rather than deleted when source revisions change, and confirmed solution-thread
relationships remain a separate user action.

Semantic JA/EN/KO prose extraction is proposal-only. A local model cannot create a finding, mutate
meeting truth, or establish source authority without explicit review.
