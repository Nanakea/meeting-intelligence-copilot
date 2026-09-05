# 0.6.2 Packaged Acceptance

Status: **pending**. Compatibility API v13; JA/EN/KO; Windows 11 x64; unsigned trusted internal use.
No historical binary or previous test count approves this candidate. Historical evidence is retained
in [the archive](history/PACKAGED_ACCEPTANCE_THROUGH_0.6.1.md).

## Freeze and Build

Use one clean monorepo commit and locked dependencies. Set `CARGO_TARGET_DIR` explicitly on build
agents; the local default is `E:\cargo-targets\meeting-intelligence-copilot`. Never stage placeholder
sidecars. Installer assembly must match the backend commit, release metadata, every dependency lock,
and actual sidecar SHA-256. A fix invalidates acceptance and requires a complete new candidate.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
powershell -ExecutionPolicy Bypass -File scripts/build-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts/test-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts/run-packaged-acceptance.ps1
powershell -ExecutionPolicy Bypass -File apps/desktop/scripts/setup-meetily-sidecars.ps1 `
  -MeetingIntelligenceBackendPath dist/backend-sidecar/meeting-intelligence-backend-x86_64-pc-windows-msvc.exe
powershell -ExecutionPolicy Bypass -File apps/desktop/frontend/src-tauri/scripts/build-unsigned-windows.ps1 -Bundle both
```

FFmpeg must be separately pre-staged from an approved, hash-verified distribution. No model is bundled.

## Mandatory Evidence

- Backend: capability authentication, API version `13`, matching release version, loopback-only bind,
  compact WebSocket snapshots, JA/EN/KO ingest, ordered replay, recovery, purge, tombstones, late-POST
  rejection, connector timeout, encrypted local indexing, and direct-write rejection.
- Desktop: complete frontend behavioral tests, TypeScript, scoped zero-warning lint, production
  build, locked Rust tests, bounded restart/backoff, native IME/paste/save/reload, keyboard and
  screen-reader navigation, long multilingual text, and 125%/150% display scaling.
- Reliability: four hours / 10,000 events; zero lost or multiply-applied events, no stale ASK NOW,
  stop <=10 seconds, processing p95 <100 ms, panel transport p95 <500 ms excluding STT, and cleanup.
- Audio: 108 routed scenarios (36 per language) and 24 platform-path scenarios. Source fixtures
  and simulator tests are not audio acceptance. Human-speech acceptance is a separate gate.
- Windows Sandbox: fresh install, launch, upgrade, rollback, uninstall, occupied port, sidecar crash,
  and recording independence. Preparing a Sandbox package does not mean executing it passed.
- Security: dependency and tracked-secret scans, SBOMs, Defender scan results, SHA-256 for every
  executable and installer, corpus/model-manifest hashes, acceptance-ledger hash, and source commit.

## Approval Boundary

`scripts/create-internal-candidate-manifest.py` requires the backend, desktop, NSIS, MSI, SBOM,
corpus, model manifest, and acceptance ledger paths. It hashes them without copying source content
into the manifest, refuses overwrite, and leaves absent gate receipts pending. Each aggregate receipt
has exactly `gate`, `source_commit`, `artifacts` (the same labeled SHA-256 map), and `status`
(`passed`, `failed`, or `pending`). A receipt is an evidence attestation, not a test runner: retain its
underlying test report in the private acceptance ledger. Do not create passed receipts without runs.
Pass the resulting file using `-AcceptanceManifest` to the readiness command.

The manual GitHub `release-verification` workflow runs hosted Windows Rust tests and builds both
installers. It requires an independently reviewed FFmpeg archive hash; it deliberately rejects an
unreviewed or changed download. It does not run native audio or Windows Sandbox in a hosted CI job.
The automatic CI packaged-backend job does not require company credentials or external source access.

Record each missing gate as pending, not passed. Signing, company recording/retention policy,
selected-source tenant access, and a consented human pilot remain external gates. A signed installer
must repeat lifecycle and audio acceptance. Public/end-user distribution: **not ready**.
GUI/audio gates remain open until new evidence is bound to these exact artifact hashes.
