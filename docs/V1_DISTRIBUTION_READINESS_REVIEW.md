# V1 Distribution Readiness Review

> Historical review: this assessment is preserved as the pre-hardening baseline and has been
> superseded by `docs/V1_DISTRIBUTION_HARDENED_CANDIDATE.md`.

Review date: 2026-07-14. Product baseline:
`d470eb74770ca5e321517243910d141c8506a0d0`. Clean Meetily baseline:
`b3b6b9cd4fd6483a28b45787a39cf35d4d3d856f`. The damaged `<legacy-meetily-worktree>` tree was not
inspected or modified.

Verdict: **the local unsigned NSIS/MSI artifacts are built, verified, and ready for controlled
human install/signing review. End-user distribution is not ready.** Signing and human installation
are not the only remaining end-user gates; packaged GUI/audio, clean-machine execution, bounded
persistent diagnostics, Python runtime locking, and inherited Meetily capability hardening remain.

## Readiness matrix

| Area | Classification | Evidence and boundary |
|---|---|---|
| 1. Product engine | **Ready** | Deterministic `MeetingEngine` remains authoritative; evidence/inference and fact lifecycle are separate; one ASK NOW/two FOLLOW UP caps and answered-gap retraction pass. No cloud/model provider owns state. |
| 2. Bilingual behavior | **Ready with bounded coverage** | 13/13 JA/EN goldens pass, prior real Parakeet/Whisper routed-audio evidence exists, and packaged controlled EN manual-work plus JA owner-answer trajectories pass. Mixed code-switching and elliptical JA owner language remain bounded. |
| 3. Meetily integration | **Ready for local candidate** | Real Rust transcript push, versioned loopback WebSocket snapshots, panel render, actionable lifecycle states, and recording independence pass. The clean worktree is frozen separately from the damaged tree. |
| 4. Session identity | **Ready** | Opaque per-recording `meeting-intel-<UUID>` identity is shared across push, registry, and panel. Older title-keyed clients remain incompatible. |
| 5. Backend recovery | **Ready for active recording** | Two failed health checks trigger at most three 1/2/4-second restarts; 60 seconds stable resets the budget; manual retry, stale ASK NOW clearing, ordered history replay, and owned process-tree cleanup pass. State is not durable after recording/app shutdown. |
| 6. Local-only privacy | **Ready for the Meeting Intelligence path, with inherited app caveats** | Backend and bridge enforce loopback; meeting content and capability tokens are not sent to cloud. Automatic updater checks were removed in `b3b6b9c`; explicit user update checks still contact GitHub release metadata. Meetily's separate telemetry/remote providers must remain disabled for a fully offline session. |
| 7. Capability-token auth | **Ready for local V1 threat model** | A 256-bit per-launch token protects sensitive HTTP and WebSocket routes, is carried outside URLs, remains in memory, is redacted, and clears on shutdown. This is not an OS sandbox against a malicious process running as the same user. |
| 8. Packaged backend sidecar | **Ready as an unsigned local component** | Ignored 15,539,241-byte PyInstaller artifact, SHA-256 `FAD93ED97CE12D96DFEE308C5728E6A72542DCADA9F90AF3A94A85FCD816113C`, passes loopback/auth/compatibility/transport/shutdown/occupied-port/redaction checks and Defender. It is `NotSigned`. |
| 9. Managed lifecycle/restart | **Ready** | Real artifact start/stop and forced-crash restart tests pass; incompatible/missing/startup/stopped reasons are fixed and privacy-safe; recording/STT remain independent. Managed backend stdout/stderr are null rather than persistently logged. |
| 10. Packaged JA/EN acceptance | **Partially ready** | Real packaged backend controlled-transcript acceptance passes both languages with stale retraction, evidence, caps, no mojibake, token auth, and cleanup. Installed GUI, routed Parakeet/Whisper audio, and consented human speech are manual. |
| 11. Installer/signing | **Partially ready / release blocked** | Real ignored NSIS and MSI candidates build with all three real sidecars and pass Defender with zero detections. Both are `NotSigned`; neither has been installed. Authenticode, updater signing, SmartScreen, UAC, upgrade/rollback, uninstall, and signed-payload checks remain. |
| 12. Fresh-machine setup | **Partially ready** | `FRESH_MACHINE_SETUP.md` and a read-only prerequisite checker cover tools, caches, sidecars, models, offline gates, and failures. The checker passes required prerequisites on the development machine. No clean-machine/account run exists. |
| 13. Tests/goldens | **Ready** | Product gate passes 214 API tests, schema sync, 13/13 goldens, 21 web tests, TypeScript, Vite, and offline/frozen pnpm. Meetily passes 13 Node tests, TypeScript, Next builds, locked Rust checks, prior 201 runnable Rust tests with 4 ignored, lifecycle artifact tests, and dual-format bundling. |
| 14. Security/audit | **Partially ready** | Current product Python audit and pnpm audit report no known vulnerabilities; `pip check` is clean. Meetily has zero moderate/high/critical pnpm findings and one no-fix low Babel finding. Secret/private-key marker scans are clean; generated candidates are ignored; Defender scans are clean. Unsigned payloads, broad inherited Tauri filesystem permissions, and final signed-binary review remain. |
| 15. Logs/persistence | **Partially ready** | Acceptance logs have a one-megabyte ceiling, are token/transcript-redacted, and are removed on success. Managed backend output is discarded, avoiding transcript persistence but limiting diagnostics. `LiveMeetingRegistry` is memory-only. A bounded/rotated persistent operational log design is not verified. |
| 16. Updater/versioning | **Partially ready** | Meetily version `0.4.0` consumes compatibility API v3/backend 0.4.0. Remote checks are user initiated. Signed updater artifacts, update install, rollback, key custody, and publication metadata are untested. |
| 17. Upstream Meetily issues | **Deferred/known** | Next reports ESLint absent while separate tests/typecheck/build pass; Cargo reports workspace-level patch/profile warnings; one low Babel advisory has no published 7.x fix; optional-MySQL RSA remains in lock metadata but not the Windows SQLite build graph. A Microsoft-signed `frontend/vs_buildtools.exe` is inherited/tracked but not bundled. |
| 18. Remaining manual steps | **Blocked on human evidence** | Clean-account install/launch, SmartScreen/UAC, JA/EN packaged audio, human speech, failure UX, upgrade/rollback/uninstall, user-data retention, fresh machine, and post-signing scan/reputation behavior. |

## Artifact record under review

| Artifact | Bytes | SHA-256 | Signature | Defender |
|---|---:|---|---|---|
| Backend EXE | 15,539,241 | `FAD93ED97CE12D96DFEE308C5728E6A72542DCADA9F90AF3A94A85FCD816113C` | `NotSigned` | zero detections |
| NSIS setup | 58,835,095 | `F30109D5BE36E3DFB85276F81F5A01611E480DAE5EC39A937A34F6078D829E29` | `NotSigned` | zero detections |
| MSI | 85,598,208 | `1C3FBCEB40D756AE06142BEAF117BDF6B6ECD10F3F17C40C02258B9381D70AAD` | `NotSigned` | zero detections |

Defender was enabled with real-time protection and signature version `1.455.125.0`. Generated
artifacts are ignored and uncommitted. Every rebuild or signed derivative needs a new hash and scan.

## Security and dependency review

The current audit evidence is:

- `uvx pip-audit --path .venv\Lib\site-packages --progress-spinner off`: no known vulnerabilities;
- `python -m pip check`: no broken requirements;
- product `pnpm audit --audit-level=moderate`: no known vulnerabilities;
- Meetily `pnpm audit --audit-level=moderate`: zero moderate-or-higher findings;
- full Meetily audit: one low `@babel/core` 7.29.0 advisory. Advisory metadata names 7.29.1 as
  patched, but the registry does not publish that version; forcing Babel 8 is an unreviewed major
  change, so the low advisory remains documented rather than masked;
- Rust audit tooling was not installed in the current shell. The prior full audit left one no-fix
  RSA advisory in SQLx optional-MySQL lock metadata; `cargo tree --locked -i rsa` prints no path for
  the Windows SQLite build graph;
- tracked-file scans found no private-key PEM markers in either repository and no generated
  product/installer candidates committed;
- Meetily inherits an empty `frontend/.env.example` and a validly Microsoft-signed
  `frontend/vs_buildtools.exe` from pinned upstream parent `0281737`; the latter is not declared as a
  Tauri resource or external binary and was not executed; and
- normal startup no longer fetches updater metadata. The explicit updater action remains remote and
  must never include meeting content or the local capability token.

The primary inherited capability concern is Meetily's broad Tauri `fs:read-all`/`fs:write-all`
permission set. It predates this integration and is not exercised by Meeting Intelligence, but it
expands impact if the webview is compromised. Narrowing it requires an upstream application-wide
file-access inventory and regression plan; it is an end-user release hardening item, not something
to change blindly during the candidate freeze.

## Verification evidence

Product:

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Result: 214 API tests, schema sync, 13/13 goldens, 21 web tests, TypeScript, Vite, and
offline/frozen pnpm all pass. The only product warning is the known Starlette/httpx deprecation.

Meetily evidence across the installer and updater commits:

```powershell
cd $env:MEETILY_ROOT\frontend\src-tauri
cargo check --locked

cd $env:MEETILY_ROOT\frontend
pnpm exec tsc --noEmit
pnpm test
pnpm build
```

The production Tauri command built both formats through the reviewed unsigned wrapper. Prior full
managed-lifecycle evidence passed 201 runnable Rust tests with 4 ignored; the ignored tests are the
real artifact lifecycle cases and were run explicitly against the packaged backend. Existing
compiler, Node module-type, and missing-ESLint warnings remain non-fatal and documented.

## Exact remaining blockers

Before end-user distribution:

1. run NSIS first-install/launch, failure-path, same-format upgrade, rollback, uninstall, and
   user-data retention checks on a clean Windows account;
2. run installed packaged GUI/audio acceptance with Parakeet English and Whisper small Japanese,
   then a consented JA/EN human-speech pilot;
3. define and verify bounded/rotated operational diagnostics without transcript/token leakage;
4. generate/review a hash-locked Python 3.12 runtime constraints set for reproducible rebuilds;
5. inventory and narrow inherited broad Tauri filesystem capabilities without breaking required
   local file/model/meeting workflows;
6. perform a fresh-machine cache/setup/install run;
7. sign every final executable/installer and Tauri updater artifact from protected credentials, then
   rehash, scan, and test SmartScreen/UAC/update/rollback behavior; and
8. decide how to handle the no-fix low Babel advisory and inherited upstream binary in the long-term
   upstream maintenance plan.

## Release decision

Create and preserve local distribution-candidate tags only after the final documentation gate is
green and both repositories are clean apart from the product's four accepted pre-existing untracked
paths. The candidate may be handed to a trusted human reviewer by exact local path and SHA-256.

Do not publish, push, auto-update, or call it end-user ready. Do not enable cloud AI, RAG, or the
semantic runtime. The next concrete action is a clean-account NSIS install and packaged JA/EN
acceptance run, followed by capability/logging hardening and real signing.
