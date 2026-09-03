# Semantic Candidate Eval Report

Status: Phase 5 provider-neutral comparison. No model, network, cloud service, or
MeetingState mutation participates in the automated evaluation.

## Summary

- Annotation cases: 10
- Expected positive candidates: 18
- Exact deterministic candidate matches: 5
- Fully covered positive cases: 0
- Partially covered positive cases: 3
- Positive cases requiring semantic extraction: 5
- Negative guards passed: 2
- Negative guards failed: 0

Exact matching includes category, slot, normalized value, provenance kind, and
evidence IDs.
A deterministic value with a different provenance kind is intentionally not counted as an
exact match.

## Coverage matrix

| Case | Lang | Classification | Exact candidates | Deterministic assessment |
|---|---|---|---:|---|
| `ja-sem-01` | ja | `semantic_required` | 0/2 | No exact candidate coverage |
| `ja-sem-02` | ja | `partial` | 1/2 | Some exact coverage; semantic extraction still needed |
| `ja-sem-03` | ja | `semantic_required` | 0/1 | No exact candidate coverage |
| `ja-sem-04` | ja | `partial` | 2/4 | Some exact coverage; semantic extraction still needed |
| `en-sem-01` | en | `semantic_required` | 0/2 | No exact candidate coverage |
| `en-sem-02` | en | `semantic_required` | 0/2 | No exact candidate coverage |
| `en-sem-03` | en | `semantic_required` | 0/1 | No exact candidate coverage |
| `en-sem-04` | en | `partial` | 2/4 | Some exact coverage; semantic extraction still needed |
| `ja-sem-neg-01` | ja | `negative_guard` | 0/0 | No candidate emitted (guard preserved) |
| `en-sem-neg-01` | en | `negative_guard` | 0/0 | No candidate emitted (guard preserved) |

## Missing exact candidates

### `ja-sem-01`

- `fact:manual_work.frequency=毎営業日|kind=evidence|evidence=ja-sem-01`
- `pain:manual_work|kind=evidence|evidence=ja-sem-01`

### `ja-sem-02`

- `fact:data_mismatch.business_impact=出荷締め作業が約1時間遅延|kind=evidence|evidence=ja-sem-02`

### `ja-sem-03`

- `fact:data_mismatch.owner=一次対応: 山田さん / 最終判断: 情シス|kind=evidence|evidence=ja-sem-03`

### `ja-sem-04`

- `fact:integration_failure.source_system=EC|kind=inference|evidence=ja-sem-04`
- `fact:integration_failure.target_system=SAP|kind=inference|evidence=ja-sem-04`

### `en-sem-01`

- `fact:manual_work.frequency=every business day|kind=evidence|evidence=en-sem-01`
- `pain:manual_work|kind=evidence|evidence=en-sem-01`

### `en-sem-02`

- `fact:manual_work.business_impact=delays shipping cutoff by about 1h|kind=evidence|evidence=en-sem-02`
- `pain:manual_work|kind=evidence|evidence=en-sem-02`

### `en-sem-03`

- `fact:manual_work.owner=first response: Yamada / final approval: IT|kind=evidence|evidence=en-sem-03`

### `en-sem-04`

- `fact:integration_failure.source_system=EC|kind=inference|evidence=en-sem-04`
- `fact:integration_failure.target_system=SAP|kind=inference|evidence=en-sem-04`

## Decision coverage matrix

| Bucket | Evidence in this suite | Decision implication |
|---|---|---|
| Deterministic covered | JA data-mismatch pain; JA/EN integration pain/failure point; both negative guards | Preserve as baseline and fallback |
| Semantic expected to help | 13 missing exact candidates across JA/EN paraphrase, impact, ownership, and English integration cases | Evaluate locally before any composition |
| Unsafe or ambiguous | Source/target provenance, contextual owner attachment, and the Salesforce scope in `en-sem-04` | Require evidence/provenance review; never auto-promote |
| Negative guard | JA owner question and EN vague future ownership intent emit nothing | Zero false positives remains a promotion gate |

## Failure taxonomy

| Failure class | Current evidence | Required gate |
|---|---|---|
| Paraphrase | Workday frequency and shipping-cutoff impact are missed | Normalized-value precision/recall |
| STT variation | Not isolated by this clean-text annotation suite | Add bounded real-STT variants before enablement |
| Slot ambiguity | Impact and failure wording may map to neighboring slots | Exact slot precision with evidence review |
| Scope ambiguity | `en-sem-04` mentions SAP and Salesforce in one flow | Do not infer target scope without explicit support |
| Ownership ambiguity | Split roles require active-template context and role preservation | Reject vague/future owners; preserve role text |
| Category confusion | EN spreadsheet handoff may indicate manual work without keyword anchors | Compare category precision against negative meetings |

## Live provider status

A local Ollama CLI was detected on the evaluation workstation, but no Ollama
service was running and no model call was made. Live local-model quality
therefore remains pending; mocked tests establish transport and fail-closed
safety only.

## Recommendation: do not enable yet

Keep the semantic provider uncomposed and safe-off. The current evidence proves the
candidate boundary, deterministic baseline, and adapter failure behavior, but it does
not establish semantic precision. A future local-only eval should score exact
candidates, provenance, evidence IDs, category confusion, and both negative
guards. Only then consider a narrow opt-in for selected slots; broad runtime
enablement is not justified.

## Interpretation

The deterministic analyzer preserves both negative guards but has limited
paraphrase and English integration/ownership coverage. Partial matches
demonstrate that the comparison measures the existing baseline rather than
assuming every annotation needs a model. The annotation suite remains separate
from deterministic state goldens; this report does not justify enabling a
semantic provider.
