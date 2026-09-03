# Meetily-integrated Meeting Intelligence Copilot V1 Runbook

> **Frozen historical runbook:** commands and evidence here describe the earlier V1 milestone.
> They do not establish current API v8 or installer acceptance. Use `docs/INTERNAL_PILOT.md` and
> `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for current gates.
>
This runbook freezes the reproducible local V1 milestone and backend-restart recovery behavior for
the bilingual Meeting Intelligence Copilot. The product is a question copilot: the live loop is
transcript update → normalized event → pain point → facts → information gaps → one ASK NOW question,
not meeting summarization.

## Frozen milestone

Product repository:

- Path: `$env:ASSISTANT_ROOT`
- Integration milestone commit: `1810f99 feat: integrate live meeting intelligence with Meetily`
- Current verified product baseline before deterministic transfer-detail coverage:
  `7e5a6b1 docs: record IntelligencePanel UX hardening`

Meetily integration copy:

- Path: `<legacy-meetily-worktree>`
- Commit: `09cf3b7f900d7365202bf7ed1e5ec2297f5adf25`
- Branch: `spike/real-transcript-ingress`
- Pinned upstream parent: `0281737d87d26352fb0adc78c8c0975f691b23d1`
- Patch backup: `<legacy-patch-backup>\meetily-integration-09cf3b7.patch`

Meetily backend-recovery worktree:

- Path: `<legacy-meetily-worktree>-recovery`
- Branch: `spike/backend-recovery`
- Commit: `6045188e89ccc6bce61769b136f36b8071c914f8`
- Parent: `87d5f15 feat: route meeting intelligence by opaque session id`
- Patch backup: `<legacy-patch-backup>\meetily-recovery-6045188.patch`

Meetily local-only worktree for this phase:

- Path: `<legacy-meetily-worktree>-local-only`
- Branch: `spike/local-only`
- Commit: `5534dc84821b1b50e0b7106e2f3a6d15ffedea1c`
- Patch backup: `<legacy-patch-backup>\meetily-local-only-5534dc8.patch`

Meetily packaging inspection worktree:

- Path: `<legacy-meetily-worktree>-packaging`
- Branch: `spike/packaging-startup`
- Base commit: `5534dc8`

Meetily IntelligencePanel UX worktree:

- Path: `<legacy-meetily-worktree>-panel-ux`
- Branch: `spike/intelligence-panel-ux`
- Commit: `ba06098ae2b9d249288aa75f61c2336cdcae2a1c`
- Parent: `5534dc8 feat: enforce local-only meeting intelligence transport`
- Patch backup: `<legacy-patch-backup>\meetily-panel-ux-ba06098.patch`

Current clean Meetily distribution worktree:

- Path: `$env:MEETILY_ROOT`
- Branch: `spike/distribution-readiness`
- Runtime-hardened baseline: `f05b2ca9238f33f957f5bb77089920f05eb387c5`
- Capability-token commit: `f9d4aa7 feat: pass local capability token to intelligence backend`
- Bounded-restart commit:
  `499cb872286d8af65806fce3e75c44ccc2987fdf feat: add bounded restart backoff for intelligence backend`
- Managed-lifecycle status commit:
  `6f90f71cd5510c695329a2a31c48e537d7e1be56 feat: manage intelligence backend sidecar lifecycle`
- Patch backups:
  `<legacy-patch-backup>\meetily-capability-token-f9d4aa7.patch` and
  `<legacy-patch-backup>\meetily-restart-backoff-499cb87.patch`, plus
  `<legacy-patch-backup>\meetily-managed-sidecar-6f90f71.patch`

The `<legacy-meetily-worktree>` working tree contains unrelated, pre-existing deletions from the earlier
damaged-tree recovery incident. Those deletions are not part of the integration and must not be
restored or committed. Future Meetily feature work should preferably use a clean clone or worktree
based on `$env:MEETILY_ROOT` at `6f90f71`, which descends from the latest clean panel UX
commit `ba06098` and includes the original integration, opaque session identity, recovery,
local-only hardening, and panel polish.

## Live session identity

Each recording now creates one opaque `meeting-intel-<UUID>` session id. That id is used consistently
by the Rust HTTP push, the FastAPI ingest route, the `LiveMeetingRegistry` key, and the IntelligencePanel
WebSocket subscription. The `meeting_id` names retained in the backend/domain contract are the live
session id in this integration; they do not mean the display title.

The human-readable meeting title remains Meetily display/recording metadata only. A title containing
slashes, Japanese text, spaces, punctuation, or emoji cannot change the backend identity, and two
recordings with the same title receive different session ids. The active id is cleared after recording
shutdown.

## Backend restart recovery

`LiveMeetingRegistry` now keeps a bounded local cache per active session: an append-only transcript
log plus the latest `MeetingState` snapshot. On backend restart, the next reconnecting client can
recover the active session directly from that local cache before any new transcript replay occurs.
During an active recording, the Meetily panel still handles the boundary explicitly:

1. The WebSocket reconnect path clears the old snapshot and shows a localized reset/reconnecting
   message; no stale ASK NOW question is rendered.
2. The restarted backend recovers the active session from the persisted log/snapshot cache when it
   is available, so reconnecting clients can receive a consistent state immediately.
3. If the backend state is still empty or incomplete, the panel invokes Meetily's existing
   `get_transcript_history` command, sorts the active-recording history by `sequence_id`, and
   re-posts the stable transcript payloads to the same opaque session id.
4. The backend's existing sequence ordering and deduplication rebuild the deterministic state. The
   panel shows a recovered message and resumes rendering the backend-owned state.

If history is empty or unavailable, the panel remains honest with the reset message and new live
transcript events rebuild the session. This is recovery for an active recording, not durable state
across stopping the recording or restarting the Meetily app.

The managed sidecar supervisor at `499cb87` makes crash recovery bounded and visible without
affecting recording or local STT:

- an owned backend is checked once per second and declared unhealthy after two consecutive failed
  authenticated compatibility checks;
- after the initial failure, at most three automatic restarts are attempted with non-zero
  exponential delays of 1, 2, and 4 seconds;
- 60 seconds of stable runtime resets the failure budget;
- exhaustion exposes `unavailable` through a privacy-safe Tauri status command and stops automatic
  retrying until the user selects the localized `Retry now` / `今すぐ再試行` action;
- missing executable, incompatible backend, generic startup failure, and stopped/unhealthy backend
  are distinguished by fixed reason codes with actionable JA/EN text; local paths and exception text
  stay in local developer logs and are never serialized to the webview;
- starting, ready, degraded, restarting, unavailable, and stopped status contain only phase,
  bounded retry counts, auth/managed flags, backend version, and retry delay—never token,
  transcript, URL, path, or meeting content; and
- every reconnect clears the previous snapshot, so stale ASK NOW content stays hidden while ordered
  transcript-history replay reconstructs active-recording context.

## Candidate live meeting sources

The internal candidate uses Meetily as the shared local capture host. Its supported input model is
**locally captured meeting audio**. Zoom desktop, Google Meet in a browser, and Teams desktop are
acceptance targets, not verified support claims, until all 18 synthetic platform-path sessions pass:

- Zoom desktop meetings
- Google Meet running in a browser
- Teams desktop meetings

This phase does not ship direct Zoom, Google Meet, or Teams platform adapters. The backend reserves
their adapter names behind the adapter registry seam so direct integrations can land later without
reworking the API or leaking platform logic into the pure domain.

## Backend startup modes

- Manual loopback mode: start the backend yourself on `127.0.0.1` and point Meetily at it. This is
  the simplest development path and remains supported.
- Managed sidecar mode: packaged release builds manage the backend by default. Debug builds remain
  manual by default. `MEETING_INTELLIGENCE_MANAGE_SIDECAR=1` explicitly enables management and
  `MEETING_INTELLIGENCE_MANAGE_SIDECAR=0` explicitly disables it. Meetily performs the authenticated
  API v3 compatibility handshake, monitors bounded restarts, and keeps recording/STT alive even if
  the backend never becomes ready.

## Required local tools

- Windows
- Rust stable
- MSVC / Windows SDK
- LLVM/libclang
- pnpm
- VOICEVOX
- VB-Audio Virtual Cable
- VLC
- Local Parakeet model `parakeet-tdt-0.6b-v3-int8` for English
- Whisper `small` for Japanese

The intelligence path is local-only and capability-authenticated: the product backend binds to
loopback, Meetily pushes to the loopback backend, and the Meetily panel subscribes to its loopback
WebSocket. Strict URL validation, the local WebSocket Origin allow-list, per-launch token, and the
complete boundary are documented in
`docs/LOCAL_ONLY_PRIVACY.md`. Meetily’s separate telemetry and optional cloud summary providers
are outside this path.

## IntelligencePanel behavior

The live panel is a private question copilot, not a transcript or summary dashboard. The current
Meetily UX worktree makes that hierarchy explicit:

- A compact bilingual header identifies the question copilot and shows a visible `Local only` /
  `ローカルのみ` badge.
- The detected issue appears before the dominant ASK NOW card. The card renders at most one active
  primary question, and the secondary list renders at most two active follow-ups.
- Connecting, unavailable, reconnecting, reset, and recovered states use distinct bilingual copy.
  Recording continues during backend failure, and ASK NOW stays hidden while context is unsafe.
- When a pain exists but no active suggestion remains, the panel says there is nothing to clarify
  right now instead of incorrectly saying that no pain was detected.
- Only active facts and open gaps render. Retracted questions and superseded/contradicted facts are
  filtered at the presentation boundary; deterministic reducer/selector logic remains authoritative.
- The panel width is bounded relative to the app window, long Japanese/English text wraps, and the
  panel owns its scroll area for smaller desktop windows.

## Start the product backend

From PowerShell, start the backend before launching Meetily:

```powershell
$env:MEETING_INTELLIGENCE_TOKEN = `
  [Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N")
cd $env:ASSISTANT_ROOT\apps\api
.venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Use the same token in the Meetily process environment. The preferred launcher below does this
without printing or persisting the value. If the token is absent, direct source startup is an
explicit unauthenticated development mode and must not be used for packaged acceptance.

First-time dependency setup is explicit and may contact package registries:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1
```

The normal verification gate is network-disabled and requires those dependencies to exist:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Verify the backend locally at `http://127.0.0.1:8000/health` before recording.

### Experimental semantic provider (not in the V1 runtime)

The product contains a Phase 4C Ollama candidate adapter, but the current FastAPI composition root
does not invoke it. Normal startup remains deterministic even if these variables are present. For a
future eval tool that explicitly uses the selector, the opt-in settings are:

```powershell
$env:MEETING_INTELLIGENCE_SEMANTIC_ANALYZER = "ollama"
$env:MEETING_INTELLIGENCE_OLLAMA_URL = "http://127.0.0.1:11434"
$env:MEETING_INTELLIGENCE_OLLAMA_MODEL = "qwen2.5:7b"
```

Only loopback HTTP URLs are accepted. Do not use this for V1 acceptance yet; no live-model quality
claim has been made, and `scripts\check.ps1` intentionally requires neither Ollama nor network
access.

## One-command local V1 startup

The product launcher starts or reuses the backend, checks `/health`, keeps operational logs under
`%TEMP%\meeting-intelligence-copilot`, and leaves the backend bound to `127.0.0.1`:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 `
  -MeetilyPath $env:MEETILY_ROOT -LaunchMeetily
```

This authenticated path generates a cryptographically random 256-bit token in memory, passes it to
both processes, and does not print it. Use `-DryRun -LaunchMeetily` to inspect the command without
spawning either process. For a deliberate backend-only unauthenticated source session, use
`-AllowUnauthenticatedDev`; that switch is forbidden for packaged acceptance. See
`docs/PACKAGING_PLAN.md` for the exact blocker and the staged packaging strategy.

The backend-sidecar packaging prototype is also dry-run-first and writes only to ignored product
paths when a reviewed PyInstaller installation is available:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-backend-sidecar.ps1 -DryRun
```

A real ignored 15,540,634-byte backend executable candidate
(`FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48`) has been built from the
hash-locked product source and passed authenticated loopback health, missing/invalid-token, ingest,
WebSocket, bounded temporary-log, process-tree shutdown, occupied-port, and Defender checks; it has
not been committed. Build/copy the real ignored
Meetily helper with `$env:MEETILY_ROOT\scripts\setup-meetily-sidecars.ps1` before native
checks. Authenticated lifecycle startup/shutdown, bounded crash restart, two-file 256 KiB privacy-safe
operational-log rotation, unsigned NSIS install/upgrade/rollback/re-upgrade/uninstall, and MSI payload
extraction are proven. Signed payload and packaged GUI/audio acceptance remain blocked.

Run the packaged bilingual backend/transport acceptance independently of GUI and audio hardware:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\run-packaged-acceptance.ps1
```

This controlled-input gate passed the English manual-work and Japanese owner-answer trajectories on
the real executable, including stale ASK NOW retraction, `source_of_truth = 倉庫側`, Japanese
business impact/owner behavior, UTF-8/no mojibake, evidence/cap guards, redacted bounded logs, and
cleanup. It does not replace the Parakeet/Whisper routed-audio and packaged GUI steps below.

## Launch the Meetily integration copy

In a second PowerShell window:

```powershell
cd $env:MEETILY_ROOT\frontend
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
$env:MEETING_INTELLIGENCE_TOKEN = "<same 64-character token used by the backend>"
pnpm run tauri:dev:cpu
```

For this local-only phase, leave `MEETING_COPILOT_URL` and `SPIKE_TAP_PATH` unset. The Rust bridge
defaults to `http://127.0.0.1:8000`, rejects non-loopback URL overrides, and the diagnostic tap is
disabled unless explicitly enabled. Keep Meetily Analytics disabled and disable Auto Summary when
the entire Meetily session must remain offline; these are separate Meetily features, not part of
the IntelligencePanel path.

Configure the language and local STT provider/model before recording starts. Do not load or switch
the Whisper model while recording: the pinned Meetily build can deadlock that operation against the
active transcription worker.

## English acceptance

1. Select English and Parakeet with the local `parakeet-tdt-0.6b-v3-int8` model.
2. Route `synthetic_manual_work.wav` through VB-Audio Virtual Cable into the Meetily capture input.
3. Start recording and confirm the IntelligencePanel connects to the live backend.
4. Play the WAV once and stop recording after the transcript is delivered.

Expected behavior:

- The manual-work pain point appears with evidence-backed facts and the panel shows only one ASK NOW
  question plus no more than two FOLLOW UP questions.
- The opening trajectory asks for business impact, then frequency, then volume after those slots are
  answered.
- “Every morning” is retained as a superseded frequency fact when “every business day” arrives;
  “every business day” is the active fact and the history remains inspectable.
- The final ASK NOW is the volume question, with time cost and owner available as follow-ups. No
  question asks for a slot already answered by an active fact.

## Japanese acceptance

1. Select Japanese and Whisper `small`; load the model before recording starts.
2. Use the local VOICEVOX six-beat inventory-mismatch script with VB-Audio Virtual Cable routed into
   the Meetily capture input. The beats are: inventory mismatch, manual comparison/correction,
   source-of-truth question, source-of-truth answer, shipping-delay impact, and frequency/worker
   statement.
3. Start recording, play the script, and confirm the panel uses Japanese chrome and live updates.

Expected behavior:

- `source_of_truth` is extracted as `倉庫側` with the exact transcript event as evidence.
- Business impact is extracted from the shipping-delay statement.
- The active Japanese ASK NOW question asks who owns the response.
- The panel shows the known facts and missing owner/scope information in natural Japanese; it does
  not romanize Japanese or treat the PM’s question as an owner answer.
- After an explicit answer such as `対応は田中さんが担当しています`, the owner fact is stored as
  evidence, the owner ASK NOW is retracted, and the next unresolved gap is promoted. A split answer
  such as `一次対応は山田さん、最終確認は情シスです` is stored as
  `一次対応: 山田さん / 最終確認: 情シス`.

## Troubleshooting

- **Missing `websockets` dependency:** FastAPI’s WebSocket route needs the explicit API dependency.
  Install the project dependencies in `apps/api/.venv`, then restart uvicorn.
- **CORS/CSP loopback failures:** the backend allow-list includes Meetily’s local origins and the
  Meetily CSP must allow `127.0.0.1:8000`. Browser errors often present as a generic failed fetch.
  WebSocket browser Origins are separately restricted to the same local allow-list.
- **HTTP 401 / WebSocket policy close:** the product and Meetily processes do not share the same
  `MEETING_INTELLIGENCE_TOKEN`, or one side omitted it. Use the authenticated launcher or set the
  same 32–256 character URL-safe token in both process environments. Tokens never belong in URLs.
- **Non-loopback `MEETING_COPILOT_URL`:** the Rust bridge rejects HTTPS, LAN, malformed, credentialed,
  query, and non-root-path values and falls back to `http://127.0.0.1:8000`. The panel applies the
  same policy to its runtime URL override.
- **Transcript diagnostic files:** `SPIKE_TAP_PATH` is unset by default and has no temporary-file
  fallback. Set it only for a deliberate local acceptance capture, then remove it.
- **Telemetry/cloud features:** Analytics is opt-in and disabled by default. Meetily summary and
  remote transcription providers remain separate; disable Auto Summary and choose local Parakeet,
  Whisper, or built-in models for an offline Meetily session. See `docs/LOCAL_ONLY_PRIVACY.md`.
- **Whisper `is_partial`:** it is not authoritative finality in the pinned build. The adapter uses
  sequence/content stability and deduplication; do not discard every `is_partial=true` update.
- **Backend restart:** `LiveMeetingRegistry` is in memory, so restart clears server state. During an
  active recording, the panel clears stale UI state, displays the localized reset/reconnecting text,
  and replays Meetily's ordered `get_transcript_history` through the normal ingest route. If replay
  cannot run, continue recording to rebuild from new events; no stale ASK NOW is retained. Managed
  mode retries only three times at 1/2/4 seconds after two failed health checks, then requires the
  manual retry action; it never spins in a tight loop.
- **Summary path / `llama-helper`:** Meetily’s summary/sidecar path is separate from the intelligence
  path and may have its own model or helper limitations. It is not required for ASK NOW behavior.
- **Experimental Ollama candidate adapter:** it is safe-off and not composed into FastAPI. When used
  by a future eval tool, malformed output, unknown slots/categories, missing evidence, timeout, or a
  non-loopback URL produces no candidates. It never creates ASK NOW directly.
- **BlockNote/prosemirror or `bun:test`:** these were unrelated upstream summary/editor issues. The
  current clean distribution worktree uses Node's test runner and pinned overrides, and its tests,
  typecheck, and Next build pass. Do not weaken intelligence tests if they recur.
- **Dirty Meetily tree:** the known deleted `.github/`, `backend/`, and docs assets are damaged-tree
  artifacts. Do not restore or commit them; use `$env:MEETILY_ROOT` or another clean
  clone/worktree based on `ba06098` or its descendants for future Meetily work.

## Known non-blocking limitations

- Japanese owner extraction supports explicit person/team ownership clauses across all owner-bearing
  templates. Questions, vague future actions, and unresolved-owner statements are intentionally not
  treated as facts; implicit or highly elliptical ownership language remains unsupported.
- `LiveMeetingRegistry` restores valid active context from its bounded SQLite/WAL cache after a
  backend restart. Meetily's ordered transcript history repairs missing cache prefixes; normal stop
  purges the transient session and crash remnants expire after 24 hours.
- The legacy meeting-title session-reuse collision is not supported by older title-keyed clients;
  the current opaque per-recording session id path eliminates that collision for new recordings.
- Capability-token authentication is implemented for sensitive HTTP and WebSocket transport.
  `/health` intentionally remains public and minimal; same-user process inspection is outside this
  application-layer capability boundary.
- Mixed Japanese/English code-switching remains provisional.
- A signed Tauri installer and independent clean-account interactive launch remain unproven. The
  unsigned NSIS install/upgrade/rollback/re-upgrade/uninstall lifecycle passes automation locally.
- Meetily emits fixed privacy-safe lifecycle events to at most two files capped at 256 KiB each;
  transcript text, tokens, raw backend errors, and development paths are excluded.
- Real acceptance uses local synthetic audio/TTS through VB-Audio Virtual Cable rather than a live
  human speaker.

## Verification

Run the product gate from the product repository:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

For Meetily Rust/UI changes, the pinned integration checks are:

```powershell
cd $env:MEETILY_ROOT\frontend\src-tauri
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
cargo check --locked

cd $env:MEETILY_ROOT\frontend
pnpm test
pnpm run build
pnpm exec tsc --noEmit
```

The Node tests, full `tsc --noEmit`, Next production build, `cargo check --locked --offline`, and full
Rust library suite pass in the clean distribution worktree. The hardened candidate passed 13 Node
tests, 202 Rust unit tests (4 ignored by default), one doc test, and the two ignored real-artifact
start/stop and crash/restart tests when run explicitly.
Cargo validation requires the real ignored
target-suffixed `llama-helper` and backend payloads; prepare them with
`scripts\setup-meetily-sidecars.ps1` rather than substituting placeholders.
