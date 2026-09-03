# Local Semantic Analyzer Plan

Status: Phase 5 provider-neutral comparison complete; the local Ollama candidate adapter remains
uncomposed and safe-off. The deterministic analyzer remains the production default. No new runtime
dependency, RAG, or cloud provider is added, and normal verification makes no model or network call.

## 1. Objective and non-goals

The next analyzer experiment should close the gap between a transcript's meaning and the current
keyword rules, especially for Japanese and English paraphrases. It should propose normalized
semantic facts while preserving the existing question-copilot contract:

- the event log and reducer remain the source of truth;
- every candidate keeps the transcript event IDs that support it;
- explicit evidence and inference remain separate;
- an analyzer cannot answer a gap, reopen a gap, or rank a question by itself;
- the deterministic analyzer remains available as the baseline and fallback.

This spike is not a request to replace the rule analyzer, add production LLM inference, reuse
Meetily's summary model, or introduce RAG.

## 2. Current deterministic inventory

The current `apps/api/app/services/analyzer.py` is pure, vendor-free, explicitly language
dispatched, and final-event gated. It emits evidence-only `AnalysisResult` proposals. The reducer
only applies facts to an already detected pain, then recomputes gaps and selects one ASK NOW plus
at most two FOLLOW UP questions.

| Area | Current deterministic coverage | Semantic gap worth evaluating |
|---|---|---|
| Japanese `data_mismatch` | Detects inventory/two-sided mismatch; fills correction process, frequency, source of truth, business impact, and explicit owner. | Paraphrased mismatch wording, affected scope, varied frequency and impact phrasing, and source/target relationships. |
| JA/EN `integration_failure` | Detects failed integrations; fills error signal, retry behavior, business impact, explicit owner, and bounded explicit directional transfer details (`source_system`, `target_system`, `failure_point`). | Indirect descriptions that omit one side of the relationship, do not state transfer direction, or have ambiguous multi-system scope. |
| Japanese `vague_requirement` | Detects a small set of vague usability requests; fills current problem and expected behavior; explicit owner is supported. | Acceptance criteria, priority, and paraphrased requests without the current marker phrases. |
| English `manual_work` | Detects copy/manual spreadsheet work; fills bounded impact/frequency/volume/time-cost expressions and explicit owner proposals. | Reason for manual work, success condition, and broader natural paraphrases such as “each workday.” |
| English `process_delay` | Detects explicit approval/process lead-time or bottleneck language and fills bounded current/normal lead time, impact, bottleneck, owner, and process step. | Broader delay phrasing, Japanese coverage, and multi-stage/scope relationships. |
| English `unclear_ownership` | Detects explicit unresolved ownership and fills bounded impact, stakeholders, decision process, escalation, owner, and current status. | Pronouns, indirect responsibility, Japanese coverage, and richer governance semantics. |
| Japanese owner extraction | Explicit person/team clauses and compact split-role values are supported for every owner-bearing template. Questions, vague future actions, and unresolved-owner statements are rejected. | Elliptical ownership, pronouns, indirect references, and richer multi-role semantics. |

Observed brittleness is mostly linguistic rather than lifecycle-related: substring markers are
not robust to inflection, paraphrase, omitted subjects, punctuation, honorifics, negation, or
code-switching. Some bounded ASR substitutions are already handled where real acceptance evidence
justified them (for example, Japanese homophones in the inventory/source-of-truth path). They
should not become a general-purpose fuzzy matcher.

## 3. Phase 4A semantic boundary

The provider-neutral observation boundary now exists in
`apps/api/app/domain/semantic_candidates.py`. A pure protocol and no-op implementation live in
`apps/api/app/services/semantic_candidate_analyzer.py`. They accept a supplied list of normalized
transcript events and return an untrusted `SemanticObservationBatch`; no production composition root
invokes this protocol yet.

The first future semantic adapter should be compatible with the current event-centric analyzer while
allowing a small read-only context window for paraphrases and references.

### Candidate input

```text
SemanticAnalysisInput
  event: TranscriptEvent                 # one normalized final event
  context_events: list[TranscriptEvent]  # optional, bounded recent final events
  active_template_ids: list[str]         # read-only context, not mutable MeetingState
  active_slots: list[tuple[str, str]]    # optional context for an answer to an open slot
```

`event` is the primary evidence source. `context_events` must be bounded and retain stable event
IDs; the provider must never receive raw platform payloads, mutable reducer objects, audio, or
provider-specific state. Non-final or duplicate events are rejected before this boundary. The
normalizer, rather than a model, decides whether a Meetily `is_partial` field is authoritative;
the current live path uses stable sequence identity because Whisper's `is_partial` is not reliable
finality.

### Candidate output

```text
SemanticObservationBatch
  pains: list[PainCandidate]
  facts: list[FactCandidate]

PainCandidate
  category: registered template
  title: localized display title or stable title key
  evidence_event_ids: non-empty IDs from the input
  kind: evidence | inference
  confidence: 0..1
  reason: non-blank observation rationale

FactCandidate
  pain_category_hint: registered template (optional at parse time; required by Phase 4A validation)
  slot: slot registered for that template
  value: normalized value
  evidence_event_ids: non-empty IDs from the input
  kind: evidence | inference
  confidence: 0..1
  reason: non-blank observation rationale
```

Provider-only support text or spans may be carried alongside a candidate for validation, but are
not a new wire contract and would be dropped after validation. A future merge gate may adapt a
validated result to the existing internal `AnalysisResult` / `ProposedFact` shape. Phase 4A does not.
No candidate output contains gaps, questions, lifecycle status, supersession decisions, or a
replacement `MeetingState`.

## 4. Phase 4A structural validation

Validation belongs at the adapter boundary and must fail closed. Pydantic is already part of the
project stack, so no new validation framework is justified.

The implemented validator rejects unknown categories, slots that do not belong to their hinted
category, blank fact values, invalid provenance kinds, missing evidence IDs, evidence IDs absent
from the supplied `TranscriptEvent` list, and confidence outside `0..1`. Candidate provenance is
preserved unchanged, including `inference`.

This is deliberately **structural validation only**. Phase 4A does not claim that a candidate value
is semantically true, does not compare support spans with transcript text, and does not adapt a
validated batch into `AnalysisResult`. That later semantic-quality/merge gate must prevent a
paraphrase from introducing an actor, system, number, or outcome absent from cited evidence.

Malformed batches fail as a whole and have no state-mutation path. `MeetingEngine`, the deterministic
analyzer, reducer, gap calculation, and question selection are unchanged. Confidence is observation
metadata only; it is not permission to close or reopen a gap or rank a question.

## 5. Merge and lifecycle ownership

The merge path is:

```text
TranscriptEvent
  -> deterministic baseline and/or semantic candidate adapter
  -> validated AnalysisResult proposals
  -> existing reducer
  -> active facts, historical facts, gaps, and suggestions
```

The reducer remains the only component allowed to create or update `MeetingFact`, `PainPoint`,
`InformationGap`, and `QuestionSuggestion` lifecycle state. In particular:

- a proposed fact for a category with no active pain is ignored, as today;
- an active fact with the same value corroborates and merges evidence IDs;
- a different value follows the existing explicit lifecycle logic, preserving superseded or
  contradicted history;
- a slot is filled only by an active fact, and a gap reopens only through explicit reducer logic;
- template-specific slot order remains the initial priority strategy;
- semantic confidence never becomes a universal priority score.

The first implementation should prefer one result per event and reuse the existing `AnalysisResult`
shape. Supporting multiple concurrent pain candidates is a separate robustness/eval change, not an
implicit side effect of adding a semantic provider.

## 6. Future fallback and failure behavior

The semantic provider is optional and best-effort. On unavailable provider, timeout, malformed output,
validation failure, or unsupported language, the deterministic analyzer's result for that event is
used. The event is still recorded and the meeting continues. There must be no duplicate reducer
application when a semantic attempt falls back.

Fallback is observable in adapter diagnostics for testing, but it does not add a new user-facing
state or make the panel wait for a model. Existing WebSocket snapshots, evidence traceability, and
question caps are unchanged.

## 7. Provider decision and Phase 4C adapter

Phase 4C adds `OllamaSemanticCandidateAnalyzer` under `app/adapters/analysis/`. It is isolated from
the deterministic analyzer and Meetily's summary-coupled provider contract. It accepts only HTTP
loopback base URLs (`127.0.0.1`, `localhost`, or `::1`), uses a bounded final-event window and a
five-second default timeout, requests structured JSON, and returns only a batch that passes both
Pydantic parsing and Phase 4A structural validation. Malformed JSON, unknown categories or slots,
missing/invented evidence IDs, timeouts, and transport failures all return an empty batch.

The selection helper recognizes `MEETING_INTELLIGENCE_SEMANTIC_ANALYZER=ollama`; absent or unknown
values select `NullSemanticCandidateAnalyzer`. Optional settings are
`MEETING_INTELLIGENCE_OLLAMA_URL` (default `http://127.0.0.1:11434`) and
`MEETING_INTELLIGENCE_OLLAMA_MODEL` (default `qwen2.5:7b`). The FastAPI composition root and
`MeetingEngine` do not call this selector yet, so setting the variable alone does not alter V1
runtime behavior. Tests use injected in-memory transports only.

Phase 4C does not install Ollama, download a model, add RAG, or change the default composition root.
`scripts/check.ps1` remains fully runnable without Ollama or network access, and executable tests
guard the safe-off selector and fail-closed behavior. This adapter never logs transcript content.

## 8. Small semantic evaluation set

These are analyzer-level cases, not new production goldens yet. IDs are stable annotation IDs; a
future eval runner should replay each as a final `TranscriptEvent` and compare validated candidates
with the expected slot/value/evidence tuple. “Current baseline” describes the expected deterministic
result today, not the desired future result. The machine-readable annotations are in
`evals/semantic/local-semantic-paraphrases.json`.

| ID | Language and paraphrase | Expected semantic facts | Kind | Current baseline |
|---|---|---|---|---|
| `ja-sem-01` | `営業日のたびにこの転記作業が発生します。` | `manual_work.frequency = 毎営業日` | evidence | No JA `manual_work` detector; no fact. |
| `ja-sem-02` | `出荷の締め作業が1時間ほど後ろ倒しになります。` | `data_mismatch.business_impact = 出荷締め作業が約1時間遅延` | evidence/normalized | No matching deterministic impact phrase. |
| `ja-sem-03` | `一次対応は山田さん、最終判断は情シスです。` | `owner = 一次対応: 山田さん / 最終判断: 情シス` | evidence | Current owner helper supports the pattern family; `最終判断` is an explicit extension case for evaluation. |
| `ja-sem-04` | `EC側のCSVをSAPに取り込むところで止まります。` | `integration_failure.source_system = EC; target_system = SAP; failure_point = CSV取り込み` | evidence/normalized | Supported by the deterministic directional-transfer grammar; retained as a semantic parity/regression case. |
| `en-sem-01` | `We have to do the transfer every workday, not just on reporting mornings.` | `manual_work.frequency = every business day` | evidence/normalized | “workday” is not in the current frequency vocabulary; likely no fact. |
| `en-sem-02` | `The shipping cutoff slips by about an hour because of the spreadsheet handoff.` | `manual_work.business_impact = delays shipping cutoff by about 1h` | evidence/normalized | Pain may match only if the transfer/copy anchor is also present; impact wording is not normalized today. |
| `en-sem-03` | `Yamada owns first response, while IT gives final approval.` | `owner = first response: Yamada / final approval: IT` | evidence/normalized | No native EN owner extraction. |
| `en-sem-04` | `The CSV import from EC into SAP fails before the update reaches Salesforce.` | `integration_failure.source_system = EC; target_system = SAP; failure_point = CSV import` | evidence/normalized | No EN integration detector or system-relationship facts. |

Negative guards belong in the same eval set:

| ID | Utterance | Expected result |
|---|---|---|
| `ja-sem-neg-01` | `担当はどなたですか？` | No `owner` fact; this is a question. |
| `en-sem-neg-01` | `Someone should probably own this later.` | No `owner` fact; this is vague future intent, not evidence of an owner. |

The Phase 4A tests load every annotation (including the negative guards), construct the corresponding
candidate batch, and prove that its expected category/slot/value/kind/evidence shape is representable
and structurally valid. They do not claim model accuracy because no model is running.

Phase 4B adds an executable offline harness in `app/evals/semantic_candidate_eval.py` and
`scripts/eval_semantic_candidates.py`. It validates the annotation schema and candidate batches,
runs the existing deterministic analyzer on each event without applying reducer state, and compares
exact category/slot/value/kind/evidence tuples. `docs/SEMANTIC_EVAL_REPORT.md` is generated from that
result and guarded by a test. The annotation suite remains separate from deterministic golden state
trajectories.

A later evaluator should score slot-level precision/recall, normalized-value agreement, evidence-ID
precision, and negative-guard false positives. It should also assert that all existing deterministic
goldens remain byte-for-byte behaviorally unchanged, that a malformed semantic result falls back,
and that no candidate creates a suggestion for an already answered active fact.

Phase 5 turns the executable baseline into a decision report. It identifies deterministic coverage,
semantic-help candidates, unsafe/ambiguous relationships, negative guards, and a failure taxonomy for
paraphrase, STT variation, slot ambiguity, scope ambiguity, ownership ambiguity, and category
confusion. The recommendation is **do not enable yet**: the local CLI was present but no service was
running, so no live model quality claim exists. Mocked adapter safety is not evidence of extraction
precision.

## 9. Acceptance gates for a later implementation

Before any provider is enabled in production, add tests for:

- schema validation and unknown-slot rejection;
- evidence-ID and support-span preservation;
- JA/EN paraphrase cases above plus question and vague-action guards;
- semantic success, timeout, malformed output, and deterministic fallback;
- supersede/contradict/reopen behavior through the existing reducer;
- no new suggestion when an active fact answers the target gap;
- unchanged existing golden trajectories, WebSocket schema, import boundaries, and Windows
  `scripts/check.ps1` verification.

Promotion should require an explicit comparison report against the deterministic baseline. A
reasonable initial proposal is high evidence-linked precision, useful recall on the annotated
paraphrases, zero negative-guard false positives, and no regression in existing goldens; exact
thresholds must be agreed before implementation rather than inferred from model confidence.

## 10. Recommended order

1. **Done:** keep the deterministic analyzer as the default and document the annotated cases.
2. **Done in Phase 4A:** add candidate models, fail-closed structural validation, a null provider,
   and a test-only static provider; do not call Ollama.
3. **Partially done in Phase 4B:** measure exact deterministic candidate coverage with a provider-free
   harness. Semantic support quality and live-provider fallback evaluation remain deferred.
4. **Done in Phase 4C:** add an opt-in, loopback-only local provider adapter behind a safe-off
   selector. Production runtime composition remains deferred pending evaluation.
5. **Done in Phase 5:** document the comparison and keep the provider uncomposed because live
   semantic precision and negative-guard performance are not yet established.
6. Keep RAG, cloud providers, broad contextual ranking, and production rollout out of this slice.
