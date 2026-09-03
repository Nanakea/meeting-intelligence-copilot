# Meetily Integration Research and V1 Integration Record

> **Historical evidence notice:** PASS records in this research log apply to the recorded V1
> revisions and synthetic runs, not automatically to the current API v8 working tree. Current
> implementation blockers and acceptance gates are tracked in `docs/INTERNAL_PILOT.md`.
>
> Status: the early sections preserve the architecture-only research trail; the implemented V1
> integration and its real-ingress evidence are recorded in the final section and supersede earlier
> provisional recommendations. The reproducible milestone procedure is in
> `docs/MEETILY_V1_RUNBOOK.md`.

## 2. Upstream version / commit / license inspected

- Upstream parent: `https://github.com/Zackriya-Solutions/meetily` at
  **`0281737d87d26352fb0adc78c8c0975f691b23d1`**.
- Integration copy: `<legacy-meetily-worktree>`, branch `spike/real-transcript-ingress`.
- Integration commit: **`09cf3b7f900d7365202bf7ed1e5ec2297f5adf25`**.
- HEAD commit date: **2026-06-05**
- Integration inspection date: **2026-07-13**
- License: **MIT** (`LICENSE.md`, © 2024 Zackriya Solutions) — permissive; fork/reuse allowed with attribution.
- Patch backup: `<legacy-patch-backup>\meetily-integration-09cf3b7.patch`.
- Backend recovery worktree: `<legacy-meetily-worktree>-recovery`, branch `spike/backend-recovery`, commit
  **`6045188e89ccc6bce61769b136f36b8071c914f8`**, based on session-id commit `87d5f15`.
- Recovery patch backup: `<legacy-patch-backup>\meetily-recovery-6045188.patch`.
- Current clean local-only copy: `<legacy-meetily-worktree>-local-only`, branch `spike/local-only`, commit
  **`5534dc84821b1b50e0b7106e2f3a6d15ffedea1c`**.
- Packaging inspection worktree: `<legacy-meetily-worktree>-packaging`, branch `spike/packaging-startup`,
  based on **`5534dc8`**.
- Caveat: the integration copy has unrelated, pre-existing deletions from the earlier damaged-tree
  recovery incident. They are not part of the integration and should not be restored or committed;
  use the clean local-only clone/worktree from `5534dc8` for future Meetily feature work.

## 1. Executive conclusion

Meetily is a **Tauri/Rust desktop capture host** that already solves everything we previously planned
to build around the intelligence engine: desktop shell, mic + system-audio capture, local Whisper/
Parakeet STT, a live `transcript-update` event, SQLite persistence, and a multi-provider summary/
Ollama layer. We should **stop building all of that.** Our differentiator — the deterministic
`TranscriptEvent → PainPoint → MeetingFact → InformationGap → template-priority → ASK NOW` engine and
its golden evaluation harness — is **not** present in Meetily and is the asset to protect.

**Implemented V1:** the pinned Meetily copy is the *Meeting Capture Host*, and our engine remains an
independently-versioned local Intelligence Engine behind its adapter boundary. The proven local
transport is HTTP push plus WebSocket snapshots: Meetily posts transcript payloads to the loopback
FastAPI backend, which remains the source of truth and broadcasts full versioned state to the Meetily
IntelligencePanel.
**Source-grounded fact:** Meetily already manages a local AI sidecar/service lifecycle — it bundles
native sidecars via Tauri `externalBin` (`binaries/llama-helper`, `binaries/ffmpeg`) and talks to its
AI backend over **localhost HTTP** (`APP_SERVER_URL = http://localhost:5167`), with graceful sidecar
shutdown (`summary::summary_engine::client::shutdown_sidecar_gracefully`). The earlier stdio JSONL
recommendation remains useful historical analysis, but it was superseded by the proven HTTP/WebSocket
path after real Meetily ingress testing. The engine core stays Python, preserving the deterministic
tests and golden fixtures; the product ASK NOW panel is ported into Meetily's existing React frontend.

Do **not** rewrite the engine in Rust just because Meetily is Rust — that discards the deterministic
eval harness for no product gain. The pure `domain/` + `services/` core remains vendor-independent;
the FastAPI/WebSocket adapter is the implemented local V1 transport and the Meetily panel is the
production-facing UI for this milestone.

---

## 3. Current Meetily architecture (source-grounded)

The supported app is the Tauri/Rust project under `frontend/src-tauri/src/`. `frontend/src-tauri/src/lib.rs`
declares the wired modules (lines 38–56): `analytics, api, audio, config, database, notifications,
ollama, onboarding, openai, anthropic, groq, openrouter, parakeet_engine, state, summary, whisper_engine`.

- **`audio/`** is the wired capture+transcription path. **`audio_v2/` is NOT declared in `lib.rs`** — it
  is dead/experimental and must not be used as the integration surface. (`lib_old_complex.rs`,
  `recording_commands.rs.backup`, `recording_saver_old.rs` are also legacy.)
- **`whisper_engine/`** and **`parakeet_engine/`** are the two STT backends, behind a provider trait.
- **`database/`** — SQLite via `sqlx` (queries in `api/api.rs` bind `meeting_id`).
- **`summary/`** — multi-provider LLM summary layer (`summary/mod.rs`: "OpenAI, Claude, Groq, Ollama,
  OpenRouter, CustomOpenAI" + "Templates for structured meeting summary generation"), with a
  **built-in AI sidecar** managed by `summary/summary_engine/client`.
- **`api/api.rs`** hardcodes `const APP_SERVER_URL = "http://localhost:5167"` — the app still talks to a
  **local backend server** (the top-level Python `backend/`, FastAPI) for some operations. Per the task
  we do not anchor on this legacy Python backend, but it is a real runtime dependency today.
- **`analytics/`** ships **PostHog cloud telemetry** (`analytics/analytics.rs:1` `use posthog_rs`,
  host `https://us.i.posthog.com`), opt-out via `disable_analytics`. Cloud LLM providers
  (`anthropic/openai/groq/openrouter`) are present. **Meetily is not local-only by default.**

## 4. Actual live transcript data-flow trace (source-grounded)

```
system audio + microphone (cpal capture)
        ↓   frontend/src-tauri/src/audio/ (capture/, devices/) → chunked PCM
audio chunks + VAD/level (audio/level_monitor.rs "audio-levels")
        ↓
transcription worker pool  audio/transcription/worker.rs  (start_transcription_task)
        ↓   provider trait  audio/transcription/provider.rs  → TranscriptResult { text, confidence, is_partial }
Whisper (audio/transcription/whisper_provider.rs → whisper_engine)
  or Parakeet (audio/transcription/parakeet_provider.rs → parakeet_engine; is_partial ALWAYS false)
        ↓   builds worker.rs:27  struct TranscriptUpdate { text, timestamp, source, sequence_id,
        ↓                        chunk_start_time, is_partial, confidence, audio_start_time,
        ↓                        audio_end_time, duration }
app.emit("transcript-update", &update)      audio/transcription/worker.rs:222
        ↓   (also persisted separately via save_transcript / RECORDING_MANAGER; DB linked by meeting_id)
Tauri event "transcript-update"
        ↓
frontend/src/services/transcriptService.ts:57  listen<TranscriptUpdate>('transcript-update', …)
        ↓   (reload path: invoke('get_transcript_history') → Transcript[])
live transcript UI (React)
```

**Payload shape (the boundary that matters), `TranscriptUpdate` (worker.rs:27–39):**
`text: String`, `timestamp: String` (wall-clock "14:30:05"), `source: String` ("Audio" = system /
"Microphone"), `sequence_id: u64` (monotonic per recording), `chunk_start_time: f64` (legacy),
`is_partial: bool`, `confidence: f32`, `audio_start_time: f64` (sec from recording start),
`audio_end_time: f64`, `duration: f64`.

**Verified, not assumed:**
- **`is_final` semantics:** there is **no `is_final`** — only `is_partial`. Final = `!is_partial`.
  **Whisper emits partials that are later revised; Parakeet never emits partials** (`parakeet_provider.rs:40`
  `is_partial: false`). So the "one region → possibly multiple emissions" problem is real for Whisper.
- **Speaker:** **no speaker/diarization field.** The only channel signal is `source` ("Audio" vs
  "Microphone"). No per-speaker identity exists.
- **Duplicate/revision:** partial segments for the same audio window can be emitted repeatedly and then
  superseded by a final; dedup/coalescing is a **consumer responsibility** (the event stream is not
  emit-once). Our adapter must handle this.
- **Language:** **not in the event.** Language is a global user preference read in
  `worker.rs:449/526` `get_language_preference_internal()`; Parakeet ignores it entirely
  (`parakeet_provider.rs:29–31`). One language per session at best.
- **meeting_id:** **not in the event.** Identity is implicit "current recording"; persistence attaches
  `meeting_id` (sqlx binds in `api/api.rs`), retrievable via `get_recording_meeting_name` /
  `get_transcript_history`.

## 5. TranscriptEvent field mapping

| Our field | Meetily source field | Exact source type | Mapping | Risk |
|---|---|---|---|---|
| `event_id` | *(none)* — derive from `meeting_id` + `sequence_id` (+ `is_partial`) | — | **derived** | **High** — must be stable *and* dedupe partial→final revisions; `sequence_id` is u64 per session |
| `meeting_id` | current recording (`get_recording_meeting_name`), DB `meeting_id` | String | **derived** (from session, not the event) | Medium — must resolve the active meeting id at ingest |
| `seq` | `TranscriptUpdate.sequence_id` | `u64` | **direct-ish** | Medium — partials also advance it; ordering vs our monotonic-final `seq` needs a rule |
| `speaker` | `TranscriptUpdate.source` only | String ("Audio"/"Microphone") | **derived/partial** | **High** — no diarization; at best "system" vs "me", not real speakers |
| `text` | `TranscriptUpdate.text` | String | **direct** | Low |
| `language` | global pref `get_language_preference_internal()` | Option<String> | **derived (config)** | **High** — not per-event; Parakeet ignores; our analyzer dispatches on explicit lang |
| `is_final` | `!TranscriptUpdate.is_partial` | bool | **derived** | Medium — Whisper revises partials; Parakeet all-final |
| `timestamp/offset` | `audio_start_time` / `audio_end_time` (`duration`) | f64 sec from start | **direct-ish** | Low — good relative offsets; wall-clock also available |

**Conclusion:** the contract is **sufficient but not a clean 1:1 map.** `text` and timing are direct;
`event_id`, `speaker`, `language`, `meeting_id`, and `is_final` are all **derived**, and the
partial-revision behaviour must be coalesced in the adapter. Do not modify the contract yet (see §18).

## 6. Meetily vs our current roadmap comparison

| Capability | Our previous plan | Meetily current implementation | Reuse Meetily? | Keep ours? | Integration risk |
|---|---|---|---|---|---|
| Desktop shell | build | Tauri app (`lib.rs`) | **Yes** | No | Low |
| Microphone capture | build | `audio/capture`, `audio/devices` (cpal) | **Yes** | No | Low |
| System-audio capture | build | `audio/system_audio_commands.rs`, `source="Audio"` | **Yes** | No | Med (OS perms) |
| Audio mixing | build | in `audio/` pipeline | **Yes** | No | Low |
| VAD | build | speech detection (`worker.rs` "speech-detected") | **Yes** | No | Low |
| Whisper | integrate later | `whisper_engine/` + `whisper_provider.rs` | **Yes** | No | Low |
| Parakeet | not planned | `parakeet_engine/` + `parakeet_provider.rs` | **Yes** | No | Low |
| Partial transcript display | build | `is_partial` + `transcript-update` | **Yes** | No | Med (revisions) |
| Final transcript handling | build | `!is_partial` + `save_transcript` | **Yes** | No | Med |
| Meeting lifecycle | build | `start_recording`/`stop_recording`, RECORDING_MANAGER | **Yes** | No | Low |
| Transcript storage / SQLite | build | `database/` (sqlx) | **Yes** | No | Low |
| Ollama | integrate later | `ollama/ollama.rs` (summary-coupled) | **Partial** (see §16) | Ours in Python | Med |
| Local model config | build | onboarding + model download commands | **Yes** | No | Low |
| Frontend meeting UI | build | `frontend/src/` React | **Yes** (host) | Ours as panel | Med |
| Summary generation | explicitly not our product | `summary/` multi-provider + templates | **Yes** (theirs) | No | Low |
| **PainPoint state** | **build (ours)** | **absent** | No | **Yes** | — |
| **MeetingFact lifecycle** | **build (ours)** | **absent** | No | **Yes** | — |
| **evidence_event_ids** | **build (ours)** | **absent** | No | **Yes** | — |
| **InformationGap engine** | **build (ours)** | **absent** | No | **Yes** | — |
| **Deterministic gap calc** | **build (ours)** | **absent** | No | **Yes** | — |
| **ASK NOW selection** | **build (ours)** | **absent** | No | **Yes** | — |
| **Stale-question retraction** | **build (ours)** | **absent** | No | **Yes** | — |
| **Golden meeting evals** | **build (ours)** | **absent** | No | **Yes** | — |

## 7. Duplication we should STOP building

Desktop shell; mic capture; system-audio capture; audio mixing; VAD; Whisper integration; Parakeet
integration; STT lifecycle/worker pool; partial/final transcript plumbing; meeting record lifecycle;
SQLite transcript schema/storage; local-model download/config/onboarding; the live transcript UI
shell; and the **entire summarization stack** (which is explicitly not our product). Our previous
"application host + ingestion" roadmap is largely obsolete.

## 8. Capabilities our engine uniquely adds (not in Meetily)

Deterministic pain-point detection; the two-axis `MeetingFact` model (evidence/inference ×
active/superseded/contradicted) with `evidence_event_ids` traceability; `InformationGap` computation
from template slots; **template-specific** question priority (6 categories, JA+EN); one-ASK-NOW /
≤2-FOLLOW-UP selection with stale-question retraction and next-gap promotion; and a **deterministic
golden evaluation harness** (5 fixtures, exact trajectory assertions). Meetily has none of this — it
summarizes; we tell the operator *what to ask next.* Do not overstate novelty: capture/STT/summary is
commodity and theirs; the **question-elicitation state machine + eval discipline** is ours.

## 9–12. Integration option analyses

### Option A — Fork Meetily and port the engine into Rust
- Rewrite cost: **high** (reducer/selector/analyzer/templates + 6 categories) with **no product gain**.
- **Loses the 69 Python tests and the golden harness** — our determinism moat — or forces a full
  re-port of them into Rust test infra.
- Pydantic contracts → Rust structs/serde migration; golden-trajectory semantics re-implemented.
- Pros: single runtime, simplest packaging, lowest latency (in-process), best long-term maintainability
  *if* the team is Rust-first.
- Verdict: **reject for now.** Only revisit if Python packaging proves untenable and the eval harness
  has been re-hosted. Rewriting because "Meetily is Rust" is the exact trap we were warned against.

### Option B — Fork Meetily, run our Python engine as a local sidecar
- IPC candidates (evaluate on merits, not "we already use WebSocket"):
  - **localhost WebSocket** — familiar (our demo uses it) but **opens a listening port** → LAN-exposure
    risk if bound to `0.0.0.0`, port conflicts, needs auth. **Reject as the default.**
  - **localhost HTTP** — same port/exposure problems; Meetily already couples to `localhost:5167`, so
    adding another HTTP port compounds surface. Reject.
  - **stdin/stdout JSONL child process** — **no socket, no port, no LAN surface**; OS-level pipe gives
    natural backpressure; lifecycle tied to parent; trivially LOCAL_ONLY. **Preferred.**
  - **named pipe / UDS** — viable, but Windows/macOS abstraction differences add complexity vs stdio.
  - **Tauri sidecar** — Meetily already ships a "built-in AI sidecar"
    (`summary/summary_engine/client::shutdown_sidecar_gracefully`), so bundling a second sidecar is a
    proven pattern in this codebase.
- Windows/macOS: stdio sidecar works on both; Python packaging (PyInstaller/embedded) is the main lift.
- Backpressure: pipe blocks naturally; WS needs manual flow control. Crash recovery: parent respawns
  child. Latency: sub-ms IPC, dominated by analysis. Privacy: transcript crosses a process boundary but
  stays on-device; stdio avoids network entirely.
- Verdict: **strong** — but frame it deliberately (Option D) rather than as a "temporary" sidecar.

### Option C — Keep our engine standalone + a Meetily adapter/plugin
- Requires a **supported Meetily extension/event API**. Meetily exposes Tauri **events**
  (`transcript-update`, etc.) and **commands** (`get_transcript_history`, `start_recording`…), but
  **there is no documented plugin/extension contract** for third parties to receive transcript events
  out-of-process without modifying the Rust source. **No supported plugin API exists.**
- Verdict: **reject** — without forking, we cannot subscribe to `transcript-update` from an external
  process; the events are in-app Tauri events, not an IPC surface.

### Option D — Fork Meetily; keep intelligence as an independently-versioned local engine/service
- Same mechanism as B (stdio sidecar) but as **deliberate product architecture**: a **Meeting Capture
  Host** (forked Meetily) and a **Meeting Intelligence Engine** (our repo), versioned separately, with
  a small, stable JSONL contract between them.
- Gives us: independent test/release cadence for the engine; the engine stays vendor-agnostic
  (unaware of Tauri/Rust/Whisper); the fork carries only a thin bridge (subscribe to `transcript-update`
  → normalize → write JSONL to the sidecar → render `MeetingState` in an ASK NOW panel).
- Verdict: **recommended.**

## 13. Historical recommended integration architecture (superseded by frozen V1)

Fork Meetily (Capture Host). In the fork's Rust layer, add a thin **bridge** that subscribes to the
existing `transcript-update` events, resolves the active `meeting_id` + language config, and forwards
normalized lines to our engine sidecar. Our engine runs unchanged as a **pure fold**:

```
Meetily audio/STT (unchanged)
  → transcript-update (TranscriptUpdate)
  → [fork bridge] normalize + dedupe partial/final → MeetilyTranscriptSource
  → TranscriptEvent (our contract)
  → MeetingEngine (unchanged: reducer/templates/selector)
  → MeetingState snapshot (JSONL back over the pipe)
  → ASK NOW / FOLLOW UP panel in Meetily's React UI
```

## 14. Historical IPC recommendation (superseded)

**Provisional: child-process stdio JSONL** (engine spawned by the host; one `TranscriptEvent` JSON per
line in, one `MeetingState` JSON per line out). No listening socket, no port, no auth surface, natural
backpressure, lifecycle bound to the host. The spike (below) validated it end-to-end and measured it
~15× lower latency than the HTTP prototype, **but** localhost HTTP is a **proven viable fallback** that
matches Meetily's *existing* sidecar convention (`localhost:5167`). This remains **PROVISIONAL** until a
real-audio spike; the decision hinges on privacy surface (stdio) vs consistency with Meetily's current
HTTP pattern, not on latency (both are sub-millisecond). This recommendation is superseded: the
implemented V1 uses loopback HTTP POST plus WebSocket because it required no new process lifecycle,
reused Meetily’s existing `reqwest` dependency, and was validated with real English and Japanese
ingress. Keep the historical comparison for context; do not use it as the current startup or
integration procedure.

## 15. Frontend strategy (implemented V1 supersedes the original recommendation)

Choice: **B + D** — retain our React/Vite UI **only as the engine/eval demo**, and **port a minimal ASK
NOW panel into Meetily's existing React frontend** (it is already React/TS). Smallest clean insertion
point: alongside `frontend/src/services/transcriptService.ts` (the transcript consumer), add a sibling
that receives `MeetingState` snapshots from the sidecar and renders a compact private panel:
**Active pain point · Known · Missing · one ASK NOW · ≤2 FOLLOW UP.** Reuse our existing panel logic
(pure renderers of `MeetingState`). Do **not** build a new dashboard; keep it low-clutter and glanceable.
In the frozen V1, the panel is implemented in Meetily and receives full state snapshots over the
loopback WebSocket.

## 16. Ollama strategy

Meetily's Ollama layer (`ollama/ollama.rs`) is a **streaming NDJSON client bound to the summary
multi-provider stack** (`summary/mod.rs`: OpenAI/Claude/Groq/Ollama/OpenRouter/CustomOpenAI) and its
**templates** — it is **summary-specific**, streaming, and **not** a generic schema-constrained
structured-output API. **Do not reuse it** for our future pain/question extraction. Keep any future
Ollama analyzer **inside our Python engine, behind our `AnalysisEngine` port**, so the intelligence
boundary owns its own model contract and stays testable. (Ollama remains deferred; not implemented now.)

## 17. Privacy / security considerations

- **Raw audio / transcript ownership:** stay on-device in Meetily; our engine only ever receives
  **text** `TranscriptEvent`s over a local pipe (no audio).
- **Process boundary:** transcript text crosses host→engine; stdio keeps it off the network. **Reject
  WebSocket/HTTP defaults** — they bind a port (LAN-exposure + port-conflict risk).
- **Cloud code paths in Meetily (must be neutralized for LOCAL_ONLY):** PostHog telemetry
  (`analytics/*`, `us.i.posthog.com`) and cloud LLM providers (`anthropic/openai/groq/openrouter`).
  Enforce LOCAL_ONLY in the fork by disabling analytics and cloud providers by default and asserting no
  outbound connections in the intelligence path.
- **Local backend:** `localhost:5167` is a real dependency today; document/limit it.
- **IPC auth:** stdio needs none (no listener). If ever socket-based, require loopback bind + token.
- **Temp files / logs / crash dumps:** ensure the engine writes no transcript to disk by default;
  scrub logs; disable core dumps of transcript content.
- **DB/retention:** Meetily's SQLite holds transcripts; our engine should be **stateless per meeting**
  (in-memory `MeetingState`) and add no new persistent store.
- **Subprocess env/secrets:** spawn the engine with a minimal env; no API keys needed (local only).
- No unsupported legal conclusions are drawn; this is an engineering privacy posture.

## 18. Impact on our domain contracts

The existing `TranscriptSource → TranscriptEvent` boundary **appears viable, and no domain-contract
change is currently proven necessary** (the spike drove the real engine through it unchanged). However,
**adapter-derived identity, speaker semantics, language provenance, and partial/final normalization
must be validated by a real-audio integration spike before the contract is frozen.** Watch items live
in the adapter, not (yet) the contract:
- `speaker`: Meetily provides only a channel (`source`). Our `Speaker` should tolerate a channel-derived
  or null speaker; our engine already does not depend on speaker identity for analysis.
- `language`: our analyzer dispatches on explicit `lang`; the adapter must set it from Meetily's global
  language config (not per-event). Fine as long as a meeting is single-language (matches our fixtures).
- `is_final`: map from `!is_partial`; **only feed `is_final` events into the engine** (our engine already
  ignores non-final), so Whisper partial-revisions never reach the reducer.
- Partial→final **text revision** is a transcription concern, **not** a `MeetingFact` supersede — do not
  conflate it with the deferred contradiction/supersede lifecycle.

## 19. Impact on our 69 backend tests and golden fixtures

**No proven impact** (spike ran the committed engine unchanged and reproduced the golden manual-work
trajectory exactly). `MeetilyTranscriptSource` is a *new*
adapter behind the existing `TranscriptSource` port; `ReplayTranscriptSource`, the 5 fixtures, the
golden harness, and all 69 tests remain the deterministic contract. We would add adapter-level tests
(normalization, partial-drop, dedupe, meeting_id/lang derivation) using recorded `TranscriptUpdate`
samples — but the engine core and its evals are untouched. This is the strongest argument for keeping
the engine independent (Option D).

## 20. Revised development roadmap

1. **Spike (no commit):** record real `TranscriptUpdate` streams from a forked Meetily; characterize
   partial/final cadence, `sequence_id` behaviour, and `meeting_id`/language retrieval.
2. Define the **stdio JSONL contract** (TranscriptEvent in / MeetingState out) + engine `--stdio` entry
   (a thin transport around the existing `MeetingEngine`; FastAPI/WS stays demo-only).
3. Implement `MeetilyTranscriptSource` (adapter) with dedupe + derivation; add adapter tests.
4. Fork Meetily; add the Rust bridge (subscribe `transcript-update` → sidecar) and the ASK NOW React
   panel; enforce LOCAL_ONLY (analytics + cloud providers off).
5. Later (still deferred): Ollama analyzer behind our port; contradiction/scoped-facts robustness phase.

## 21. Code we should NOT write anymore

Desktop shell; audio capture (mic/system); audio mixing; VAD; Whisper integration; Parakeet
integration; STT worker/lifecycle; partial/final transcript plumbing; meeting lifecycle/recording
manager; SQLite transcript schema/storage; model download/config/onboarding UI; live-transcript UI
shell; **summary/summarization engine**; a bespoke Ollama download/management layer; and **any FastAPI/
WebSocket server intended for production** (keep it demo-only). Reuse Meetily for all of these.

## 22. Open questions requiring a prototype spike

1. Exact Whisper **partial→final revision cadence** — does the same region re-emit overlapping text,
   and how do we coalesce it into one final `TranscriptEvent`?
2. Is `sequence_id` **monotonic, unique, and stable** enough to base `event_id` + dedupe on across
   partial/final?
3. When/where is the active **`meeting_id`** available at ingest (timing vs first transcript event)?
4. Is Meetily's **global language** reliably set before transcription starts (and how to handle
   Parakeet, which ignores language)?
5. **Python packaging** of the engine sidecar on Windows + macOS (size, startup latency, signing).
6. Does `source` ("Audio"/"Microphone") give us any useful **speaker** signal, or should `speaker` be
   null until diarization exists?
7. Does the Meetily team accept upstream **bridge hooks**, or do we maintain a thin long-lived fork?

---

### Assumptions explicitly challenged (as requested)

- **FastAPI in production →** No. The asset is pure `domain/`+`services/`; FastAPI/WS is demo transport.
- **WebSocket as local IPC →** No. Opens a port (LAN/port-conflict/privacy). Prefer stdio JSONL.
- **React/Vite UI as the product UI →** No. Keep it as eval demo; port an ASK NOW panel into Meetily.
- **Python as final engine language →** Keep for now (tests+golden moat); revisit only on packaging pain.
- **Meetily maps cleanly to TranscriptEvent →** No. `event_id/speaker/language/meeting_id/is_final` are
  all derived; partial-revision needs coalescing.
- **Meetily partial/final is sufficient →** Sufficient *if* we feed only finals; but no `is_final` field
  (derive from `!is_partial`), and Whisper revises partials.
- **Reuse Meetily's Ollama →** No. It is summary-template-coupled/streaming, not schema-constrained.
- **Forking preferable to independent integration →** Fork the *host*, keep the *engine* independent
  (Option D) — not an in-tree Rust rewrite.

### Most important upstream files / symbols to the conclusion

- `frontend/src-tauri/src/audio/transcription/worker.rs` — `struct TranscriptUpdate` (l.27), `emit("transcript-update")` (l.222), `get_language_preference_internal()` usage (l.449/526)
- `frontend/src-tauri/src/audio/transcription/provider.rs` — `struct TranscriptResult { text, confidence, is_partial }` (l.42)
- `frontend/src-tauri/src/audio/transcription/parakeet_provider.rs` — `is_partial: false` (l.40), language unsupported (l.29–31)
- `frontend/src-tauri/src/audio/transcription/whisper_provider.rs` — partial-capable Whisper path
- `frontend/src/services/transcriptService.ts` — `listen<TranscriptUpdate>('transcript-update')` (l.57), `get_transcript_history`
- `frontend/src-tauri/src/lib.rs` — wired module list (l.38–56; no `audio_v2`), `invoke_handler` (l.526)
- `frontend/src-tauri/src/api/api.rs` — `APP_SERVER_URL = "http://localhost:5167"` (l.20), sidecar shutdown (l.563)
- `frontend/src-tauri/src/summary/mod.rs` — multi-provider summary + templates; `summary_engine/client` sidecar
- `frontend/src-tauri/src/ollama/ollama.rs` — streaming NDJSON Ollama client (summary-coupled)
- `frontend/src-tauri/src/analytics/analytics.rs` — PostHog telemetry (`us.i.posthog.com`)
- `frontend/src-tauri/tauri.conf.json` — `externalBin: ["binaries/llama-helper","binaries/ffmpeg"]` (l.100), Win/macOS signing, updater
- `frontend/src-tauri/Cargo.toml` — `tauri = "2.6.2"`, `tauri-build = "2.3.0"`, `tauri-plugin-process`, `tauri-plugin-updater`
- `LICENSE.md` — MIT

---

## Transcript Bridge Spike Results

> Throwaway spike run entirely in a scratch dir (outside the product repo). It modeled Meetily's exact
> upstream types, normalized synthetic streams into our `TranscriptEvent`, and drove the **real
> committed `MeetingEngine`** (imported read-only) over two IPC transports. No product code changed.

**Environment.** Meetily commit **`0281737d87d26352fb0adc78c8c0975f691b23d1`** (pinned; not moving HEAD).
Tauri **2.6.2** (tauri-build 2.3.0), Meetily product `0.4.0`. Bundle uses `externalBin` sidecars
(`binaries/llama-helper`, `binaries/ffmpeg`), macOS hardened-runtime + entitlements, Windows sign
command, updater artifacts. Spike run with the project's Python 3.12 venv.

**Q1 — transcript normalization / coalescing (measured).** Modeled `TranscriptUpdate` (worker.rs:27)
and a normalizer that emits **only authoritative finals**; partials are retained for UI and never
returned to the engine. Results on the Whisper-style partial→final manual-work stream:
- 3 partials dropped, **4 finals emitted**, 3 partials retained for UI. ✔
- **No stable segment identity exists upstream** — Meetily gives no per-utterance id (`sequence_id` is
  per-emission). Coalescing rule: a run of `is_partial=true` closes when a `is_partial=false` final
  arrives; that final is the utterance. Verified edge cases:
  - final emitted twice → **second dropped** (consecutive-duplicate dedup). ✔
  - partial arriving **after** a final → treated as a new utterance, **not sent** to the engine. ✔
  - **Parakeet all-final path** (`is_partial` always false) → **each emission emitted once**. ✔
  - genuine repeat separated by a different final → **re-emitted** (not over-deduped). ✔
- `is_final = !is_partial` is **safe for both** Whisper (partials coalesced) and Parakeet (all final),
  *provided* only finals reach `MeetingEngine.apply()`.

**Q2 — TranscriptEvent mapping classification (from the working normalizer).**
`text` = **DIRECT**; `ts_start/ts_end` = **DIRECT** (`audio_start_time/audio_end_time`); `is_final` =
**DERIVED-STABLE** (`!is_partial`, finals only); `event_id` = **DERIVED-STABLE** (adapter-owned
`{meeting_id}:seg{final_seq}`); `seq` = **DERIVED-STABLE** (adapter monotonic final counter);
`meeting_id` = **DERIVED-STABLE** (Meetily session id); `speaker` = **DERIVED-BEST-EFFORT**
(`Microphone→"You"`, `Audio→"Remote"` — **source/channel identity, NOT human diarization**);
`language` = **DERIVED-BEST-EFFORT** (meeting-level config copied onto events; Parakeet ignores it).
**Event-ID strategy recommendation:** adapter-owned monotonic **final** sequence namespaced by
`meeting_id` (do not hash raw text — Whisper revisions and genuine repeats would collide/diverge).

**Contract risk flagged (not changed):** the field name **`speaker` is semantically misleading** for
Meetily — it will carry a *channel* ("You"/"Remote"), not a diarized human speaker. Our analysis does
not depend on speaker, so this is cosmetic today, but the name should be revisited before any
speaker-dependent feature.

**Q3 — IPC comparison (measured, sub-millisecond; spike numbers, not benchmarks).**
| Transport | p50 | p95 | max | startup | malformed | port/LAN |
|---|---|---|---|---|---|---|
| **stdio JSONL** (100) | 0.036 ms | 0.095 ms | 0.65 ms | (engine import) | handled (error line, no crash) | **none** |
| **stdio JSONL** (1000) | 0.036 ms | 0.10 ms | 0.24 ms | — | — | **none** |
| **localhost HTTP** (100) | 0.61 ms | 0.75 ms | 0.98 ms | ~660 ms | handled (HTTP 400) | 127.0.0.1 only; **port-conflict detected (OSError)** |
| **localhost HTTP** (1000) | 0.60 ms | 0.71 ms | 1.17 ms | — | — | loopback-only (no LAN) |

- **Ordering preserved** on both (stdio is strictly 1:1 over the pipe; HTTP is request/response).
- **Backpressure:** stdio blocks naturally on the pipe; HTTP relies on the server accept queue.
- **Clean shutdown:** stdio child exits rc=0 on stdin EOF; HTTP terminated cleanly. **stderr is
  separated from the stdout data channel** on stdio (verified).
- **Crash/shutdown caveat (Windows):** after killing the stdio child, the parent's next `stdin.write`
  did **not** raise immediately (Windows pipe buffering) — child-death must be detected via
  `proc.poll()`/read-EOF, not by relying on a write error. This is a real robustness note for either
  transport's supervisor.
- Latency is **not** a differentiator (both sub-ms, dwarfed by analysis). The real difference is
  **attack/leak surface**: stdio opens no socket; HTTP binds a port (loopback here, but a
  mis-configuration to `0.0.0.0` would expose the LAN) and can conflict on a fixed port.

**Q4 — Tauri/Python packaging (source-grounded).** Tauri **2.6.2** with `externalBin` already ships two
native sidecars, so adding a **PyInstaller standalone `binaries/intelligence-engine`** is the
**proven-by-example** path and does **not** assume Python on the user's machine.
- (1) *system Python* — smallest artifact, but **rejected**: assumes Python present + correct version.
- (2) *bundled venv* — works, large, fragile path handling.
- (3) *PyInstaller standalone* — **recommended**: one signed binary per OS, no host Python; larger
  binary (tens of MB) and a cold-start cost (our stub startup ≈ engine import, well under a second).
- (4) *Tauri sidecar (externalBin)* — the **delivery mechanism** for (3); Meetily already uses it, with
  macOS hardened-runtime/entitlements and a Windows sign command in `tauri.conf.json`. Update/version
  coordination rides the existing updater; subprocess env can be minimized (no secrets needed, local-only).

**Q5 — full real-engine trajectory (strongest criterion — PASSED).** Feeding the Meetily-shaped
synthetic manual-work stream through *normalizer → stdio IPC → `TranscriptEvent` → the committed
`MeetingEngine`* reproduced the golden trajectory **exactly**, on both stdio and HTTP:
| Event | ASK NOW | active frequency |
|---|---|---|
| "Every morning …" (final, after 3 partials) | **business_impact** | every morning |
| "How often is that process required?" | **business_impact** | every morning (unmutated) |
| "It delays our morning shipping report …" | **volume** | every morning |
| "And it runs every business day." | **volume** | **every business day** |

Final `frequency` history: `every morning` = **superseded**, `every business day` = **active** — i.e.
the same-slot evidence refinement and promotion behaved identically to the golden fixture, with **no
change to `PainPoint`/`MeetingFact`/`InformationGap`/reducer/templates/selector.** The
`TranscriptSource → TranscriptEvent → MeetingEngine` boundary survives Meetily integration.

---

## Historical Architecture Decision Status

The following status record describes the pre-real-ingress spike. The final production status below
supersedes its provisional and blocked conclusions.

**PROVEN** (only what the synthetic-payload bridge spike actually exercised):
- **`TranscriptSource → TranscriptEvent` boundary survives synthetic payloads matching the pinned
  Meetily `TranscriptUpdate` shape** — the normalizer produced valid `TranscriptEvent`s from
  Meetily-shaped input at commit `0281737…`.
- **The existing `MeetingEngine` preserves its golden semantics after normalized final events cross
  both spike transports** (stdio JSONL and localhost HTTP) — the committed engine reproduced the
  golden manual-work trajectory exactly, unchanged.

**PROVISIONAL** (design intent, *not* yet validated against real Meetily audio/transcription):
- **Meetily as Capture Host** — strong source read, but no real build/run performed here.
- **Python engine remaining independent** — worked in the spike harness; production packaging/lifecycle unproven.
- **Existing `TranscriptEvent` contract surviving** — held for synthetic payloads; real STT partial/final
  cadence, `meeting_id`/language timing, and the `speaker` naming are unproven.
- **stdio JSONL IPC** — spike-validated for correctness/latency; not Meetily's native pattern (HTTP is),
  and untested against a real transcript stream. HTTP remains a viable fallback.
- **FastAPI removed from production path** — recommended; not exercised against a real host.
- **React/Vite UI demoted to eval/demo** — recommended; not exercised.
- **Meetily Ollama not reused for V1 intelligence** — based on source reading, not a runtime test.

> **PROVEN here means: synthetic payloads shaped like Meetily's `TranscriptUpdate` were normalized and
> fed to the committed engine across both spike transports. It does NOT mean actual Meetily
> audio/transcription integration has been tested.** Real-ingress status is recorded below.

---

## Historical Real Meetily Transcript Ingress Spike (superseded)

**Honest outcome: NOT EXECUTED — blocked. No real Meetily audio/transcription run was performed, and no
real transcript data was captured. Nothing below is fabricated; the real-event tables are intentionally
empty.**

1. **Exact Meetily commit:** `0281737d87d26352fb0adc78c8c0975f691b23d1` (pinned; unchanged).
2. **Temporary Meetily file modified (disposable scratch clone only, NOT built/run):**
   `frontend/src-tauri/src/audio/transcription/worker.rs` — a diagnostic tap inserted immediately after
   the `TranscriptUpdate` is constructed and before `app_clone.emit("transcript-update", &update)`
   (between l.220 and l.222): `if let Ok(j) = serde_json::to_string(&update) { eprintln!("SPIKE_TRANSCRIPT_UPDATE {}", j); }`.
   This tees the **real** emitted payload to stderr as JSONL without altering transcript behavior. It was
   **not compiled or run** (see blocker). This documents the exact, minimal tap point for a future spike.
3–14. **Transcription provider/model, audio source, real event table, partial/final behavior, normalizer
   result on real data, duplicate/revision observations, Whisper result, Parakeet result, real
   normalized `TranscriptEvent` table, real engine trajectory, STT-wording analyzer misses, real latency
   breakdown:** **NOT MEASURED — blocked.** (Real-event and real-trajectory tables deliberately left empty.)

**Exact blocker (evidence-based).** A real ingress run requires building **and interactively operating**
the pinned Meetily Tauri desktop app with live capture + local STT. In this session:
- **Rust toolchain too old:** installed `cargo/rustc 1.74.0` (2023-10) is **below Tauri 2.6.2's required
  Rust version**; building the pinned `Cargo.lock` would require a Rust upgrade — an explicit large,
  unrelated detour (which the task forbids).
- **Non-interactive automation session:** the spike needs a human-driven GUI flow (start recording,
  select the audio source, play the synthetic script through a captured device such as the present
  `VB-Audio Virtual Cable`, stop) and live observation. This CLI agent cannot operate a desktop GUI or
  set up TTS→virtual-cable routing autonomously.
- **Heavy first-run cost:** full Tauri build + native STT toolchains + Whisper/Parakeet model downloads
  (~100 MB–1.5 GB) — tens of minutes, disproportionate to a spike.
- Audio hardware *is* present (Realtek, USB, **VB-Audio Virtual Cable**, Yeti X), so the blocker is the
  build/toolchain + interactive-GUI requirement, **not** missing audio devices. A developer at an
  interactive desktop with an upgraded Rust could run this tap in well under a day.

**New source finding (from placing the tap).** At `worker.rs:211` the emitted `source` is **hardcoded
`"Audio".to_string()`** — so at this emission boundary the "Microphone vs Audio" distinction is **not
reliably populated**. This further weakens any `source → speaker` mapping.

15. **Speaker-field recommendation.** `speaker = "You" | "Remote"` is **not** a safe temporary contract:
    Meetily provides no diarization, and `source` is hardcoded at the tap point. The field name `speaker`
    is **misleading**. Recommend: keep the contract unchanged for now (our analyzer ignores speaker), but
    **plan a future migration** renaming `speaker` → a neutral `source_label`/`channel` (reserving
    `speaker` for genuine diarization if it ever exists). Until then, populate it as `"unknown"` rather
    than implying a human speaker. **Do not change the contract in this spike.**
16. **Revised IPC recommendation:** unchanged and **still PROVISIONAL** — stdio JSONL preferred on
    surface/latency (synthetic spike), localhost HTTP a viable fallback matching Meetily's existing
    pattern; neither validated against a real transcript stream. The Windows child-death caveat
    (detect via `poll()`, not write-error) stands and must be implemented before a real trial.
17. **Revised contract recommendation:** no change now; the only proven-necessary future change is the
    `speaker` rename above. Real STT partial/final cadence and `meeting_id`/language timing remain
    unvalidated and could still surface adapter (not contract) needs.
18. **Revised architecture decision statuses (conservative).** The real-ingress spike **did not exercise**
    any boundary, so it promotes **nothing** to PROVEN. Specifically:
    - *Actual Meetily audio/transcription ingress* → **NOT TESTED (blocked)**.
    - *Deterministic analyzer robustness to real STT wording* → **UNKNOWN / at risk** — the golden text is
      clean; real Whisper/Parakeet output may punctuate/segment differently and miss keyword rules. This
      is the single highest-value open risk and must be the first thing a future real spike measures.
    - All other statuses remain exactly as in the section above (two synthetic items PROVEN; everything
      else PROVISIONAL). Nothing here is downgraded further, and nothing is upgraded.

---

## Real Meetily Production Integration — FINAL STATUS (supersedes the sections above)

**Everything marked "NOT EXECUTED — blocked" above has since been executed for real.** The Rust
toolchain blocker was resolved (LLVM 17 for bindgen, VS2022 BuildTools v143 for the ONNX/whisper.cpp
link step, `cargo +stable` pinned per-invocation without touching the global default), and a
human-in-the-loop + CDP (Chrome DevTools Protocol) automation harness was used to drive the real,
pinned Meetily desktop app end to end. This section is the authoritative, current status; where it
conflicts with anything above, this section wins.

### IPC boundary: HTTP POST + WebSocket — implemented choice

Section 14/16 above provisionally preferred **stdio JSONL** over HTTP. That recommendation is
**superseded**. The current API v8 candidate path is:

```
Meetily worker.rs (real TranscriptUpdate emission point)
  -> bounded ordered async dispatcher, reqwest::Client
  -> POST http://127.0.0.1:8000/ingest/live/{session_id}
     body: {"adapter": "meetily", "lang": "ja"|"en", "payload": <TranscriptUpdate>}
  -> acknowledgement + bounded batch replay/gap repair
  -> apps/api MeetilyLiveNormalizer -> MeetingEngine -> LiveMeetingRegistry -> SQLite/WAL recovery
  -> compact WebSocket ws://127.0.0.1:8000/ws/meeting/{session_id}
  -> Meetily's own React IntelligencePanel (ported into frontend/src/components/IntelligencePanel/)
```

`session_id` is an opaque `meeting-intel-<UUID>` generated once at recording start. The `meeting_id`
field names retained in the domain/state contract carry this session id for compatibility. The
human-readable meeting title is display metadata only and is never used as the registry, POST, or
WebSocket identity.

Why HTTP won over stdio JSONL in practice: it required zero new process-lifecycle management (no
child process to spawn/monitor/restart from Meetily's side), reused a dependency Meetily already had
(`reqwest`), matched Meetily's own existing sidecar convention (already noted in §3/§14 as
`localhost:5167`), and let the same backend serve multiple concurrent consumers (the ingestion route
and the WebSocket panel) without a custom multiplexing protocol. The "no LAN/port-conflict surface"
concern behind the stdio recommendation was real but adequately mitigated: the backend binds
`127.0.0.1` only, and Meetily's CSP (`connect-src`) was extended to allow only that loopback origin.

### Real blockers hit and fixed during the real run (all real bugs, not hypothetical)

1. **Whisper `is_partial` is a duration heuristic, not a streaming signal.** `whisper_engine.rs` sets
   `is_partial = duration_seconds < 15.0`. Real conversational VAD segments are almost always under
   15s, so nearly every Whisper-sourced update is marked partial even though each `sequence_id` is
   only ever emitted once (confirmed: no revision was ever observed for the same `sequence_id` across
   dozens of real captured segments, English and Japanese). Meetily's own frontend
   (`TranscriptContext.tsx`) does not gate display on `is_partial` either — it dedupes purely by
   `sequence_id`. **`MeetilyLiveNormalizer` therefore dedupes by `sequence_id` and does not reject
   `is_partial=true` content.** Parakeet's `is_partial` is genuinely `false` for real finals, so this
   design is correct for both engines.
2. **English real ingress used Parakeet, not Whisper.** Meetily's default transcript provider is
   `parakeet` unless `api_save_transcript_config(provider="localWhisper", ...)` is explicitly called.
   An earlier informal claim that the English spike proved "Whisper ingress" was incorrect and is
   retracted; the real engine used was Parakeet (`parakeet-tdt-0.6b-v3-int8`). Both engines are now
   explicitly, deliberately exercised: **English via Parakeet, Japanese via Whisper `small`.**
3. **`whisper_load_model` while a recording session is active deadlocks** both that call and the
   subsequent `stop_recording` (both need the same whisper-engine mutex; the load never released it).
   Recovery required killing and relaunching the Meetily process. **Documented as an observed upstream
   Meetily integration hazard; model/provider/language configuration must happen before recording
   starts, never during.**
4. **CORS, not CSP, blocked the browser->backend connection initially.** Meetily's webview origin
   (`http://localhost:3118` in dev) differs from the backend's `127.0.0.1:8000`; a plain `fetch()`
   failed with a generic "Failed to fetch" (the standard opaque browser behavior for a CORS rejection).
   Fixed by adding `CORSMiddleware` to the FastAPI app with a small fixed allow-list (not `*`).
   Separately, Meetily's own CSP `connect-src` also needed the loopback origin added (a real, additional
   requirement, confirmed necessary once CORS was fixed) — both were required, not just one.
5. **`uvicorn` alone bundles no WebSocket implementation.** Every WS route silently 404'd until
   `websockets` was added as an explicit dependency (`apps/api/pyproject.toml`).
6. **A stale-closure React bug dropped all WebSocket broadcasts.** `useIntelligencePanel`'s default
   `socketFactory` parameter was re-created on every call (JS default parameters are not referentially
   stable) and was listed in the `useEffect` dependency array, so the socket was torn down and recreated
   on every re-render — silently missing every broadcast between reconnects. Fixed by hoisting the
   default to a module-level constant. Verified via a plain external Python WebSocket client (bypassing
   React entirely) that the server-side broadcast was correct throughout; the bug was exclusively
   client-side.
7. **The frontend's pre-existing `meetingTitle` React state can diverge from Meetily's actual
   `RECORDING_MANAGER` meeting name** (it is only set correctly when the normal "Start Recording" UI
   button flow runs). The intelligence panel instead reads the meeting id via the existing
   `recordingService.getRecordingMeetingName()` Tauri command, which reflects the same Rust-side state
   the production HTTP push uses — eliminating this class of mismatch by construction.

### Real English ingress: PASS

- Engine: **Parakeet** (`parakeet-tdt-0.6b-v3-int8`), explicitly configured via
  `api_save_transcript_config`.
- WAV: the existing `synthetic_manual_work.wav` (SAPI "David Desktop", 4 sentences), played once
  through VB-Audio Virtual Cable into Meetily's selected microphone (`CABLE Output (VB-Audio Virtual
  Cable) (input)`).
- Real STT text (unaltered): "Every morning one person downloads the order file and copies the rows
  into another spreadsheet." / "How often is that process required?" / "It delays our morning shipping
  report by about an hour." / "And it runs every business day."
- Confirmed via server logs: real `POST /ingest/meetily/ui-e2e-english-v4` (4x, 200 OK) from Meetily's
  own Rust process, and a real `WebSocket /ws/meeting/ui-e2e-english-v4?lang=en [accepted]`.
- Rendered IntelligencePanel (read live from the DOM via CDP), final state: **ASK NOW** = "How much
  data is processed each time?" (volume, correctly promoted after business_impact and frequency were
  both answered); FOLLOW UP = "How much time does this take each time?" / "Who performs this work?";
  KNOWN = business_impact "delays morning shipping report by ~1h" (stated), frequency "every business
  day" (stated, correctly showing only the active value after "every morning" was superseded); MISSING
  = Volume, Time cost, Owner, Reason it is manual, Success condition.

### Real Japanese ingress: PASS

- Engine: **Whisper `small`** (explicitly loaded before recording started, to avoid the deadlock in
  finding #3 above), language explicitly configured to `"ja"` via `set_language_preference` (confirmed
  via whisper.cpp's own log line `prompt[1] = [_LANG_ja]` — the real inference call, not a post-hoc
  tag).
- Audio source: **VOICEVOX** (neural, fully local/offline TTS engine, speaker "Shikoku Metan/Normal"),
  chosen after three real SAPI-voice attempts (Haruka Desktop, Ichiro) were conclusively shown to be
  rejected by Meetily's Silero VAD (each attempt recognized only a single ~1.3s isolated-numeral
  fragment out of ~9-25s of real continuous speech, at identical confidence 0.12 each time, regardless
  of voice/gender/pause-timing) — a real acoustic-classifier mismatch with classic SAPI synthesis, not
  a Japanese-support defect. VOICEVOX produced a full 6.55s continuous VAD segment at confidence 0.99
  on first try.
- Real STT text (unaltered, from the frozen six-beat inventory-mismatch script): "EC側と倉庫側で**最高**の数が合わない状態がほぼ毎朝発生しています" / "今は担当者が毎朝、両方のデータを見比べて手動で修正しています。" / "**最古**数について" + "としているのはEC側と倉庫側のどちらですか" (segmented question) / "倉庫システム側を**制**としています" / "この確認作業のせいで、朝の出荷作業が**大体**30分ほど遅れています。" / "この作業は営業日には毎日発生していて、対応しているのは宮田さん一人だけです。"
- Real STT-induced kanji homophone confusions found and fixed in `apps/api/app/services/analyzer.py`
  (`_ja_data_mismatch`), each with the exact observed text, a paraphrase regression, and (where
  applicable) a negative guard — see `apps/api/tests/test_slice2.py`:
  - "在庫" (zaiko/inventory) mis-heard as same-reading "最高"/"最古" (different kanji each time) →
    pain detection broadened to accept the literal word OR a structural two-"側"-mentions +
    mismatch-verb pattern.
  - "正" (sei/authoritative) mis-heard as same-reading "制" → `source_of_truth` extraction broadened to
    accept `"正として"` OR `"制として"` (kept scoped to that exact construction).
  - `business_impact` was previously **not wired at all** for this template (a real, silent,
    launch-blocking gap found only because real STT exercised it) — added, anchored on "遅れ" (delay),
    which is stable across the "だいたい"/"大体" kanji/kana variation also observed in this run.
- Confirmed via server logs: real `POST /ingest/meetily/ui-e2e-japanese-v1` (7x, 200 OK) and a real
  `WebSocket ... [accepted]`.
- Rendered IntelligencePanel (read live from the DOM via CDP, in Japanese chrome, no mojibake), final
  state: **今すぐ聞く (ASK NOW)** = "この差異の対応は、どなたが担当されていますか？" (owner, correctly
  promoted after source_of_truth and business_impact were both answered); 理由 (why) = "対応の責任者が
  不明です。"; 補足質問 (FOLLOW UP) = "この差異は、どの商品や倉庫の範囲で発生していますか？"; pain title
  = "EC・倉庫間の在庫差異"; わかっていること (KNOWN) = 修正方法 "手動修正", 在庫の基準 "倉庫側" (extracted
  despite the 正→制 confusion), 業務影響 "出荷作業の遅延" (the newly-wired rule), 発生頻度 "毎日"
  (correctly showing only the active value after "ほぼ毎朝" was superseded); わかっていないこと
  (MISSING) = 担当者 (owner), 影響範囲 (affected_scope).

### Known, accepted, honestly-documented limitations

- Japanese owner extraction supports explicit person/team ownership clauses for all templates with an
  `owner` slot. Questions, vague future actions, and unresolved-owner statements are intentionally
  rejected; implicit or highly elliptical ownership language remains a limitation.
- `LiveMeetingRegistry` keeps active state in memory and transactionally persists an append-only
  transcript log plus compact snapshot in a bounded SQLite/WAL recovery cache. Backend restart
  restores a valid active session; Meetily's ordered transcript history remains the gap-repair
  source if a cache write failed. Normal stop purges the session and crash remnants expire after
  24 hours.
- The legacy meeting-title session-reuse collision is eliminated for current opaque per-recording
  session ids; older title-keyed clients do not receive that guarantee.
- Real end-to-end testing used synthetic TTS audio (SAPI, then VOICEVOX) routed via VB-Audio Virtual
  Cable, not a live human speaker — the closest practical fully-automated substitute, but not identical
  to real human prosody/disfluency.
- Meetily's own PostHog telemetry and cloud LLM provider options (§3) are unrelated to and untouched by
  this integration; the Meeting Intelligence path added here is 100% loopback-local
  (`127.0.0.1:8000`), calls no cloud/remote service, and the CORS allow-list is a small fixed set of
  known local origins, never `*`.

### Independent review (Codex) — findings and fixes

An independent, read-only review of the above implementation found and confirmed 4 real bugs (2 HIGH,
2 MEDIUM+HIGH mixed) that were fixed before this was considered done, plus 3 MEDIUM findings that were
fixed, and one MEDIUM finding partially accepted (documented, not fully redesigned):

- **HIGH, accepted & fixed:** `seq`/`event_id` were derived from arrival order, not Meetily's real
  `sequence_id`; since the Rust side fires each push in its own `tokio::spawn`, requests could reach the
  backend out of order and invert transcript chronology. Fixed: `MeetilyLiveNormalizer` now derives
  `seq` directly from `sequence_id`, and `LiveMeetingRegistry` buffers early-arriving events until every
  earlier `sequence_id` has been applied, draining contiguous runs in true order.
- **HIGH, accepted & fixed:** `/ingest/meetily/{id}` was a plain `def` route, which FastAPI runs on a
  worker thread -- but `LiveMeetingRegistry` holds shared, unsynchronized state and
  `asyncio.Queue.put_nowait()` is not thread-safe. Fixed: the route is now `async def`, keeping all
  registry mutation on the single event loop thread.
- **HIGH, accepted & fixed:** the meeting title was previously used as both the URL path segment and
  the long-lived registry key. The integration now generates one opaque `meeting-intel-<UUID>` id per
  recording, threads it through the Rust POST and frontend WebSocket subscription, and uses it as the
  backend registry key. URL encoding remains defense in depth. Two recordings with the same title are
  now independent, and slashes/Japanese/punctuation/emoji in the title cannot affect live identity.
- **MEDIUM, accepted & fixed:** a WebSocket subscribing before any real transcript push could create the
  meeting with its default `?lang=ja` guess, which could then silently win against the real language if
  the actual push arrived after. Fixed: `LiveMeetingRegistry.subscribe()` no longer creates a meeting or
  takes a `lang` at all; only a real `ingest()` call is authoritative, and pending subscribers are
  attached once the meeting is created for real.
- **MEDIUM, accepted & fixed:** `business_impact` fired on a standalone `"時間がかかる"` (takes time)
  match with no real evidence behind it (only `"遅れ"` was actually observed in real STT), fabricating a
  specific shipping-delay value for generic wording. Fixed: removed the standalone trigger; added a
  negative regression test.
- **MEDIUM, accepted & fixed:** the frontend's WebSocket hook kept comparing against pre-restart state
  after a backend restart (which resets versions to 0/1/2...), permanently freezing the panel. Fixed:
  reset to a clean state on every successful (re)connection, clear stale ASK NOW UI while recovering,
  and replay the active recording's ordered `get_transcript_history` through the normal ingest route.
- **MEDIUM, accepted & fixed:** the disposable diagnostic tap always ran (falling back to a `%TEMP%`
  path), adding blocking sync file I/O to the transcription hot path unconditionally. Fixed: strictly
  opt-in now -- only runs when `SPIKE_TAP_PATH` is explicitly set, no fallback path.
- **MEDIUM, accepted and fixed across the local-only/distribution follow-ups:** the original Rust URL
  check used broad string prefixes and the WebSocket endpoint had no Origin/auth check (CORS does not
  cover WS upgrades). The clean worktrees now use strict URL parsing for `MEETING_COPILOT_URL`, apply
  the same loopback-only policy in the IntelligencePanel hook, reject non-allowlisted browser
  WebSocket Origins, and require a per-launch capability token for sensitive HTTP and WebSocket
  routes in managed/packaged mode. The token remains in process memory, stays out of URLs/UI/logs,
  and is covered by real packaged transport smoke. Transcript-content logging was also removed from
  the Rust transcription path. See
  `docs/LOCAL_ONLY_PRIVACY.md` for the exact boundary and the remaining local-process limitations.

All fixes were verified with updated/new unit tests (`apps/api/tests/test_meetily_live_adapter.py`,
`apps/api/tests/test_slice2.py`), a full `scripts/check.ps1` rerun (exit 0), and a live confirmation
re-run of the real English/Parakeet ingress producing the identical, correct MeetingState trajectory as
before the fix pass -- proving the ordering/thread-safety/URL-encoding changes introduced no regression.

## Backend restart recovery hardening — 2026-07-13

The recovery implementation is isolated in the clean Meetily worktree
`<legacy-meetily-worktree>-recovery` at commit `6045188e89ccc6bce61769b136f36b8071c914f8` on
`spike/backend-recovery`; the original damaged `<legacy-meetily-worktree>` tree was not modified. The existing
Tauri `get_transcript_history` command was inspected and confirmed to retain the complete active
recording history with stable `sequence_id` values, text, confidence, and recording-relative timing.

On a WebSocket reconnect, the Meetily panel clears its old snapshot and displays localized reset or
reconnecting text. It then sorts that history by `sequence_id` and re-posts it to the same opaque
session id using the existing `/ingest/meetily/{session_id}` contract. The backend registry's existing
ordering and sequence deduplication rebuild state deterministically. A successful replay shows a
localized recovered indicator; an empty or failed replay never renders stale ASK NOW and new live
events can rebuild the session.

Verification for this hardening included 13 focused adapter tests, app-source TypeScript checking,
and `cargo check --locked` with `RUSTUP_TOOLCHAIN=stable`, `LIBCLANG_PATH=<path-to-LLVM-bin>`, and
`CMAKE_GENERATOR=Visual Studio 17 2022`. The full frontend typecheck still reports only the known
unrelated `tests/lib/blocknote-markdown.test.ts` import of `bun:test`.

## Japanese owner extraction — 2026-07-13

The product analyzer now uses one small grammar-shaped helper for every template that declares an
`owner` slot. It recognizes explicit person/team declarations such as `対応は田中さんが担当して
います`, `在庫差異の確認は倉庫チームが見ています`, and embedded-term phrasing such as
`API連携の一次対応はEC運用チームで対応しています`. The resulting fact is `kind=evidence` and
points to the current transcript event in `evidence_event_ids`.

For split role statements, V1 keeps one deterministic owner value:
`一次対応: 山田さん / 最終確認: 情シス`. Questions (`どなたが担当されていますか？`,
`担当は田中さんでしょうか？`), vague future actions (`倉庫チームに確認します`), and unresolved
ownership (`担当者はまだ決まっていません`) do not create facts. Once an explicit owner answer
arrives, the owner gap is answered, its ASK NOW suggestion disappears, and the selector promotes the
next template-specific open gap.

## Packaging and startup audit — 2026-07-13

The product now has a Windows-first `scripts/start-local-v1.ps1` launcher. It starts or reuses the
FastAPI backend on `127.0.0.1`, checks `/health`, keeps operational logs under the user's temp
directory, and can optionally launch a clean Meetily worktree without persisting Rust/LLVM/CMake
environment variables. The launcher is backend-first and does not make the Meetily desktop app a
requirement for API/golden verification.

The clean packaging worktree `<legacy-meetily-worktree>-packaging` was created at `5534dc8` on
`spike/packaging-startup`. Its Tauri configuration declares `binaries/llama-helper` and
`binaries/ffmpeg` as `externalBin` resources. With the pinned Windows environment,
`cargo check --locked` downloaded and verified the FFmpeg payload, then failed in Tauri's custom
build validation because `binaries\\llama-helper-x86_64-pc-windows-msvc.exe` is absent. This is a
real packaging blocker for `cargo check`, `tauri dev`, and `tauri build`, but it is separate from the
V1 ASK NOW transport and reducer path, which does not use Meetily's summary `llama-helper`.

No Meetily source or configuration was changed for this audit. The staged delivery plan is manual
startup plus the launcher now, a future standalone Python/PyInstaller intelligence-engine artifact,
and only then a real Tauri `externalBin` package. A Rust rewrite remains rejected because it would
discard the tested Python engine and golden evaluation harness without solving the sidecar payload.
See `docs/PACKAGING_PLAN.md` for the exact commands, blocker classification, version coordination,
privacy posture, and rollback notes.

## Deterministic directional integration details — 2026-07-13

The Japanese integration-failure analyzer now covers the explicit grammar-shaped statement
`source の payload を target に operation ... で failure`. The frozen case
`EC側のCSVをSAPに取り込むところで止まります。` produces evidence-backed
`source_system = EC`, `target_system = SAP`, and `failure_point = CSV取り込み`, detects the
integration-failure pain, and leaves business impact as ASK NOW. After impact and owner answers,
the deterministic selector promotes owner and then retry behavior without re-asking the filled
system/failure slots.

The boundary deliberately does not infer direction from loose mentions such as
`SAPとSalesforceの連携`, does not fabricate all three facts from a failure-point-only statement,
and skips fact extraction for questions. A Japanese-system paraphrase using
`受注システム → 注文データ → 倉庫システム → 送信途中で失敗` is covered by the same grammar.
Verification adds a three-checkpoint golden fixture, raises the deterministic suite to 9/9, and
keeps every new fact tied to the exact transcript event id.
