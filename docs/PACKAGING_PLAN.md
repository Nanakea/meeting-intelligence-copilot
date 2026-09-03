# V1 Packaging and Startup Plan

> **Superseded artifact evidence:** this file preserves the V1 packaging history. Its pass records,
> hashes, and test counts predate the current API v8 candidate and cannot approve that candidate.
> Current release gates are in `docs/INTERNAL_PILOT.md` and
> `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md`.
>
Status: **an authenticated real ignored PyInstaller candidate, controlled packaged JA/EN transcript
acceptance, the real Meetily `llama-helper`, an opt-in Meetily-owned lifecycle with bounded crash
restart and persistent diagnostics, and unsigned NSIS/MSI lifecycle/payload checks pass locally;
signing and independent clean-machine packaged GUI/audio acceptance remain open**.
This plan is for the local-first Meeting Intelligence Copilot V1. It does not introduce Ollama,
RAG, cloud AI, or a new model provider.

## Current reproducible state

- Product repository: `$env:ASSISTANT_ROOT`; the frozen local-demo tag remains
  at `2eecf1a`; the runtime-hardened tag is `meeting-intelligence-v1-runtime-hardened` at
  `c2e029d6c028251fc0784ff77b1d7bf472d5e47b`.
- Clean Meetily local-only base: `<legacy-meetily-worktree>-local-only`, commit `5534dc8`, branch
  `spike/local-only`.
- Clean packaging inspection worktree: `<legacy-meetily-worktree>-packaging`, commit `5534dc8`, branch
  `spike/packaging-startup`.
- Latest clean distribution worktree: `$env:MEETILY_ROOT`, based on panel/local-only base
  `ba06098`, with real sidecar setup commit `b8b99d9`, lifecycle commit `c1e0299`, and hardening
  runtime-hardening commit `f05b2ca`, capability-token commit `f9d4aa7`, and bounded-restart commit
  `499cb872286d8af65806fce3e75c44ccc2987fdf`, managed-lifecycle status commit `6f90f71`, unsigned
  installer build commit `a81c39d`, user-initiated updater commit `b3b6b9c`, and distribution
  hardening commit `58f26bbbf8827d3bba427b10749ebe25ec9a6d4e` on
  `spike/distribution-readiness`. Patch backups are
  `<legacy-patch-backup>\meetily-distribution-b8b99d9.patch` and
  `<legacy-patch-backup>\meetily-distribution-c1e0299.patch`, plus
  `<legacy-patch-backup>\0001-fix-harden-Meetily-distribution-runtime-f05b2ca.patch`
  `<legacy-patch-backup>\meetily-capability-token-f9d4aa7.patch`, and
  `<legacy-patch-backup>\meetily-restart-backoff-499cb87.patch`, and
  `<legacy-patch-backup>\meetily-managed-sidecar-6f90f71.patch`,
  `<legacy-patch-backup>\meetily-unsigned-installer-a81c39d.patch`, and
  `<legacy-patch-backup>\meetily-user-initiated-updater-b3b6b9c.patch`.
  Its frozen local tag is `meeting-intelligence-meetily-runtime-hardened` at
  `f05b2ca9238f33f957f5bb77089920f05eb387c5`.
- Original `<legacy-meetily-worktree>` remains a separate dirty tree with known damaged-tree deletions. It
  was not modified, restored, or used for this work.

## Runtime topology

The smallest safe V1 startup topology is two local processes:

```text
start-local-v1.ps1
  -> apps/api/.venv/Scripts/python.exe -m uvicorn app.api.main:app
     -> 127.0.0.1:8000/health

Meetily Tauri app
  -> authenticated POST 127.0.0.1:8000/ingest/meetily/{session_id}
  -> authenticated WS   127.0.0.1:8000/ws/meeting/{session_id}
```

The backend remains the source of truth and the frontend remains a render of versioned full
snapshots. The existing `/health` endpoint is intentionally minimal: `{"status":"ok"}`; the
separate authenticated `/health/compatibility` endpoint carries product, API, backend, and
capability-auth status. The current transport contract is API version 4 / backend version 0.6.0.

## Product launcher

`$env:ASSISTANT_ROOT\scripts\start-local-v1.ps1` is the near-term
Windows launcher. It is intentionally backend-first:

- It resolves the product repository from the script location, so the caller's current directory
  does not change the product path.
- It requires `apps/api/.venv/Scripts/python.exe`. If it is missing, it prints the exact bootstrap
  command: `powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1`, followed by the
  offline verification command.
- It starts Uvicorn on `127.0.0.1` only, polls `/health`, and writes only operational stdout/stderr
  to `%TEMP%\meeting-intelligence-copilot`.
- Authenticated two-process launch generates a 256-bit capability token with the Windows-compatible
  cryptographic RNG, passes it to both child processes, and never prints or persists it.
- It requires the exact authenticated API v8 compatibility response before reusing a backend.
  Backend-only unauthenticated startup requires the explicit `-AllowUnauthenticatedDev` switch.
- It reuses an already healthy backend instead of starting a second fixed-port process.
- It does not persist environment variables. Meetily's toolchain variables are set only on the
  child launch process and restored in the launcher process.
- `-MeetilyPath` defaults to the clean distribution worktree. `-LaunchMeetily` is optional and checks
  the real Windows sidecar before launching; it never creates a fake executable or a placeholder
  command.

Examples:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 `
  -MeetilyPath $env:MEETILY_ROOT -LaunchMeetily

# Inspect startup commands without spawning a process.
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 -LaunchMeetily -DryRun

# Use the latest clean distribution worktree with its real helper prepared.
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 `
  -MeetilyPath $env:MEETILY_ROOT -LaunchMeetily
```

The launcher does not stop an existing backend or Meetily process. To stop a backend started by
the launcher, use its displayed PID or the normal local process controls after confirming the PID.

## Manual Meetily startup command

The actual Meetily development command is:

```powershell
cd $env:MEETILY_ROOT\frontend
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
pnpm run tauri:dev:cpu
```

Set the same `MEETING_INTELLIGENCE_TOKEN` used by the backend when launching manually. Keep
`MEETING_COPILOT_URL` and `SPIKE_TAP_PATH` unset for the normal local-only path. The bridge
and panel default to `http://127.0.0.1:8000` and reject non-loopback overrides. Configure the local
STT language and model before recording starts; do not load Whisper while recording.

## Meetily helper resolution

The clean packaging worktree was inspected at `5534dc8` without source changes. Its
`frontend/src-tauri/tauri.conf.json` declares:

```json
"externalBin": [
  "binaries/llama-helper",
  "binaries/ffmpeg"
]
```

The exact pinned Windows check was run from `frontend\src-tauri` with the required environment:

```powershell
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
cargo check --locked
```

Result: **exit 101**. The build script downloaded and verified
`ffmpeg-x86_64-pc-windows-msvc.exe`, then Tauri failed configuration validation because this
real resource was absent:

```text
binaries\llama-helper-x86_64-pc-windows-msvc.exe
```

This is a packaging/build blocker, not a V1 intelligence-engine failure. `llama-helper` belongs to
Meetily's separate local summary path. The Tauri externalBin declaration is validated during the
custom build step, so `cargo check`, `tauri dev`, and `tauri build` cannot be treated as clean until
the target-suffixed payload exists. A fake binary, `cmd` placeholder, or hidden failure would make
the result misleading and is explicitly out of scope.

The source is present in the repository workspace as the real `llama-helper` Rust package. The clean
distribution worktree now provides a reproducible setup command:

```powershell
cd $env:MEETILY_ROOT
powershell -ExecutionPolicy Bypass -File scripts\setup-meetily-sidecars.ps1
```

The script builds the real CPU helper, smoke-tests its `ping`/`shutdown` JSON protocol, and copies
the 3,782,144-byte result to the ignored target-suffixed Tauri path. No fake or generated binary is
committed. With that helper and the repository-managed FFmpeg payload present,
`cargo check --locked` passes in `$env:MEETILY_ROOT`. The previous externalBin blocker is
resolved for local Windows validation. Unsigned NSIS and MSI bundling now passes; signed production
bundling remains unverified.

Pass the real ignored backend candidate to the same script with
`-MeetingIntelligenceBackendPath`. It copies the executable to the ignored target-suffixed Tauri
path and refuses to invent a placeholder. Commit `c1e0299` adds the corresponding `externalBin`
declaration and an opt-in Rust lifecycle prototype; `f05b2ca` adds ended-session cleanup and the
verified runtime/dependency hardening; `f9d4aa7` adds capability-authenticated transport;
`499cb87` adds bounded restart supervision plus manual retry; and `6f90f71` adds privacy-safe,
actionable missing/incompatible/startup/stopped status across Rust and the JA/EN panel.

## Packaging strategy

The strategy is staged:

1. **Proven locally — standalone backend plus managed `externalBin` lifecycle.** The exact hashed
   PyInstaller artifact, target-specific Tauri input, compatibility handshake, token transport,
   restart budget, and controlled bilingual packaged acceptance all pass.
2. **Current candidate — unsigned Windows installers.** The separate build flavor preserves release
   signing defaults while generating ignored NSIS/MSI candidates with the three real sidecars. It
   is for controlled human review only.
3. **Release gate — signing and clean-account acceptance.** Sign the final payload and updater
   artifacts from protected CI secrets, then test install, upgrade, rollback, uninstall, packaged
   GUI/audio, logs, and security behavior before publication.

A Rust rewrite is not the next step. It would duplicate the working engine, discard its Python
   golden/evaluation moat, and expand the current native build surface without solving the missing
   sidecar payload. The adapter port remains the correct boundary.

Python dependencies currently use minimum-version ranges in `apps/api/pyproject.toml`.
`scripts/bootstrap-dev.ps1` is the explicit network-capable setup path; `scripts/check.ps1` never
installs Python packages and forces pnpm offline with a frozen lockfile. Before distributing a
standalone engine, add a generated lock/constraints artifact from a verified Python 3.12 environment
and record its build hash; do not silently depend on whatever packages happen to be installed on a
user machine.

## Backend executable candidate

The product now has a standalone entry point at `apps/api/app/sidecar_main.py`. It imports the same
FastAPI app used in development, accepts only a validated port through
`MEETING_INTELLIGENCE_BACKEND_PORT`, and fixes the host to `127.0.0.1`; there is no packaged option
to bind a LAN interface.

`apps/api/requirements-packaging.txt` pins PyInstaller 6.21.0 and all six transitive packaging
dependencies to exact versions and Windows-wheel SHA-256 values. Populate an approved wheelhouse in
an explicit network-capable setup step, then install offline with hash enforcement:

```powershell
cd $env:ASSISTANT_ROOT
apps\api\.venv\Scripts\python -m pip download --only-binary=:all: --require-hashes `
  --dest <approved-wheelhouse> -r apps\api\requirements-packaging.txt
apps\api\.venv\Scripts\python -m pip install --no-index --require-hashes `
  --find-links <approved-wheelhouse> -r apps\api\requirements-packaging.txt
```

`scripts/build-backend-sidecar.ps1` rejects any environment that does not exactly match all seven
versions in the hashed packaging requirements and never installs packages or contacts a registry.
It removes only the expected ignored artifact before building so a failed rebuild cannot leave a
stale executable, and fixes `PYTHONHASHSEED`, UTF-8 mode, and `SOURCE_DATE_EPOCH` from product HEAD.
Its default behavior builds a target-suffixed one-file executable at the ignored path:

```text
dist/backend-sidecar/meeting-intelligence-backend-x86_64-pc-windows-msvc.exe
```

Inspect it without building:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\build-backend-sidecar.ps1 -DryRun
```

The Windows candidate built successfully from Python 3.12.10 with PyInstaller 6.21.0:

| Check | Result |
|---|---|
| Artifact size | Recompute for the API v3 capability-auth pilot artifact before distribution. |
| Candidate SHA-256 | `FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48` |
| Source reproducibility boundary | Built from exact product commit and hash-locked dependency inputs; cross-machine byte reproducibility is not claimed |
| Cold compatibility readiness | 1,184 ms direct and 1,297 ms bilingual on the measured Windows development machine |
| Listener | `127.0.0.1` only on an injected non-default port |
| Packaged transport | Minimal `/health`; authenticated compatibility, HTTP ingest, and WebSocket v0→v1 smoke passed; missing and invalid tokens rejected |
| Windows Defender | Custom scan completed with zero detections; real-time protection enabled, signatures `1.455.125.0` |
| Authenticode | `NotSigned` — remains a distribution blocker |

PyInstaller reported only platform/optional imports in its warning inventory; the packaged HTTP and
WebSocket smoke confirms the required runtime imports. The one-file executable launches an extracted
child process: stopping only the launcher can leave the listener alive. The lifecycle prototype
therefore retains the owned root PID and performs a scoped Windows process-tree shutdown. Its
ignored real-artifact test confirmed that the loopback listener disappears after shutdown.

The script never copies the executable into Meetily. Remove `dist\backend-sidecar` and
`tmp\pyinstaller` to clean the candidate and build work files; remove an approved temporary
wheelhouse separately when it is no longer needed. All are ignored and no generated artifact is
committed.

## Executable acceptance automation

`scripts\test-backend-sidecar.ps1` launches the real ignored artifact on an unused injected port and
fails closed if that port is occupied. It validates the exact minimal and compatibility health
contracts, confirms a `127.0.0.1`-only listener, drives native WebSocket v0→v1 behavior through a
synthetic Meetily HTTP event, checks that operational logs omit a unique transcript marker, and
terminates the full PyInstaller process tree before verifying that the listener disappeared. The
harness generates a fresh token, requires it for sensitive HTTP and WebSocket calls, verifies
missing and invalid tokens are rejected, and confirms the token is absent from captured logs. It
checks each short-lived stdout/stderr file before reading it, enforces a one-megabyte default ceiling,
and removes the uniquely scoped temp directory after a successful inspection. `-KeepLogs` is an
explicit diagnostic option; failed runs retain their logs for investigation.

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\test-backend-sidecar.ps1
```

The measured artifact passed both the normal run and the occupied-port rejection path. See
`docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for the executable gate and the still-required signed
installer, packaged GUI, JA/EN speech, crash/recovery, upgrade, and uninstall checks.

## Bilingual packaged transport automation

`scripts\run-packaged-acceptance.ps1` invokes the reviewed Python 3.12 harness at
`apps/api/scripts/packaged_bilingual_acceptance.py`. It starts the real ignored executable with an
ephemeral token and replays controlled EN `manual-work-en` and JA `owner-answer-ja` fixture text
through authenticated loopback ingest while consuming native WebSocket snapshots.

The measured run passed every golden checkpoint relevant to those trajectories, including stale
question retraction/promotion, `source_of_truth = 倉庫側`, Japanese business impact and owner
behavior, one-ASK/two-follow-up caps, evidence traceability, UTF-8/no mojibake, explicit session
cleanup, bounded/redacted temporary logs, and process-tree cleanup. It calls no cloud/Ollama service
and needs no model. This is packaged backend/transport proof only; Meetily GUI installation plus
Parakeet/Whisper routed-audio and consented human-speech acceptance remain manual blockers.

Measured Windows development footprint at inspection time:

| Item | Bytes | Interpretation |
|---|---:|---|
| Complete API venv | 76,529,417 | Upper-bound context, not expected artifact size |
| Product `apps/api/app` source | 263,857 | Domain/application code is small relative to runtime |
| FastAPI package | 1,360,047 | Measured installed package tree |
| Starlette package | 588,919 | Measured installed package tree |
| Pydantic + pydantic-core | 9,284,269 | Includes native extension payload |
| Uvicorn package | 535,979 | Measured installed package tree |
| websockets package | 1,316,820 | Measured installed package tree |

These package-tree figures remain useful context, but the measured one-file candidate above now
replaces the earlier feasibility-only estimate. Rebuild hashes are not claimed to be bit-for-bit
reproducible; each release candidate must record and sign its own hash.

## Concrete Tauri sidecar path

The hardened authenticated lifecycle slice is implemented through clean Meetily commit `6f90f71`, never in
`<legacy-meetily-worktree>`:

1. Build and smoke-test the backend artifact outside Meetily. Verify `/health`, HTTP ingest,
   WebSocket snapshots, both languages, restart recovery, exit behavior, SHA-256, size, cold start,
   and that logs contain no transcript text.
2. Copy the real target-suffixed executable to
   `frontend/src-tauri/binaries/meeting-intelligence-backend-x86_64-pc-windows-msvc.exe`; do not
   commit a placeholder. Commit `c1e0299` adds `binaries/meeting-intelligence-backend` to Tauri
   `externalBin` in the same clean change that owns lifecycle management. Commit `f05b2ca` adds
   ended-session cleanup and dependency/runtime hardening; `f9d4aa7` adds in-memory token generation
   and authenticated native/webview transport; `499cb87` adds bounded restart/backoff and manual
   retry without changing recording or STT; `6f90f71` distinguishes missing executable,
   incompatible backend, startup failure, and stopped/unhealthy backend without serializing paths or
   exception text to the webview.
3. Rust validates the configured loopback URL, passes its port and generated capability token through
   the child process environment, spawns the sidecar hidden, and waits for an authenticated versioned
   health handshake before treating the managed process as ready. Non-loopback overrides fall back
   to the validated local default. The token is process-memory only and clears on shutdown.
4. Sidecar startup is opt-in (`MEETING_INTELLIGENCE_MANAGE_SIDECAR=1`) and best-effort. Recording and
   local STT continue if startup fails. The supervisor requires two consecutive failed health checks,
   then permits three automatic restarts at 1/2/4 seconds. A 60-second stable run resets the budget;
   exhaustion stops automatic retries and exposes a manual retry action. On every retry, stale ASK
   NOW state is cleared and the existing ordered transcript-history replay path is reused.
5. Store only operational sidecar logs in the normal app log directory, redact transcript content,
   rotate/bound files, and include exit code plus backend build/version. The current temp-log launcher
   remains the development fallback.
6. Version the backend artifact, Meetily bridge, and wire contract together. The backend now keeps
   `/health` minimal and exposes product/API/backend versions at `/health/compatibility`; automatic
   startup must require that handshake and reject incompatible versions.
7. Sign/hash the final artifact and test installer upgrade, rollback, uninstall cleanup, port
   collision, crash recovery, and Windows Defender behavior before calling the sidecar distributable.

One-file PyInstaller is the prototype because it maps directly to Tauri `externalBin`; one-folder
delivery remains a fallback if extraction latency, dynamic imports, native DLL loading, or security
software makes one-file unreliable. A Rust rewrite is still not justified.

## Version/update coordination

- The product backend, its API/schema contracts, and the Meetily bridge must be versioned together
  for a release. A bridge built against a changed contract is not a compatible update merely because
  its WebSocket still opens.
- The backend owns snapshot versioning and deterministic state. The launcher must not add a second
  state store or a model-driven startup decision.
- The backend exposes an authenticated `/health/compatibility` handshake while preserving the minimal
  public `/health` response. Meetily commit `f9d4aa7` requires the exact expected product/API version before
  retaining a managed backend, reuses a compatible existing listener without owning it, and keeps
  failed engine startup non-blocking for recording.
- Version `0.4.0` is aligned across Tauri config, the frontend package, and Cargo. Bump and review all
  three together, with Tauri config as the release authority.
- The signed-release config retains updater artifact generation. The unsigned flavor disables it
  because Tauri updater signatures require a private key. Remote update checks are user initiated in
  `b3b6b9c`; normal application startup does not contact the GitHub release endpoint.
- Development rollback remains simple: stop managed launch and run the backend manually. Automated
  NSIS rollback to the archived original candidate and re-upgrade to the hardened candidate pass;
  signed and cross-machine rollback evidence remains pending.

## Windows and macOS notes

- **Windows (verified environment):** Rust stable, MSVC/Windows SDK, LLVM/libclang, pnpm, and the
   `Visual Studio 17 2022` CMake generator are required for the Meetily native build. The launcher
   uses the product's existing Python 3.12 venv and loopback-only Uvicorn command. Tauri produced
   both NSIS and WiX MSI candidates. The first build downloaded hash-validated NSIS 3.11 and WiX
   3.14.1 tooling; an offline first build must pre-seed those Tauri caches.
- **macOS (source-known only):** Tauri declares hardened runtime and an entitlements file. A
  macOS package, signing flow, sidecar target name, and acceptance run were not verified here; do
  not treat the Windows result as macOS packaging evidence.

## Privacy and troubleshooting

The normal path binds only to `127.0.0.1`, uses no cloud AI, Ollama, or RAG, and writes launcher
logs to the user's temp directory. The launcher does not persist secrets or toolchain variables.
Meetily's unrelated telemetry, remote providers, and summary features remain separate and must be
disabled when the entire Meetily session must stay offline. The updater now checks remote release
metadata only after explicit user action.

For operational failures, use the following classification:

- Missing `websockets`: install the product API dependencies in `.venv` and restart Uvicorn.
- CORS/CSP loopback failure: confirm the backend and Meetily origins are the documented local ones
  and that Meetily allows `127.0.0.1:8000` in its CSP.
- Whisper `is_partial` is not authoritative finality; sequence/content stability is the adapter's
  authority.
- Backend restart clears the in-memory registry; active-recording history replay is the recovery
  path, not durable storage.
- Summary path / `llama-helper`: separate from ASK NOW. If the ignored payload is missing, run
  `scripts\setup-meetily-sidecars.ps1` in the clean distribution worktree; never substitute a fake
  executable.
- BlockNote/prosemirror: dependency overrides are rooted in `pnpm-workspace.yaml`; the former
  `bun:test`-only test now runs under Node. Do not weaken product assertions to mask editor issues.
- Dirty Meetily status: the deleted `.github/`, `backend/`, and docs assets in `<legacy-meetily-worktree>` are
  known damaged-tree artifacts. Leave them alone and use `$env:MEETILY_ROOT` or another
  clean clone/worktree based on `ba06098` or its descendants for future distribution work.

## Known non-blocking limitations

- Implicit or elliptical JA owner extraction remains unimplemented; explicit person/team ownership
  clauses are implemented and evidence-backed in the current product.
- `LiveMeetingRegistry` is memory-authoritative while running and uses a bounded SQLite/WAL cache
  for active-session restart recovery. The cache is transient, not long-term meeting storage.
- Legacy meeting-title session reuse can collide for older title-keyed clients; current opaque
  per-recording session ids avoid that collision.
- Capability-token authentication protects sensitive loopback HTTP and WebSocket routes; it is an
  application capability, not an OS sandbox against a malicious same-user process.
- Mixed JA/EN code-switching remains provisional.
- Unsigned NSIS install/upgrade/rollback/re-upgrade/uninstall and MSI payload extraction are proven,
  but Authenticode, signed updater artifacts, independent clean-account GUI launch, routed audio, and
  human-speech acceptance remain unproven. Generated installers and sidecars remain ignored build
  artifacts. See `docs/INSTALLER_SIGNING_PLAN.md`.
