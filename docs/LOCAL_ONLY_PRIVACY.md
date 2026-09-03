# Local-only privacy guarantees

> Optional connected-context providers are a deliberate exception to the no-remote-I/O runtime:
> when a user configures SharePoint, OneDrive, or an ERP endpoint, the local sidecar makes direct,
> read-only HTTPS requests to that source. No cloud AI or telemetry receives the data, and disabling
> all remote connectors restores the loopback-only behavior described below. See
> `docs/CONNECTED_CONTEXT.md`.

This document describes the normal V1 Meeting Intelligence path and the boundary between that
path and unrelated Meetily features. It is a local-first question copilot, not a cloud meeting
summarizer.

## Normal intelligence path

```text
Meetily local STT (Parakeet or Whisper) capturing audio from this machine
  -> Rust TranscriptUpdate
  -> capability-authenticated HTTP POST to validated loopback FastAPI URL
  -> local deterministic MeetingEngine
  -> capability-authenticated WebSocket snapshot from the loopback backend
  -> Meetily IntelligencePanel
```

The path carries transcript text, facts, gaps, and questions only between processes on the same
machine. No cloud AI, Ollama, RAG, or remote service is required. The product backend does not
contain a cloud SDK or a telemetry client; its analyzer is deterministic and its registry is
local-first. The internal candidate is designed to follow meeting audio captured locally by
Meetily without direct platform adapters or cloud APIs. Until the 18-session platform matrix is
recorded as passing, product copy must say **locally captured meeting audio** rather than claiming
verified Zoom, Google Meet, or Teams support.

An experimental Ollama semantic-candidate adapter now exists outside the normal runtime composition.
It is selected only when code explicitly invokes the selector with
`MEETING_INTELLIGENCE_SEMANTIC_ANALYZER=ollama`; the current FastAPI/MeetingEngine path does not.
The adapter accepts only an HTTP loopback base URL, sends a bounded transcript window only to that
local endpoint, never logs transcript content, and fails closed to no candidates. Tests inject an
in-memory transport and do not contact Ollama.

The semantic adapter remains shadow-only. It is not composed into `MeetingEngine`, cannot mutate
`MeetingState`, cannot create ASK NOW content, and cannot affect deterministic ranking. Explicit
pilot/eval invocations may retain aggregate candidate-validation counters only; prompts,
transcript/evidence text, and model output are not operational diagnostics.

## Retention

- Saved Meetily meetings remain local until the user deletes them by default.
- User-selectable saved-meeting retention is 7, 30, or 90 days, or forever.
- Active intelligence recovery rows are separate transient data. A normal stop purges the session;
  crash remnants expire after 24 hours. Per-session cleanup returns `reset: false` if the cache
  deletion could not complete, while still tombstoning the ended session in memory.
- The authenticated `DELETE /meetings` operation purges every transient live/recovery row for
  Meetily's explicit delete-all workflow; a false reset result must be shown as incomplete deletion.
- The live recovery SQLite database uses WAL transactions and a 64 MiB event-log ceiling per active
  session. If storage is unavailable, intelligence continues in memory and recording/STT remains
  independent.
- Meetily exposes aggregate-only diagnostics export, separate resolved locations for application
  data, pilot/recovery data, and recordings, plus per-meeting and delete-all actions. Per-meeting,
  retention, and delete-all flows remove an ownership-marked recording folder before deleting its
  database row; unsafe or inaccessible folders remain listed for a safe retry. Device
  encryption such as BitLocker is recommended because application-level user scoping does not
  protect an unlocked Windows account or an unencrypted disk removed from the device.
- Meetily's explicit per-meeting JSON export contains user-facing meeting content (title,
  timestamps, transcript, saved summary, and prepared issue draft) but omits database, transcript,
  live-session, and evidence identifiers, capability tokens, and local filesystem paths. Exports
  written to a user-chosen location are outside Meetily retention and delete-all controls.

## Enforced local boundaries

Product:

- Start V1 with `.venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000`.
  Binding to `127.0.0.1` is required; do not expose the API on `0.0.0.0` or a LAN address.
- FastAPI CORS allows only the documented Meetily local webview origins: `localhost:3118`,
  `127.0.0.1:3118`, `tauri://localhost`, and `http://tauri.localhost`.
- WebSocket upgrades apply the same origin allow-list. A missing Origin is permitted for native
  local clients; a non-allowlisted browser Origin is closed with policy code `1008`.
- When `MEETING_INTELLIGENCE_TOKEN` is configured, `/health/compatibility`, transcript ingest,
  per-session/all-session cleanup, and WebSocket subscription fail closed without the matching
  token. `/health` remains public and returns only `{"status":"ok"}`.
- HTTP clients use `X-Meeting-Intelligence-Token`. Browser WebSockets carry the token as a second
  `Sec-WebSocket-Protocol` value beside `meeting-intelligence-v1`, keeping it out of URLs and
  ordinary access logs. Comparison is constant-time and rejected token values are never logged.
- The product has no outbound transcript/fact/question transport in the normal runtime. It only
  serves local HTTP and WebSocket requests. The uncomposed experimental Ollama adapter is the only
  optional outbound product path and rejects every non-loopback destination.

Clean Meetily distribution worktree:

- The integration supports both manual loopback-backend mode and managed-sidecar mode. In both
  cases the transcript path stays loopback-only; managed mode adds a per-launch in-memory
  capability token and compatibility handshake before traffic is treated as ready.
- The Rust push accepts `MEETING_COPILOT_URL` only when it parses as HTTP with a loopback host
  (`localhost`, `127.0.0.1`, or `::1`), no credentials, no query/fragment, and no non-root path.
  Invalid or empty values fall back to `http://127.0.0.1:8000`.
- The `useIntelligencePanel` hook applies the same policy to `window.__MEETING_COPILOT_URL__`
  before constructing either its `fetch` URL or WebSocket URL. An external value cannot redirect
  the panel's transcript replay or live subscription.
- The diagnostic `SPIKE_TAP_PATH` is opt-in only. When unset, no diagnostic JSONL transcript tap is
  written and there is no fallback temporary-file path.
- Transcription logs no longer include recognized text; they retain only operational metadata such
  as character count, confidence, timing, and sequence information.
- Managed-sidecar mode generates a 256-bit token, holds it only in process memory, passes it to the
  child backend through its environment, and clears the owned value on sidecar shutdown. Rust HTTP,
  frontend replay, and frontend WebSocket calls all use that same token; it is neither rendered nor
  persisted by the panel.

## Meetily features outside this path

Meetily is a larger desktop application and contains separate capabilities that are not part of
the copilot path:

- Summary generation supports local built-in/llama-helper and Ollama, plus optional OpenAI,
  Anthropic, Groq, OpenRouter, and custom OpenAI-compatible providers.
- Transcript settings contain optional remote transcription providers. V1 acceptance uses local
  Parakeet for English and local Whisper small for Japanese; selecting a remote provider is a
  separate user choice and can send audio/transcript data externally.
- The meeting-details flow may auto-generate a summary after recording when the user's
  `isAutoSummary` setting is enabled and a summary model/provider is configured. The IntelligencePanel
  does not call this path. To keep the entire Meetily session offline, disable Auto Summary and use
  local transcription/summary providers.
- Model downloads, updater traffic, GitHub links, and other application network calls are separate
  from the copilot transport and are not removed by this integration.

These features are explicitly outside the normal intelligence path; this phase does not delete or
rewrite their provider systems.

## Telemetry status

Meetily's analytics is disabled by default in the current integration worktree:

- `AnalyticsProvider` initializes `analyticsOptedIn` to `false`, persists that value, and only
  initializes the analytics client after explicit opt-in.
- The Rust `AnalyticsConfig::default()` is also disabled. If the user opts in, the client sends
  anonymous usage/diagnostic events to PostHog at `https://us.i.posthog.com`.
- The analytics implementation sanitizes known sensitive property keys and does not receive the
  transcript text, fact values, or copilot questions. It can still receive usage metadata such as
  provider/model names, counts, durations, and application/session identifiers.

For an offline run, leave Analytics disabled in the Meetily UI and keep the stored
`analyticsOptedIn` value `false`. The copilot itself does not invoke analytics.

## Persistence and logging

Our product code keeps a bounded local SQLite/WAL live-session cache with transactional append-only
transcript rows and a compact aggregate snapshot per active session. The snapshot excludes the
transcript and recovery reconstructs it from the event rows, validating session identity, version,
last sequence, and evidence references before use. Invalid rows are quarantined. That cache is
local-only, used only for backend restart recovery, and purged on explicit
`DELETE /meeting/{session_id}` cleanup. The Meetily panel can still replay its ordered
active-recording transcript history after a backend restart, but reconnecting clients no longer
depend on replay for every restart. Normal logs identify sessions and operational failures without
dumping transcript content.

The experimental Ollama adapter has no logging calls. Its prompt contains event IDs, language,
speaker ID, and transcript text because semantic extraction requires those fields, but the request
can only target a validated loopback URL. Model responses are size-bounded and are not persisted.

Meetily independently persists saved meeting metadata/transcripts in its local SQLite-backed
database and recording/audio artifacts in its local recording storage. That storage is separate
from the copilot registry and remains a Meetily feature. `SPIKE_TAP_PATH` is the only integration
specific transcript file sink, and it is disabled unless explicitly configured.

## Verification

From the product repository:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Confirm the backend command contains `--host 127.0.0.1`, then verify
`http://127.0.0.1:8000/health`. Product tests cover the CORS/origin allow-list and reject a remote
WebSocket Origin.

From the clean Meetily distribution worktree:

```powershell
cd $env:MEETILY_ROOT\frontend\src-tauri
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
cargo check --locked

cd $env:MEETILY_ROOT\frontend
pnpm test
pnpm exec tsc --noEmit
```

For the authenticated two-process development flow, leave `MEETING_COPILOT_URL` and
`SPIKE_TAP_PATH` unset and run:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 `
  -MeetilyPath $env:MEETILY_ROOT -LaunchMeetily
```

The launcher generates the token without displaying or persisting it. For an explicit backend-only
development session with authentication disabled, use `-AllowUnauthenticatedDev`; never use that
switch for packaged acceptance. The actual external-network behavior of unrelated Meetily providers
must be reviewed separately if those features are enabled.

## Remaining limitations

- The capability token blocks casual unauthenticated loopback clients but is not an operating-system
  sandbox. A malicious process running as the same user may be able to inspect another process's
  environment or memory; OS account isolation and endpoint security remain relevant.
- Invalid copilot URL configuration falls back to the safe local default rather than failing the
  recording; this protects data flow but can hide a configuration typo.
- Meetily's independent telemetry, remote transcription, summary providers, model downloads,
  updater, and local persistence remain in the application and are controlled separately.
- The backend recovery cache is bounded and session-scoped. It is meant to survive backend restarts
  during an active recording, not to become a long-term transcript store after session cleanup.
- The clean Meetily tests, typecheck, lint, Next build, Rust tests, and unsigned installer build are
  release gates, not documentation claims. They remain unproven until their current command output
  and artifact hashes are recorded; signed packaging and clean-machine install behavior are a later
  milestone.
- The Ollama candidate adapter is not composed into the V1 meeting runtime and has not been evaluated
  against a live local model. Safe-off and mocked fail-closed behavior are tested; semantic quality is
  still unknown.
