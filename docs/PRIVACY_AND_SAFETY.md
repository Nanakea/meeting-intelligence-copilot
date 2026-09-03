# Privacy and Safety — Meeting Intelligence Copilot

Business meetings contain confidential and personal information (people, systems, financials,
customers). This product is **local-first** so that sensitive meeting content stays on the operator's
machine. This document states the data-handling posture and the safety guarantees that follow from
the architecture.

---

## 1. Data-handling principles

1. **Local-first, no cloud LLM in the runtime.** No meeting content is sent to a cloud AI provider.
   The first slice runs entirely offline on JSONL replay fixtures (PRODUCT_SPEC G10; CLAUDE rule 12).
   Adding any cloud provider requires **explicit approval** (rule 12).
2. **Data minimization.** The system retains only what it needs to track meeting state: the event
   log and the derived `MeetingState`. No audio is persisted in early slices (there is no audio yet).
3. **No real meeting data in the repository.** Only curated **synthetic** fixtures live under
   `fixtures/`. Real transcripts, recordings, and captured audio are git-ignored
   (`recordings/`, `transcripts/`, `data/local/`, `*.local.jsonl`, `*.wav`, …) and must never be
   committed.
4. **Local model weights and STT binaries are never committed** (`models/`, `*.gguf`, `*.bin`).
5. **Secrets never committed.** `.env*` (except `.env.example`), keys, and PEMs are git-ignored.

---

## 2. Privacy properties from the architecture

- **Adapter isolation.** Because the domain speaks only `TranscriptEvent` and reaches the outside
  world only through ports, the blast radius of any future integration (Vexa, Meet, Teams, Zoom,
  whisper.cpp, Ollama) is confined to a single adapter. Data egress can be reasoned about per adapter.
- **Ephemeral by default.** `StateStore` is in-memory in early slices; closing the session discards
  meeting state. A durable store is a later, deliberate decision with its own retention policy.
- **The suggestion channel is private.** ASK NOW / FOLLOW UP cards are shown only to the operator;
  they are not broadcast into the meeting.

---

## 3. Trust and correctness safety

The product influences what a human asks in a live business meeting, so **wrong-but-confident output
is the primary safety risk**. The design mitigates it:

- **Evidence traceability (G1).** Every `PainPoint` and `MeetingFact` carries `evidence_event_ids`,
  so any conclusion can be traced back to the exact transcript utterances. The operator can verify
  before acting.
- **Evidence vs. inference separation (G2, DOMAIN_MODEL §3).** The system never presents an inferred
  value as if it were explicitly stated. Inference is visibly marked and treated as softer.
- **Deterministic source of truth (G9).** State transitions and gap logic are deterministic and
  auditable. A model may extract or propose, but never decides meeting state (rules 8–9).
- **Auditable reopening (G7).** A resolved question reopens **only** when explicit fact-invalidation
  or contradiction logic leaves a slot with no valid active fact — never because a model felt like
  re-asking. This prevents nagging and keeps behavior explainable.
- **History retained (G6, I5).** Superseded and contradicted facts are kept, so the operator can see
  how understanding changed rather than facing silent rewrites.

---

## 4. Operational guardrails

- **No RAG** until the core pain-point/question loop is proven (rule 13) — avoids pulling external
  documents (and their provenance/leak risks) into the loop prematurely.
- **Dependency discipline (rule 16).** New frameworks/dependencies require a concrete justification,
  reducing supply-chain and data-exposure surface.
- **Boundary enforcement in CI.** The import-boundary check prevents domain code from importing a
  vendor SDK, so a provider cannot be wired in accidentally without going through an adapter and this
  review.

---

## 5. Human-in-the-loop positioning

The copilot **suggests**; the human **asks**. It never speaks in the meeting, sends messages, or
takes external actions. It is a private thinking aid for the operator, and its outputs are advisory.

---

## 6. Known future considerations (to revisit before those slices)

- **Local LLM (Ollama, Slice 4).** Local, but still gated; inference outputs must remain marked and
  non-authoritative. Prompts/outputs stay on-device.
- **Live capture (whisper.cpp / Vexa / platform bridges, Slice 5).** Introduces real audio and
  third-party meeting data. Requires an explicit retention and consent policy, per-adapter egress
  review, and confirmation that platform bridges do not exfiltrate content beyond what the operator
  authorizes.
- **Durable storage.** When a persistent `StateStore` is introduced, define retention, encryption at
  rest, and deletion controls before enabling it.
