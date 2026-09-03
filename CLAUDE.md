# CLAUDE.md — Meeting Intelligence Copilot

Local-first, real-time **question copilot** for business meetings (JA/EN). It ingests live
transcript events, tracks meeting state, distinguishes evidence from inference, and privately
suggests the single highest-value question to ask next. It is **not** primarily a summarizer.

Read `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, and `docs/DOMAIN_MODEL.md` before changing
domain logic. This file is the short, binding contract; the docs are the detail.

## Non-negotiable rules

1. This product is a **question copilot, not primarily a meeting summarizer**.
2. Domain logic must **never depend directly on a meeting platform or model provider**
   (Ollama, whisper.cpp, Vexa, Google Meet, Teams, Zoom). Reach them only through adapter ports.
3. Every inferred `PainPoint` and `MeetingFact` must preserve transcript `evidence_event_ids`.
4. **Explicit evidence and inference must be represented separately** (`MeetingFact.kind`).
5. **Historical facts must remain traceable** when superseded or contradicted — never deleted.
6. Information gaps may **reopen only through explicit fact invalidation or contradiction logic**,
   never merely because a model suggests re-asking.
7. Prefer **deterministic logic** for known state transitions and gap calculation.
8. LLMs **may extract or suggest, but must not become the source of truth** for meeting state.
9. Question priority is **template-specific initially**. Do **not** introduce a universal ranking.
10. Display **only one ASK NOW** question and **at most two FOLLOW UP** questions.
11. Do **not** suggest an information gap already answered by a valid **active** fact.
12. Do **not** introduce cloud AI providers without explicit approval.
13. **No RAG** until the basic pain-point and question loop is proven.
14. Every implementation phase must have **executable verification**.
15. **Fix root causes.** Do not disable tests or weaken assertions to make checks pass.
16. Before adding a framework or major dependency, **explain the concrete problem it solves**.
17. Keep **Windows development supported**.
18. Use **pnpm** for the frontend.
19. When compacting context, preserve **modified files, architectural decisions, test commands,
    and unresolved failures**.

## Core mental model

- **Ground truth is an append-only event log.** `MeetingState` is a deterministic fold over it.
- A `PainPoint` is matched to a **pain template** that declares an ordered set of **slots**.
- A filled slot becomes a `MeetingFact`; an empty-but-relevant slot becomes an `InformationGap`;
  the highest-priority open gap becomes a `QuestionSuggestion` (ASK NOW / FOLLOW UP).
- **Facts carry two orthogonal axes:**
  - `kind` (provenance): `evidence` (stated) | `inference` (derived by the system).
  - `status` (lifecycle): `active` | `superseded` | `contradicted`.
- A slot is **filled** iff it has ≥1 `active` fact. A gap closes when its slot is filled and
  **reopens only** when explicit logic leaves the slot with **zero** active facts.
- Priority is two-staged: **base = template slot order** (built now); a future **deterministic
  contextual ranking layer** may adjust it via explicit signals (built later, behind a port).

## Architecture boundaries

- `app/domain/` and `app/services/` are **pure**: no I/O, no vendor SDKs, no `app/adapters/` imports.
- Ports: `TranscriptSource`, `AnalysisEngine`, `StateStore`, `StatePublisher`, `PriorityStrategy`.
- Backend is the single source of truth; it pushes **versioned full `MeetingState` snapshots** over
  WebSocket. The frontend is a pure render of the latest snapshot.

## Stack

- Backend: Python 3.12, FastAPI, Pydantic contracts, asyncio orchestration, pytest.
- Frontend: React + TypeScript + Vite, Vitest, **pnpm**.
- First slice uses **JSONL transcript replay fixtures** only. No Ollama, whisper.cpp, Vexa, RAG,
  auth, or production DB in the first vertical slice.

## Commands (planned — see docs/DEVELOPMENT_PLAN.md)

```
# Everything (Windows verification entrypoint)
powershell -ExecutionPolicy Bypass -File scripts/check.ps1

# API (apps/api)
cd apps/api && .venv/Scripts/python -m pytest     # run api tests
cd apps/api && .venv/Scripts/python -m ruff check .# lint (incl. import-boundary contract)

# Web (apps/web)
cd apps/web && pnpm install
cd apps/web && pnpm test                            # Vitest
cd apps/web && pnpm dev                             # Vite dev server
```

## Definition of done for any change

- Contracts updated on both sides if the WS/domain shape changed (schema-diff check passes).
- Deterministic tests cover new state transitions (including supersede / contradict / reopen).
- `evidence_event_ids` present on every new fact/pain point.
- No new suggestion when the target gap is answered by an active fact.
- Verification command shown and green. Root cause fixed, not masked.
