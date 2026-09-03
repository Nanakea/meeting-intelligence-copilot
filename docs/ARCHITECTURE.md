# Architecture — Meeting Intelligence Copilot

How the system is wired. Companion to `DOMAIN_MODEL.md` (the contracts and state machine) and
`PRODUCT_SPEC.md` (the behavioral guarantees). The guiding constraint: a **pure, deterministic
domain** surrounded by **adapter ports**, so meeting platforms and model providers are swappable and
never leak into business logic.

---

## 1. Principles

1. **Hexagonal / ports-and-adapters.** The domain depends on abstractions (ports); concrete adapters
   depend on the domain, never the reverse.
2. **Deterministic core.** State transitions and gap calculation are pure functions. Models may
   extract or propose; the reducer is the sole source of truth (CLAUDE rules 7–9).
3. **Event log is ground truth.** `MeetingState` is a deterministic fold over an append-only log.
4. **Backend owns state.** The frontend renders **versioned full snapshots**; it holds no
   authoritative state of its own.
5. **Local-first.** No cloud LLM in the runtime. First slice runs entirely on JSONL replay.

### Documentation consistency boundary

The API v13 consistency engine is a separate review aggregate around the pure comparison service.
`DocumentClaim` and `ObservedSystemFact` enter through evidence, ERP-metadata, repository/API, and
CMDB adapters. `ConsistencyFinding` is stored in the encrypted assurance repository and may propose
unconfirmed solution-thread edges. It never imports into or mutates `MeetingState`.

NetSuite and WMS analytics use the same adapter boundary. Vendor adapters return bounded,
allow-listed, live-only projections; `app/services/erp_analytics.py` performs pure aggregation and
source comparison. No remote row is written to the evidence index, and the WMS transport exposes
GET only.

Amazon FBA is a third isolated adapter rather than a dependency of the WMS adapter. The adapter
exchanges an encrypted LWA refresh credential for a short-lived token, then exposes only fixed
FBA Inventory and Fulfillment Inbound GET projections. The pure
`app/services/fulfillment_reconciliation.py` service compares configured flow keys across
NetSuite → WMS and WMS → FBA. Its findings are transient review items outside `MeetingState`.

Deterministic extraction handles explicit identifiers, mappings, versions, environments, fields,
tests, deployments, ownership, and controls. The optional loopback Ollama adapter can only propose
literal-span-backed claims; a revision-checked user confirmation is required before comparison.
Remote exact values are retained only according to their access-lease policy, while hashes and safe
citations support later refresh and review.

The solution-thread mind-map projection is computed from the encrypted graph read model rather than
from raw documents. Its bounded traversal, safe public identifiers, source fingerprint, retrieval
hints, and Mermaid output avoid repeatedly sending large evidence bodies through downstream
processing. Suggested edges remain visually distinct and cannot enter confirmed impact analysis.

---

## 2. Component overview

```
                    ┌──────────────────────── backend (Python 3.12, asyncio) ────────────────────────┐
                    │                                                                                 │
 fixtures/*.jsonl ─▶│  TranscriptSource(port)                                                         │
 (later: whisper,   │        │  yields TranscriptEvent (platform-neutral)                             │
  Vexa, Meet, ...)  │        ▼                                                                        │
                    │   Orchestrator (asyncio)                                                         │
                    │        │  assign seq, append to event log (dedup by event_id)                   │
                    │        ▼                                                                         │
                    │   AnalysisEngine(port) ── rule engine now / Ollama later                        │
                    │        │  AnalysisResult { facts(+relation), pains, gap hints }                 │
                    │        ▼                                                                         │
                    │   Reducer (pure)  ── applies transitions, supersede/contradict, gap reopen      │
                    │        ▼                                                                         │
                    │   PriorityStrategy(port) ── TemplatePriority now / Contextual later             │
                    │        ▼                                                                         │
                    │   SuggestionSelector (pure) ── 1 ask_now + ≤2 follow_up, retraction             │
                    │        ▼                                                                         │
                    │   StateStore(port)  +  StatePublisher(port) ── in-memory + WebSocket            │
                    │                                   │                                             │
                    └───────────────────────────────────┼─────────────────────────────────────────────┘
                                                        ▼  versioned MeetingState snapshot (JSON)
                    ┌──────────────────────── frontend (React + TS + Vite) ─────────────────────────┐
                    │  WS client → meetingStore (latest snapshot) → panels:                          │
                    │  PainPointPanel · KnownFactsPanel · MissingInfoPanel · SuggestionCard          │
                    └───────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Layers and directories

The repository is a monorepo: `apps/api` (Python backend), `apps/web` (React frontend),
`contracts/` (the shared JSON-Schema contract of record), `evals/` (fixtures + golden trajectories,
used from Slice 1), and `scripts/check.ps1` (the network-disabled Windows verification entrypoint).
First-time package setup is a separate explicit `scripts/bootstrap-dev.ps1` action.

Backend layers live under `apps/api/app/`:

- `app/domain/` — **pure**. `contracts.py` (and, later, `reducer.py`, `suggestion_selector.py`,
  `ports.py`). `semantic_candidates.py` is the internal, provider-neutral Phase 4A observation schema
  and structural validator; it is not a wire contract or state-mutation path. No I/O, no vendor SDKs,
  no imports from `app/adapters/`.
- `app/services/` — **pure logic**, still domain-facing: the analysis engine (`rule_engine.py`,
  `pain_templates.py`) and orchestration wiring arrive here in later slices. The Phase 4A
  `SemanticCandidateAnalyzer` protocol has only a null implementation and is not composed into the
  runtime. No vendor SDKs.
- `app/adapters/` — **impure edges**: transcript sources (`jsonl_replay`), stores, publishers.
  Vendor/platform code lives only here. Phase 4C includes an uncomposed, loopback-only Ollama
  semantic-candidate adapter with a safe-off selector. The live production bridge also lives here:
  `live_adapters.py` reserves stable adapter names (`meetily`, `zoom`, `google_meet`, `teams`),
  `meetily_live.py` normalizes the current local Meetily payload, and future direct platform
  adapters can land behind the same seam without changing the pure layers.
- `app/api/` — FastAPI app + WebSocket endpoint (`main.py`), the **composition root** where concrete
  adapters are selected and wired. May import FastAPI; the pure layers may not.

Frontend lives under `apps/web/src/`: `types/` (contracts mirror), `hooks/` (the WS + version-guard
`useMeetingSocket`), and `components/` (the panels).

**Boundary enforcement.** A pytest import-boundary contract (AST scan in `tests/test_slice0.py`)
forbids `app/domain/` and `app/services/` from importing `app/adapters/` or any vendor package.
This makes CLAUDE rule 2 mechanical, not aspirational.

---

## 4. Ports (adapter boundaries)

| Port | Signature (conceptual) | Slice-1 impl | Later impl |
|------|------------------------|--------------|------------|
| `TranscriptSource` | `async iterate() -> AsyncIterator[TranscriptEvent]` | `JsonlReplaySource` | whisper.cpp STT, Vexa, Meet/Teams/Zoom bridges |
| `AnalysisEngine` | `analyze(event, state) -> AnalysisResult` | `RuleEngine` (evidence-only) | `OllamaAnalysisEngine` (may emit inference) |
| `PriorityStrategy` | `rank(open_gaps, state) -> list[gap]` | `TemplatePriorityStrategy` | `ContextualPriorityStrategy` |
| `StateStore` | `get / put MeetingState`, `append event` | `InMemoryStore` | SQLite/Postgres |
| `StatePublisher` | `publish(MeetingState)` | `WebSocketPublisher` | (unchanged) |
| `ContextProvider` | `health / sync / search` | local files | Graph, OData, SAP, Dynamics |
| `GovernanceRepository` | `candidate / review / record / event / delete` | encrypted SQLite/WAL | future workspace adapter |
| `GovernanceQuery` | bounded portfolio and brief queries | local workspace | future shared read model |
| `GovernanceExport` | reviewed, redacted export bytes | Meetily local export | future policy adapter |

STT sits **behind** `TranscriptSource`: it produces `TranscriptEvent`s, so swapping JSONL for live
audio never touches the domain. Model providers sit **behind** `AnalysisEngine`. This satisfies the
requirement that domain logic never depends directly on Ollama, whisper.cpp, Vexa, Meet, Teams, Zoom.

External business context sits behind `ContextProvider` and remains outside `MeetingState`. The
adapter-layer broker applies current-user ACLs, timeouts, bounded retrieval, sanitization, and
freshness labels. Context citations may accompany the rendered question or an issue draft, but only
transcript-backed analysis can mutate pain points, facts, gaps, or suggestions.

Selected Notion pages use a narrow adapter that exposes only page-markdown reads. Raw page IDs are
held in the DPAPI-encrypted source-selection store and credentials remain in Windows Credential
Manager. Slack uses selected-channel search scopes only. Neither adapter persists remote content or
implements provider writes. Zoom requires no connector: Meetily captures local meeting audio and
delivers explicit-language transcript events through the existing live adapter.

Confirmed solution governance is a second aggregate, not an extension of `MeetingState`.
Deterministic candidate detection cites exact transcript event IDs; explicit review creates an
append-only revisioned record in user-scoped encrypted SQLite/WAL storage. The compact live
projection contains safe pending-candidate summaries and portfolio alerts, never internal evidence
IDs. Confirmed glossary entities and confirmed dependency records form the local system landscape;
impact analysis traverses at most two hops and ignores unconfirmed connector relationships.

---

## 5. Event and state flow (runtime)

1. **Source → queue.** `TranscriptSource` yields `TranscriptEvent`s (replay scheduler emits them at
   fixture-driven intervals to simulate real time). Only `is_final` events drive analysis in early
   slices.
2. **Ingest.** Orchestrator assigns `seq`, appends to the event log, dedups on `event_id`
   (idempotent — invariant I7).
3. **Analyze.** `AnalysisEngine.analyze(event, state)` returns an `AnalysisResult`: proposed
   facts (each with an optional `relation`: `none | supersede | contradict`), pain points, and slot
   hints. The engine **proposes**; it does not mutate state.
4. **Reduce.** The pure `Reducer` applies the deterministic transitions in `DOMAIN_MODEL.md` §4.4:
   insert/corroborate/supersede/contradict, then recompute slot occupancy and **reopen** any gap
   whose slot dropped to zero active facts (§4.5). Bumps `MeetingState.version`.
5. **Prioritize + select.** `PriorityStrategy.rank` orders open gaps (template order now);
   `SuggestionSelector` produces one `ask_now` + ≤2 `follow_up`, retracting suggestions whose gaps
   are answered or no longer top-ranked.
6. **Persist + publish.** `StateStore` saves the new state; `StatePublisher` pushes the full
   versioned snapshot over WebSocket. In live mode, the adapter layer also keeps a bounded local
   append-only cache of transcript events plus the latest `MeetingState`, so a restarted backend can
   recover an active session before reconnecting clients ask for it.
7. **Render.** Frontend replaces its held snapshot and re-renders the four panels. Because suggestion
   identity is derived from `gap_id`, cards are stable and retraction is unambiguous.
8. **Review governance separately.** Candidate detection runs after an event is accepted, but no
   durable governance state changes until confirm/dismiss review. Normal recording cleanup retains
   reviewed governance; permanent meeting deletion removes its provenance and appends a review event
   when a multi-source record remains.
9. **Retrieve evidence separately.** `EvidenceRepository` stores user-scoped encrypted local
   document revisions and sections. `RetrievalEngine` performs deterministic FTS5/BM25 retrieval
   and optional hash-pinned local ONNX reranking. Graph and ERP results are processed in memory and
   discarded; neither path can mutate `MeetingState`.
10. **Review assurance separately.** Deterministic `AssuranceRule` evaluation appends findings in
    encrypted SQLite/WAL storage. Signed company packs are admitted only after Ed25519 verification
    against a user-enrolled key. Findings and suggested graph links remain non-authoritative until
    explicit revision-checked review.

**Re-analysis discipline (risk R4).** Analysis runs per final event and is debounced; partial STT
tokens (later) are ignored until final. This avoids jitter and wasted work.

**Demo source registry (Slice 2).** The WebSocket selects a synthetic demo meeting from a fixed
server-side registry (`app/api/demo_registry.py`) keyed by meeting ID → (fixture, language). The
client never supplies a filesystem path, so there is no path-traversal surface. A known demo ID
replays the fixture; any other valid opaque session ID is a live session and remains open after an
empty compact v0 snapshot. Language is explicit source configuration on the replay or live adapter
— the domain never guesses language from character ranges.

---

## 6. Transport / protocol

- Single WebSocket per meeting session. Known fixture/demo sessions retain the full
  `MeetingState` replay contract. Live sessions receive versioned `LiveIntelligenceSnapshot`
  projections containing pains, facts, gaps, suggestions, and last sequence, but no transcript.
  Deltas are deferred; complete projections keep the frontend deterministic without repeatedly
  broadcasting transcript text that Meetily already owns.
- The live HTTP ingress is source-neutral: `POST /ingest/live/{session_id}` receives
  `{ adapter, lang, payload }`, while `POST /ingest/meetily/{session_id}` remains a compatibility
  alias for the current Meetily bridge. Adapter selection is still an adapter-layer concern, not a
  domain concern.
- Each ingest acknowledges `applied`, `buffered`, `duplicate`, or `rejected` together with the
  received sequence, next expected sequence, and state version. The bounded
  `POST /ingest/live/{session_id}/batch` endpoint accepts at most 200 ordered events for replay and
  gap repair and also reports rejected sequence IDs.
- The frontend ignores any snapshot whose `version` is not greater than the one it holds.
- Contract sync: the Pydantic contracts are the source of truth; TypeScript types mirror them, and a
  CI **schema-diff** check fails the build if domain contracts drift. Meetily separately mirrors and
  tests the transport-only compact live projection.

---

## 7. Concurrency model

- Live HTTP routes call the registry synchronously from one asyncio event-loop thread. Each call
  buffers by source sequence and drains only a contiguous run, guaranteeing deterministic order
  without mutating registry state from FastAPI worker threads.
- Multiple meetings (later) are isolated by `meeting_id`, each with its own state and log.
- The reducer and selector are synchronous pure functions; only the edges (source, store, publisher)
  are async. This keeps determinism testable without touching the event loop.

---

## 8. Testing hooks (see DEVELOPMENT_PLAN.md)

- The pure reducer/selector are unit-tested directly with hand-built states.
- The `AnalysisEngine` port lets the **eval harness** score any engine against golden fixtures; the
  same harness that asserts the rule engine will later *measure* the Ollama engine (precision/recall,
  no-repeat rate) without asserting exact non-deterministic output.
- Phase 4A candidate tests exercise the provider-neutral schema, fail-closed structural validation,
  a null provider, a test-only static provider, and representability of every annotation-only
  semantic eval case. They perform no network or model call.
- The composition root (`app.py`) is the only place to swap adapters, so integration tests wire a
  replay source + in-memory store + capturing publisher with no network.

---

## 9. Deferred by design (not built now)

Semantic candidate adaptation into `AnalysisResult`, semantic support checking, production/runtime
composition of the optional local Ollama adapter, whisper.cpp + real bridges (`TranscriptSource`),
durable `StateStore`, WS delta protocol, and the `ContextualPriorityStrategy`. Each has a named seam
above; none requires changes to the MeetingState wire contracts to add later.

The optional v8 embedding profile is not bundled or downloaded. Company-hosted synchronization,
multi-user authorization/RBAC, autonomous connector writes, cloud AI, and automatic governance
admission remain deferred. API v8 provides portable repository/query ports for those future hosting
choices without weakening the local authority boundaries.
