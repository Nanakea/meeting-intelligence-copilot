# V1 Hardened Distribution Candidate

> **Frozen API v2 candidate:** this record preserves historical evidence only. Its executable,
> hashes, Defender result, and installer checks do not establish current API v8 readiness. Use
> `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for the current gate.
>
Freeze date: 2026-07-14.

Verdict: **the unsigned Windows candidate is locally hardened and suitable for controlled developer
and human-review use. End-user distribution is not ready.** All locally actionable blockers from
the original distribution review are complete. The remaining gates require protected signing
credentials or independent human/clean-machine GUI and routed-audio evidence.

This is a successor to `docs/V1_DISTRIBUTION_CANDIDATE.md` and
`docs/V1_DISTRIBUTION_READINESS_REVIEW.md`. Those records and their original local tags remain
unchanged historical evidence.

## Frozen source inputs

| Repository | Path | Branch | Commit |
|---|---|---|---|
| Product source used for the backend | `$env:ASSISTANT_ROOT` | current product branch | `4d7afad3c0bd902eb1bdfba9b96173089c522d4b` |
| Clean Meetily distribution source | `$env:MEETILY_ROOT` | `spike/distribution-readiness` | `58f26bbbf8827d3bba427b10749ebe25ec9a6d4e` |

The successor local tags are:

- product: `meeting-intelligence-v1-distribution-hardened` (the documentation freeze commit that
  contains this record);
- Meetily: `meeting-intelligence-meetily-distribution-hardened` at `58f26bbbf8827d3bba427b10749ebe25ec9a6d4e`.

The damaged `<legacy-meetily-worktree>` tree was not inspected, repaired, staged, or modified. Its unrelated
pre-existing deletions remain intentionally ignored. Future Meetily work must use the clean
distribution worktree or another clean clone/worktree based on `58f26bb`.

## Exact unsigned artifacts

All generated binaries are ignored and uncommitted.

| Artifact | Bytes | SHA-256 | Authenticode | Defender |
|---|---:|---|---|---|
| Backend EXE | 15,540,634 | `FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48` | `NotSigned` | zero detections |
| NSIS setup | 58,741,887 | `1A9356623521803DB7F1C22A1039B44EF447B9B1FD9307E7B3957744C21D2C59` | `NotSigned` | zero detections |
| MSI | 85,483,520 | `2B2CD35FA5102CBD29DC2FA4913B8259C55C16513247E461C0E85EFD760230B7` | `NotSigned` | zero detections |

Defender was enabled with real-time protection and signature version `1.455.125.0`. Detections were
zero before and after scanning all three final committed-source artifacts. This is point-in-time
evidence, not a guarantee about another endpoint's policy.

Additional bundled-sidecar evidence:

- `llama-helper.exe`: `B206494A702A5D93395D4F9AB4AF9352CEDA1175BE1F330CA9D17F4BC83DB1D7`;
- FFmpeg: `5AF82A0D4FE2B9EAE211B967332EA97EDFC51C6B328CA35B827E73EAC560DC0D`.

## Verification passed

Product gate:

- `scripts/check.ps1` passed with 216 API tests, schema sync, 13/13 deterministic bilingual
  goldens, 21 web tests, TypeScript, Vite production build, and offline/frozen pnpm installation;
- a new CPython 3.12 environment installed 24 exact runtime/development packages only from an
  approved local wheelhouse with `--no-index --require-hashes`;
- the isolated environment passed `pip check`, ruff, 216 API tests, schema sync, and 13/13 goldens,
  then was removed;
- product Python and pnpm audits reported no known vulnerabilities.

Meetily gate at `58f26bb`:

- offline/frozen pnpm installation, 13 Node tests, TypeScript, and Next production build passed;
- the full pnpm audit reported no known vulnerabilities;
- `cargo check --locked --offline` passed;
- 202 Rust unit tests passed, 4 ignored by default, and the doc test passed;
- the two ignored real-artifact tests passed explicitly: owned process-tree start/stop and forced
  crash/restart with a new healthy PID;
- the Windows SQLite executable graph contains no `rsa` dependency path;
- tracked private-key marker scans were clean in both repositories.

The known Starlette/httpx deprecation and inherited compiler/build warnings remain non-fatal and
are not hidden.

## Hardening completed since the original candidate

- Python 3.12 runtime/development dependencies are exact-version and wheel-hash locked; bootstrap
  supports an offline wheelhouse and does not perform an unpinned pip upgrade.
- The backend packaging script verifies runtime-lock and packaging-lock version consistency before
  building.
- Meetily writes only fixed, privacy-safe lifecycle event codes to two bounded operational-log files
  (current plus backup), each capped at 256 KiB. Tokens, transcript text, raw backend errors, and
  development paths are not logged.
- Unused broad Tauri webview filesystem permissions and direct filesystem dependencies were removed
  after source inventory confirmed no JavaScript consumer. Rust-owned meeting/model workflows remain.
- The unused Remirror direct-dependency set and its sole low Babel advisory were removed.
- The unused CSP connection to `https://api.ollama.ai` was removed.
- NSIS lifecycle automation now proves first install, same-format upgrade, rollback, re-upgrade, and
  uninstall while validating installed payload hashes and preserving pre-existing application-data
  root existence.
- MSI administrative extraction proves the expected payload and an exact backend-sidecar hash match.
- The committed-source backend passed authenticated loopback health/compatibility, HTTP/WebSocket,
  occupied-port, redaction, process cleanup, and controlled bilingual EN/JA acceptance.

## Installer lifecycle evidence

The archived original NSIS baseline used for upgrade/rollback testing is:

- path: `<artifact-root>\meetily_0.4.0_baseline_b3b6b9c_x64-setup.exe`;
- SHA-256: `F30109D5BE36E3DFB85276F81F5A01611E480DAE5EC39A937A34F6078D829E29`.

The final NSIS candidate passed baseline install, candidate upgrade, baseline rollback, candidate
re-upgrade, and uninstall. The installation directory was removed and pre-existing Roaming/Local
`com.meetily.ai` data-root existence was preserved. The installed candidate application hash was
`D08310E1E7F2B42129425401091B2176A2675DE9BC7D02FE607E8F89B410BA98`.

The final MSI administrative extraction contained 12 payload files and the backend payload exactly
matched `FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48`.

No candidate remains installed after the automated lifecycle gate.

## Remaining irreducible launch blockers

1. Sign the final application, every executable/sidecar, NSIS/MSI installer, and Tauri updater
   artifacts using protected publisher credentials. Then rehash, rescan, and test Authenticode,
   updater signatures, SmartScreen/UAC, update, and rollback behavior. No signing credential is
   available in this workspace.
2. On an independent clean Windows account or machine, install and launch the exact candidate and
   complete packaged GUI/audio acceptance with Parakeet
   `parakeet-tdt-0.6b-v3-int8`, Whisper `small`, VB-Cable, the reviewed English fixture, the
   VOICEVOX Japanese six-beat script, and consented JA/EN human speech.
3. Record the final human decision for application-data/model retention after uninstall and verify
   it against the signed build.

The current machine has a pre-existing Meetily process from the damaged tree. It was deliberately
left untouched, and Tauri single-instance behavior means it prevents trustworthy GUI-launch evidence
for the clean candidate in this session. It does not invalidate the installer lifecycle or extracted
payload checks.

## Release boundary

Do not publish or push this candidate, call it end-user ready, enable automatic remote update checks,
or enable cloud AI, RAG, or the semantic analyzer runtime. Every rebuilt or signed derivative is a
new candidate and requires new hashes, scans, lifecycle checks, documentation, and successor tags.
