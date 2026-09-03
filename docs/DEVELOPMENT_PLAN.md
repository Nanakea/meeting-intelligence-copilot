# Development Plan — Meeting Intelligence Copilot

Development proceeds in **small vertical slices**. Every slice ends with **executable verification**
(CLAUDE rule 14) and fixes root causes rather than weakening checks (rule 15). No slice introduces a
cloud provider, RAG, auth, or a production DB without explicit approval.

Legend: **DoD** = definition of done (must be green to close the slice).

---

## Slice 0 — Skeleton: contracts + transport, no intelligence

**Goal.** Prove the wire and the shapes end to end with an empty brain.

Scope:
- Backend scaffold (`pyproject.toml`, Python 3.12, ruff, pytest); package `copilot`.
- `domain/contracts.py`: all Pydantic models from `DOMAIN_MODEL.md` (incl. `kind`, `status`,
  relation fields) — types only, no logic.
- Frontend scaffold (Vite + React + TS + Vitest, **pnpm**); `types/contracts.ts` mirroring the
  Pydantic models; four empty panels.
- FastAPI WS endpoint that emits one empty, versioned `MeetingState`.
- Import-boundary lint contract (`domain/`, `analysis/` may not import `adapters/` or vendors).
- Schema-diff check: exported Pydantic JSON Schema vs. TS types.

**DoD.**
- `cd backend && python -m pytest` green (contract round-trip test).
- `cd frontend && pnpm test` green (renders empty panels from a snapshot).
- Import-boundary lint and schema-diff checks pass.

---

## Slice 1 — The target vertical slice (JA inventory mismatch)

**Goal.** Prove: fake JA meeting → transcript events → pain point → known facts → missing info →
one useful ASK NOW question → UI card; and correct retraction + reopening.

Scope:
- `fixtures/meetings/jp_inventory_mismatch.jsonl` (the `PRODUCT_SPEC.md` §5 + §5a scenario).
- `adapters/transcript/jsonl_replay.py` implementing `TranscriptSource`.
- `analysis/pain_templates.py` with the `data_mismatch` template (slots, JA fill patterns, JA
  question templates) — see `DOMAIN_MODEL.md` §2.
- `analysis/rule_engine.py` implementing `AnalysisEngine` (**evidence-only**), emitting facts with
  `relation` (`none | supersede | contradict`) via deterministic markers.
- `domain/reducer.py`: insert/corroborate/supersede/contradict + gap reopening (§4.4–§4.5).
- `domain/suggestion_selector.py`: 1 ask_now + ≤2 follow_up + retraction.
- `TemplatePriorityStrategy`, `InMemoryStore`, `WebSocketPublisher`, `orchestrator.py`, `app.py`.
- Frontend: WS client + `meetingStore` + `SuggestionCard`, `PainPointPanel`, `KnownFactsPanel`,
  `MissingInfoPanel`, rendering `kind` (evidence vs inference) distinctly.

**Verification (executable).**
- Unit (pytest): reducer idempotency (I7); evidence ids preserved (I1); supersede keeps slot filled;
  contradiction empties slot → gap reopens (I4, I8); selector cap 1+2 (I2); no suggestion on answered
  gap (I3); history retained (I5).
- **Eval golden test:** replay the fixture; assert the state trajectory — pain point present with
  evidence; known facts (`frequency`, `correction_process`); ASK NOW = `source_of_truth` with reason;
  after 「ECを正としています」 the ASK NOW retracts and `business_impact` promotes; after the
  supersede step `frequency` stays closed; after the contradiction step `source_of_truth` reopens.
- Frontend (Vitest): snapshot replace re-renders; retracted suggestion disappears; inference marked.

**DoD.** All of the above green; `PRODUCT_SPEC.md` §7 success criteria demonstrably met.

> **Scope note — delivered vs. deferred in the first Slice 1 pass.** The deterministic happy path is
> delivered: JA `evals/fixtures/inventory-mismatch-ja.jsonl` replay → analyzer → reducer
> (insert / corroborate / **supersede**) → gaps → template-priority selection → **retraction** →
> **promotion**, verified by `evals/golden/inventory-mismatch-ja.json` and `check.ps1`. The
> **contradict + gap-reopen** path (§4.4–§4.5 / PRODUCT_SPEC §5a steps 5–6) and the `relation`-based
> analyzer markers are **deferred** to when a contradiction fixture is added — the reducer preserves
> the lifecycle fields and carries `reopen_count` forward so that logic drops in without rework.

---

## Slice 2 — Generalize: data-driven templates, follow-ups, English

**Goal.** Remove scenario-specific shortcuts; support EN; exercise all four templates' priority.

Scope:
- Make templates fully data-driven (`integration_failure`, `manual_work`, `process_delay` added with
  their own slot orders — `DOMAIN_MODEL.md` §2).
- EN fill patterns + EN question templates; `TranscriptEvent.lang` drives selection.
- Second fixture in English exercising a different template.
- Confirm **no universal ranking** crept in (each template ranks independently — CLAUDE rule 9).

**DoD.** Eval goldens for ≥2 templates and both languages; unit tests for per-template ordering;
existing Slice 1 tests still green.

---

## Slice 3 — Robustness + eval maturity

**Goal.** Harden the deterministic core and formalize evaluation.

Scope:
- Out-of-order / duplicate event handling; `is_final` gating; debounce.
- Fixtures with multiple concurrent pain points and partially answered gaps.
- Multi-step supersede/contradict chains (e.g. reopen then re-answer).
- Eval harness emits **metrics** (gap precision/recall, no-repeat rate, reopen correctness) as a
  report, not just pass/fail — the interface later engines are scored against.

**DoD.** Metrics report generated in CI; robustness tests green; invariants I1–I8 covered.

---

## Slice 4 — Local LLM behind the port (Ollama)

**Goal.** Add a local model **without touching `domain/`**. Requires explicit approval (rule 12 is
about *cloud* providers; Ollama is local, but still gated behind this planned slice).

Scope:
- `adapters/analysis/ollama_engine.py` implementing `AnalysisEngine`; may emit `inference` facts
  (still carrying `evidence_event_ids` — `DOMAIN_MODEL.md` §3).
- Run the Slice 3 eval harness to **compare** Ollama vs the rule baseline (measure, don't assert
  exact text).
- Reducer remains the source of truth; the model only proposes facts/relations (rules 8–9).

**DoD.** Ollama engine selectable at the composition root; eval metrics reported; rule-engine goldens
unaffected (determinism preserved for the default engine).

---

## Slice 5 — Real transcript sources

**Goal.** Replace fixtures with live input behind `TranscriptSource`, domain untouched.

Scope: whisper.cpp STT source (partials → finals) and a Vexa/Meet/Teams/Zoom bridge, both producing
`TranscriptEvent`. Frontend and domain unchanged.

**DoD.** A live source drives the same pipeline; partials suppressed until final; end-to-end demo.

---

## Cross-cutting engineering standards

- **Verification is executable and named** for every slice (rule 14). PRs state the command and show
  it green.
- **Root-cause only** (rule 15): never skip/xfail a test or soften an assertion to pass CI.
- **Dependencies justified** (rule 16): adding a framework requires a written statement of the
  concrete problem it solves; default is stdlib + the already-chosen stack.
- **Windows-first dev** (rule 17): all commands runnable on Windows (PowerShell) and POSIX.
- **pnpm** for all frontend package operations (rule 18).
- **Context compaction** (rule 19): preserve modified files, architectural decisions, test commands,
  and unresolved failures across compaction.

## Standard commands

```
# Everything (Windows verification entrypoint)
powershell -ExecutionPolicy Bypass -File scripts/check.ps1

# API (apps/api)
cd apps/api && .venv/Scripts/python -m pytest
cd apps/api && .venv/Scripts/python -m ruff check .

# Web (apps/web)
cd apps/web && pnpm install
cd apps/web && pnpm test
cd apps/web && pnpm dev

# Eval harness (from Slice 1) — golden trajectories live under evals/
cd apps/api && .venv/Scripts/python -m pytest tests/eval
```
