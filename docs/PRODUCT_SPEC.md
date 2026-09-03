# Product Specification — Meeting Intelligence Copilot

## 1. Purpose

A local-first, real-time **question copilot** for Japanese, English, and Korean business meetings. It helps
the person in the room ask the highest-value clarifying question at the right moment. It is
**not primarily a summarizer**; summaries, if ever produced, are a byproduct of the tracked state,
never the core deliverable.

## 2. Target users

PMs, lead engineers, ERP/system leads, and people who bridge business stakeholders and engineering
teams. Their leverage in a meeting is the quality and timing of their questions.

## 3. Core capabilities (the product promise)

1. Receive **live transcript events**.
2. Identify **expressed or implied pain points**.
3. Distinguish **evidence** (what was said) from **inference** (what the system concluded).
4. Track **facts already established** in the conversation.
5. Identify **critical missing information**.
6. Privately suggest the **highest-value next question**.
7. **Stop suggesting** a question once its information gap is answered by a valid active fact.
8. Support **Japanese, English, and Korean** business meetings through explicit language
   configuration.
9. Optionally show **read-only cited ERP or knowledge context** beside a question without treating it
   as meeting evidence.
10. Detect transcript-backed **governance candidates** for decisions, risks, dependencies, actions,
    assumptions, constraints, architecture impacts, and responsibilities.
11. Admit governance records only after explicit user review, then preserve their revisions and
    provenance across meetings.
12. Provide a confirmed-only solution landscape, bounded impact analysis, portfolio alerts, and
    reviewed export packs without writing to external systems.
13. Retrieve cited local and live connected evidence without allowing retrieval to alter meeting
    truth, close gaps, or admit governance records.
14. Run deterministic checks over revisioned solution documents and read-only ERP metadata, with
    append-only findings that require explicit review.
15. Maintain a review-gated digital solution thread for requirements, design, tests, integrations,
    data, cutover, operations, and governance impact.
16. Compare approved documentation with selected code, API, pipeline, ERP-metadata, and CMDB
    observations, showing both source revisions and a neutral discrepancy until reviewed authority
    policy establishes a drift direction.
17. Project the review-gated solution thread into a bounded, fingerprinted mind map with safe
    labels, confirmed-versus-suggested relationships, retrieval hints, and Mermaid output. The map
    is a navigation aid, not a new authority or an automatic relationship-confirmation path.
18. Read approved NetSuite and WMS projections for deterministic descriptive price, inventory,
    volume, fulfillment, and service-level trends without mixing currencies, persisting remote
    rows, or exposing any mutation operation.
19. Retrieve selected Notion pages and Slack channels as live-only read context, while local Zoom
    audio reaches the deterministic meeting path through Meetily without Zoom API access.
20. Reconcile explicitly mapped NetSuite, WMS, and Amazon FBA handoff records and show the exact
    transition containing a missing record, quantity mismatch, or duplicate key without treating
    expected fulfillment timing as authoritative record loss.

## 4. Behavioral guarantees

These are testable product invariants, enforced by deterministic logic (see `DOMAIN_MODEL.md`).

- **G1 — Evidence traceability.** Every `PainPoint` and `MeetingFact` carries `evidence_event_ids`
  pointing at the transcript events that justify it.
- **G2 — Evidence vs. inference are separate.** Each fact declares `kind = evidence | inference`.
  An inferred fact is never presented to the user as if it were explicitly stated.
- **G3 — Bounded suggestions.** At any moment the UI shows **at most one ASK NOW** question and
  **at most two FOLLOW UP** questions.
- **G4 — No answered re-asking.** The system does not suggest an information gap that is currently
  answered by a valid **active** fact.
- **G5 — Justified suggestions.** Every suggestion carries a short private **reason**.
- **G6 — Non-monotonic truth.** Later evidence may **correct or contradict** earlier statements.
  Facts have a lifecycle (`active | superseded | contradicted`); history is retained, not deleted.
- **G7 — Principled reopening.** An answered gap **reopens only** when explicit fact-invalidation
  or contradiction logic leaves its slot with **zero** valid active facts — never because a model
  merely proposes re-asking.
- **G8 — Template-specific priority.** Question priority is defined **per pain template**. There is
  no single universal ranking of slots across all pain types.
- **G9 — Deterministic core.** State transitions and gap calculation are deterministic. Models may
  extract or propose, but are never the source of truth for meeting state.
- **G10 — Local-first.** No cloud LLM participates in the runtime.
- **G11 — Confirmation-gated governance.** A detected candidate cannot become a durable governance
  record until the user confirms it. Legacy summaries and connector results cannot admit records.
- **G12 — Separate authorities.** `MeetingState` remains transcript authority. Governance records
  retain transcript provenance in a separate revisioned aggregate; external citations remain
  supplemental to both.
- **G13 — Retrieval is not authority.** RAG citations and remote connector results may support a
  review, but cannot mutate `MeetingState`, satisfy a required field, or confirm a durable record.
- **G14 — Findings are proposals.** Document and ERP checks append reviewable findings. No finding
  creates a problem, risk, action, dependency, or architecture record without explicit review.
- **G15 — Confirmed-only impact.** Suggested solution-thread relationships are excluded from
  traceability and impact analysis until a revision-checked user confirmation.
- **G16 — Two-sided consistency evidence.** Every system/document discrepancy preserves one cited
  documented claim and one cited observed fact. Unreviewed semantic claims, expired leases, and
  ambiguous cross-environment matches cannot create authoritative findings.

## 5. Worked scenario (Japanese) — the first vertical slice

Pain template: **`data_mismatch`**.

| Step | Utterance | System reaction |
|------|-----------|-----------------|
| 1 | 「最近、倉庫側とEC側で在庫が合わないことがあります。」 | PainPoint `inventory_mismatch` opens; slots `source_of_truth, business_impact, owner, affected_scope, frequency, correction_process` → gaps open. |
| 2 | 「最近はほぼ毎朝、担当者が手動で直しています。」 | Facts: `frequency = ほぼ毎朝` (fills `frequency`), `correction_process = 手動修正` (fills `correction_process`). Those gaps → **answered**. |
| 3 | *(selection)* | ASK NOW = `source_of_truth` (top of `data_mismatch` order). FOLLOW UP = `business_impact`, `owner`. |
| — | ASK NOW card | 「現在、在庫の正として扱っているのはEC側と倉庫側のどちらでしょうか？」 Reason: *The authoritative inventory source is still unclear.* |
| 4 | 「ECを正としています。」 | Fact `source_of_truth = EC` (active). Gap answered → ASK NOW retracts. `business_impact` promotes to ASK NOW. |

### 5a. Non-monotonic follow-on (correction & contradiction)

| Step | Utterance | System reaction |
|------|-----------|-----------------|
| 5 | 「正確には毎日ではなく、月末だけです。」 | **Supersede**: new fact `frequency = 月末` becomes active; prior `frequency = ほぼ毎朝` → `superseded` (retained). Slot still filled → `frequency` gap stays closed. |
| 6 | (A)「倉庫側を正としています。」 → later (B)「US側ではNetSuiteが正です。」 | **Contradiction** on `source_of_truth` with no clear winner. Conflicting facts → `contradicted`; slot now has 0 active facts → gap **reopens**, ASK NOW returns to clarify the authoritative source. |

## 6. Explicit non-goals (first vertical slice)

- No meeting summaries as a headline feature.
- No direct Ollama / whisper.cpp / Vexa / Google Meet / Teams / Zoom platform adapters in the
  domain runtime. Live meeting support in the current productized path comes through Meetily's
  local audio capture and transcript bridge, not direct cloud or platform APIs.
- No cloud AI or autonomous external writes. Read-only connected context remains outside
  `MeetingState` and cannot confirm governance.
- No connector may create, update, archive, post, move, or delete remote company content. Local
  disconnect and retention controls may purge only user-owned encrypted caches and credentials.
- No universal cross-template priority ranking.
- No contextual priority re-ranking yet (the boundary is designed; the layer is not built).

## 7. Success criteria for the first slice

Replaying `fixtures/meetings/jp_inventory_mismatch.jsonl` produces, via deterministic logic and a
WebSocket stream to the UI:

1. the `inventory_mismatch` pain point with evidence,
2. the correct known facts (frequency, correction_process),
3. the correct open gaps,
4. exactly one useful ASK NOW question with a reason,
5. retraction of that question once answered,
6. correct supersede/contradict/reopen behavior for the follow-on steps,

all asserted by an executable eval test. See `DEVELOPMENT_PLAN.md`.

## 8. Future direction (not commitments)

Local STT (whisper.cpp) and real meeting bridges (Vexa/Meet/Teams/Zoom) behind `TranscriptSource`;
local LLM extraction (Ollama) behind `AnalysisEngine`; a deterministic **contextual priority**
layer keyed on explicit signals (active business interruption, shipment/billing blocked, accounting
close impact, security risk, customer impact, explicit urgency) behind `PriorityStrategy`.
