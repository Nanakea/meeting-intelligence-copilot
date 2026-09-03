# V1 Local Unsigned Distribution Candidate

> Historical freeze: this original candidate is preserved for traceability and has been superseded
> by `docs/V1_DISTRIBUTION_HARDENED_CANDIDATE.md`. Do not reuse its artifact hashes as the current
> candidate.

Frozen on 2026-07-14. This is a **local unsigned candidate for controlled human install/signing
review**, not an end-user release.

## Frozen commits and tags

| Repository | Clean worktree | Frozen commit | Local tag |
|---|---|---|---|
| Meeting Intelligence product | `$env:ASSISTANT_ROOT` | `5f92ee20ec6ad1881453e38c19e480f7c4239cfb` | `meeting-intelligence-v1-distribution-candidate` |
| Meetily distribution | `$env:MEETILY_ROOT` | `b3b6b9cd4fd6483a28b45787a39cf35d4d3d856f` | `meeting-intelligence-meetily-distribution-candidate` |

The product tag points to the verified readiness-review commit immediately before this final record,
as required by the freeze sequence. This document is committed separately as
`docs: freeze V1 distribution candidate record`.

Earlier local rollback tags remain:

- product `meeting-intelligence-v1-runtime-hardened` at
  `c2e029d6c028251fc0784ff77b1d7bf472d5e47b`;
- product `meeting-intelligence-v1-local-demo` at
  `2eecf1ae2b13cbaf68dc4776c267e4274ef305e8`; and
- Meetily `meeting-intelligence-meetily-runtime-hardened` at
  `f05b2ca9238f33f957f5bb77089920f05eb387c5`.

The original damaged `<legacy-meetily-worktree>` tree was never inspected, modified, restored, staged, or
committed during this distribution-candidate run.

## Candidate artifacts

All artifacts are generated, ignored, and uncommitted.

| Artifact | Local path | Bytes | SHA-256 | Authenticode | Defender |
|---|---|---:|---|---|---|
| Backend sidecar | `$env:ASSISTANT_ROOT\dist\backend-sidecar\meeting-intelligence-backend-x86_64-pc-windows-msvc.exe` | 15,539,241 | `FAD93ED97CE12D96DFEE308C5728E6A72542DCADA9F90AF3A94A85FCD816113C` | `NotSigned` | zero detections |
| NSIS installer | `$env:MEETILY_ROOT\target\release\bundle\nsis\meetily_0.4.0_x64-setup.exe` | 58,835,095 | `F30109D5BE36E3DFB85276F81F5A01611E480DAE5EC39A937A34F6078D829E29` | `NotSigned` | zero detections |
| MSI installer | `$env:MEETILY_ROOT\target\release\bundle\msi\meetily_0.4.0_x64_en-US.msi` | 85,598,208 | `1C3FBCEB40D756AE06142BEAF117BDF6B6ECD10F3F17C40C02258B9381D70AAD` | `NotSigned` | zero detections |

Defender was enabled with real-time protection and signature version `1.455.125.0` for the recorded
scans. A zero-detection result is point-in-time evidence. The installer hashes correspond to Meetily
`b3b6b9c`, where remote updater checks are explicit/user initiated rather than automatic at startup.

The Tauri manifests include the three real stable runtime sidecars beside `meetily.exe`:

- `ffmpeg.exe` (source input SHA-256
  `5AF82A0D4FE2B9EAE211B967332EA97EDFC51C6B328CA35B827E73EAC560DC0D`);
- `llama-helper.exe` (source input SHA-256
  `B206494A702A5D93395D4F9AB4AF9352CEDA1175BE1F330CA9D17F4BC83DB1D7`); and
- `meeting-intelligence-backend.exe` (same backend SHA-256 as above).

No placeholder or fake executable was used.

## Commits created during distribution hardening

Product:

- `e7a1473` — `docs: freeze runtime hardened baseline`
- `63aa58f` — `feat: require local capability token for intelligence transport`
- `7aeabc3` — `docs: record bounded intelligence restart recovery`
- `a1dfdb3` — `test: harden packaged backend acceptance harness`
- `e02b8bf` — `docs: record managed sidecar lifecycle`
- `898e6df` — `test: add packaged bilingual acceptance harness`
- `71b4218` — `docs: document installer and signing path`
- `d470eb7` — `feat: add local prerequisite checker`
- `5f92ee2` — `docs: update V1 distribution readiness review`
- final record commit — `docs: freeze V1 distribution candidate record`

Meetily:

- `f9d4aa7` — `feat: pass local capability token to intelligence backend`
- `499cb87` — `feat: add bounded restart backoff for intelligence backend`
- `6f90f71` — `feat: manage intelligence backend sidecar lifecycle`
- `a81c39d` — `feat: add unsigned Windows installer build`
- `b3b6b9c` — `fix: keep update checks user initiated`

## Meetily patch backups

- `<legacy-patch-backup>\0001-fix-harden-Meetily-distribution-runtime-f05b2ca.patch`
- `<legacy-patch-backup>\meetily-capability-token-f9d4aa7.patch`
- `<legacy-patch-backup>\meetily-restart-backoff-499cb87.patch`
- `<legacy-patch-backup>\meetily-managed-sidecar-6f90f71.patch`
- `<legacy-patch-backup>\meetily-unsigned-installer-a81c39d.patch`
  - SHA-256 `D66F61E2FD72981BACB3EF311C61A5B39703D95548AF03F0A2A6CDF7505A1FA4`
- `<legacy-patch-backup>\meetily-user-initiated-updater-b3b6b9c.patch`
  - SHA-256 `B68ED16B4532CF7532A5900820134D1C1E703CB694B86F37291E144D93B58BA2`

Earlier distribution setup/lifecycle backups remain recorded in `docs/PACKAGING_PLAN.md`.

## Verification passed

Final product gate:

- ruff/import-boundary checks;
- 214 API tests;
- contract schema sync;
- 13/13 deterministic bilingual goldens;
- offline/frozen pnpm installation;
- 21 web tests;
- TypeScript; and
- Vite production build.

Meetily distribution evidence:

- locked Rust check;
- 13 Node tests after the explicit-updater regression was added;
- TypeScript and Next production builds;
- prior full managed-lifecycle suite: 201 runnable Rust tests passed, 4 real-artifact cases ignored by
  default and run explicitly;
- real backend start/stop and forced crash/restart with a new healthy PID;
- NSIS and MSI Tauri production bundling with updater artifacts disabled only for the unsigned
  flavor; and
- no new updater `.sig` files and expected `NotSigned` Authenticode state.

Security/privacy evidence:

- capability token required for sensitive HTTP and WebSocket transport, absent from URLs/logs/UI;
- loopback-only backend, validated local origins, minimal public health;
- bounded restart and no stale ASK NOW during outage;
- managed child output discarded; acceptance logs bounded/redacted/cleaned;
- current product Python and pnpm audits report no known vulnerabilities;
- Meetily has zero moderate/high/critical pnpm findings and one documented no-fix low Babel finding;
- no tracked private-key markers in either repository;
- candidate artifacts and real Tauri sidecar inputs are ignored;
- both installer artifacts passed local Defender custom scans; and
- automatic startup updater egress removed; remote metadata is fetched only after explicit user
  action.

Known non-fatal warnings are the Starlette/httpx deprecation, Node module-type reparsing, Next's
missing-ESLint notice despite separate type/test/build gates, and Cargo workspace patch/profile
warnings.

## Packaged bilingual acceptance status

The real packaged backend passed controlled authenticated EN and JA trajectories through loopback
HTTP/WebSocket transport:

- English manual-work: expected pain/frequency, stale business-impact ASK retraction, volume
  promotion, and evidence/history;
- Japanese owner-answer: `source_of_truth = 倉庫側`, business impact, owner ASK, no self-answer from
  the question, explicit owner answer, stale owner ASK retraction, and affected-scope promotion;
- one ASK NOW and at most two follow-ups;
- traceable evidence IDs and no mojibake;
- token/transcript-marker redaction, one-megabyte acceptance log ceiling, cleanup, and listener/process
  shutdown; and
- no cloud, Ollama, RAG, model, audio, or GUI dependency in that automated harness.

This does not claim installed GUI, local STT, routed audio, or human-speech acceptance. Those are the
next human gate.

## Exact commands

### Verify prerequisites

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check-local-prereqs.ps1 `
  -MeetilyPath $env:MEETILY_ROOT `
  -LibClangPath <path-to-LLVM-bin> `
  -RequirePreparedSidecars
```

Replace the measured LLVM path on another machine.

### Verify product and packaged backend

```powershell
cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
powershell -ExecutionPolicy Bypass -File scripts\test-backend-sidecar.ps1
powershell -ExecutionPolicy Bypass -File scripts\run-packaged-acceptance.ps1
```

### Rebuild unsigned installers

```powershell
cd $env:MEETILY_ROOT
$env:RUSTUP_TOOLCHAIN = "stable"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
powershell -ExecutionPolicy Bypass -File `
  frontend\src-tauri\scripts\build-unsigned-windows.ps1 -Bundle both
```

Every rebuild produces new candidate hashes unless proven otherwise; update the record and scan
again. The first build may need prepared Tauri NSIS/WiX caches.

### Verify and open the NSIS candidate for controlled human review

```powershell
$installer = "$env:MEETILY_ROOT\target\release\bundle\nsis\meetily_0.4.0_x64-setup.exe"
Get-FileHash -Algorithm SHA256 -LiteralPath $installer
Get-AuthenticodeSignature -LiteralPath $installer
Start-MpScan -ScanType CustomScan -ScanPath $installer
Start-Process -FilePath $installer -Wait
```

The expected pre-signing hash is
`F30109D5BE36E3DFB85276F81F5A01611E480DAE5EC39A937A34F6078D829E29` and the expected signature
state is `NotSigned`. Run `Start-Process` only on the designated clean review account. Observe rather
than bypass SmartScreen/UAC policy, and do not pretend this command was executed during the freeze.

### Manual installed JA/EN acceptance

Follow `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` exactly:

- English Parakeet `parakeet-tdt-0.6b-v3-int8`, `synthetic_manual_work.wav`, VB-Cable;
- Japanese Whisper `small`, VOICEVOX six-beat inventory script, VB-Cable;
- expected ASK NOW/fact/stale-retraction behavior;
- forced backend crash, occupied port, missing/incompatible sidecar, restart/replay, and no recording
  interruption; and
- upgrade/rollback/uninstall plus chosen user-data retention behavior.

## Rollback

- Do not move or rewrite the frozen tags.
- For source rollback, create a new clean worktree from the runtime-hardened or local-demo tags; do
  not use `git reset --hard` and never use the damaged Meetily tree.
- For development/runtime fallback, stop managed launch and use the documented manual loopback
  backend plus clean Meetily development command.
- For an installed human-review candidate, use its registered Windows uninstaller after confirming
  that no recording or owned backend process is active. Preserve user meeting/model data until the
  retention decision is explicit.
- If signing or installed acceptance changes any binary, treat it as a new candidate: rehash,
  rescan, retest, document, and create new tags rather than mutating this record.

## Readiness verdict and blockers

Ready now:

- deterministic local question copilot and bilingual engine behavior;
- real Meetily integration, opaque sessions, active-recording replay;
- local capability auth, loopback transport, bounded restart/manual retry;
- ignored packaged backend and controlled bilingual packaged transport acceptance;
- real unsigned NSIS/MSI construction with all sidecars;
- reproducible commands, prerequisite inspection, artifact hashes, patch backups, and local tags.

Not ready:

- end-user/public distribution;
- signed application/sidecars/installers/updater artifacts;
- clean-account first install/launch, upgrade/rollback/uninstall evidence;
- installed GUI/routed-audio/human-speech acceptance;
- fresh-machine proof;
- bounded/rotated persistent operational diagnostics;
- hash-locked Python runtime dependencies;
- narrowing inherited broad Tauri filesystem permissions; and
- resolution/acceptance of documented upstream low/no-fix and maintenance items.

The exact next human action is to verify the NSIS hash on a clean Windows review account, run the
interactive install while observing SmartScreen/UAC, and execute the packaged EN/JA checklist. Real
signing follows only after that behavior is accepted and protected signing credentials are
available.

No push was performed. No cloud AI, RAG, or broad semantic runtime was enabled. No generated binary,
credential, token, certificate, model, transcript, or installer was committed.
