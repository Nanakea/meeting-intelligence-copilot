# V1 Local Demo Milestone

Status: **frozen for local developer/demo use; not ready for end-user distribution**.

## Frozen product reference

- Repository: `$env:ASSISTANT_ROOT`
- Product commit: `2eecf1ae2b13cbaf68dc4776c267e4274ef305e8`
- Local tag: `meeting-intelligence-v1-local-demo`
- The tag points to the verified product commit above. This milestone note is committed afterward and
  is intentionally not included in the tagged tree.

## Verification evidence

The frozen product commit passed the Windows offline/frozen verification entrypoint:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

The gate covers and passed:

- 186 API tests;
- schema synchronization;
- 13/13 deterministic golden scenarios across all six pain templates;
- 21 web tests;
- TypeScript checking;
- Vite production build; and
- offline `pnpm install --frozen-lockfile` behavior.

## What is ready

- Local Windows developer/demo operation of the question copilot.
- Real Meetily transcript ingress over loopback HTTP and versioned full-state WebSocket snapshots.
- Deterministic evidence-linked pains, facts, gaps, and one ASK NOW plus at most two FOLLOW UP
  questions.
- Active-recording recovery through ordered transcript-history replay after a backend restart.
- Opaque per-recording session identity and bilingual Japanese/English panel behavior.
- Real local Parakeet and Whisper acceptance using routed synthetic audio.
- Bilingual deterministic coverage for all six registered pain templates.
- A safe-off, evaluation-only semantic candidate boundary; normal runtime remains deterministic.
- Reproducible offline/frozen product verification from the prepared development environment.

## What is not ready

- An end-user installer or signed, supported distribution.
- A built, measured, and bundled product backend executable.
- A clean full Meetily Rust/Tauri build with the real `llama-helper` external binary.
- Versioned sidecar compatibility negotiation, lifecycle ownership, bounded restart, signing,
  upgrade/rollback, and uninstall cleanup.
- Durable meeting state, production retention limits, or a packaged local capability-token flow.
- Broad live-model semantic extraction; the semantic provider remains disabled.
- Diverse live human-speech validation or packaged end-to-end JA/EN acceptance.

## Required Meetily references

- Current panel/demo worktree: `<legacy-meetily-worktree>-panel-ux` at
  `ba06098ae2b9d249288aa75f61c2336cdcae2a1c`, branch `spike/intelligence-panel-ux`.
- Local-only base worktree: `<legacy-meetily-worktree>-local-only` at
  `5534dc84821b1b50e0b7106e2f3a6d15ffedea1c`, branch `spike/local-only`.
- Clean packaging worktree: `<legacy-meetily-worktree>-packaging` at
  `5534dc84821b1b50e0b7106e2f3a6d15ffedea1c`, branch `spike/packaging-startup`.
- Durable original integration commit: `09cf3b7f900d7365202bf7ed1e5ec2297f5adf25`, branch
  `spike/real-transcript-ingress`, based on pinned upstream parent
  `0281737d87d26352fb0adc78c8c0975f691b23d1`.

The original `<legacy-meetily-worktree>` working tree contains known unrelated deletions from the earlier
damaged-tree incident. Those deletions are not part of this milestone. Do not restore or commit
them; use one of the clean worktrees above for future Meetily work.

## Key documents and artifacts

- `docs/V1_READINESS_REVIEW.md` — readiness verdict, risk review, and release blockers.
- `docs/PROJECT_STATUS.md` — current implementation and gate status.
- `docs/MEETILY_V1_RUNBOOK.md` — reproducible local startup and bilingual acceptance procedure.
- `docs/PACKAGING_PLAN.md` — launcher, sidecar prototype, and packaging sequence.
- `docs/REALISTIC_SCENARIOS.md` — deterministic scenario coverage.
- `docs/SEMANTIC_EVAL_REPORT.md` — evidence for keeping semantic extraction disabled.
- `docs/LOCAL_ONLY_PRIVACY.md` — local-only guarantees and caveats.
- Meetily integration patch backup:
  `<legacy-patch-backup>\meetily-integration-09cf3b7.patch`.

## Launch blockers

1. Build the real Meetily `llama-helper-x86_64-pc-windows-msvc.exe` in a clean worktree and rerun
   the pinned Rust/Tauri checks without placeholders.
2. Install a reviewed, pinned PyInstaller toolchain; build the ignored backend artifact; then
   measure and smoke-test size, startup, native imports, and Windows Defender behavior.
3. Add versioned sidecar health compatibility, Rust lifecycle management, bounded restart,
   operational logging, signing/hash verification, installer upgrade/rollback, and uninstall
   cleanup.
4. Run packaged end-to-end Japanese/English acceptance and a small consented human-speech pilot,
   while preserving recording when the intelligence backend is absent or crashes.

## Recommended next work order

1. Resolve the real `llama-helper` build blocker in `<legacy-meetily-worktree>-packaging` or a new clean
   worktree based on the accepted Meetily commit.
2. Produce and smoke-test the ignored backend executable, choosing one-file versus one-folder from
   measured evidence.
3. Implement the versioned sidecar health/lifecycle contract, including cleanup and replay-gap
   observability.
4. Complete packaged JA/EN and consented human-speech acceptance, then address only the highest
   observed deterministic coverage gaps.
5. Keep semantic extraction evaluation-only until live local evidence demonstrates sufficient
   precision, provenance, evidence linking, slot mapping, and negative-guard safety. Do not add RAG
   or cloud AI.

## Post-freeze distribution update (not part of the tag)

The frozen tag and its original 186-test evidence above are unchanged. Subsequent distribution work
reduced several launch blockers without changing the milestone verdict:

- Product commits `c722c43` and `1276496` build and validate a real ignored PyInstaller candidate
  and add the separate versioned compatibility handshake.
- Product commits `e13472f` and `677b18a` record the clean lifecycle prototype and add the executable
  acceptance harness/checklist. The current offline gate passes 196 API tests, schema sync, 13/13
  goldens, 21 web tests, TypeScript, and Vite production build.
- Clean Meetily commit `b8b99d9` builds and protocol-smokes the real `llama-helper`; commit `c1e0299`
  adds the target-specific backend `externalBin`, exact compatibility consumption, opt-in hidden
  startup, and owned Windows process-tree shutdown. Commit `f05b2ca` adds dependency/runtime
  hardening, ended-session cleanup, secure note rendering, and full native/frontend verification.
- Patch backups are
  `<legacy-patch-backup>\meetily-distribution-b8b99d9.patch` and
  `<legacy-patch-backup>\meetily-distribution-c1e0299.patch`, plus
  `<legacy-patch-backup>\0001-fix-harden-Meetily-distribution-runtime-f05b2ca.patch`.
- The backend artifact harness proves occupied-port rejection, loopback-only health, HTTP ingest,
  native WebSocket v0→v1, transcript-free operational logs, and listener cleanup.

The remaining blockers are bounded restart/backoff and operational logging, final signing and
installer upgrade/rollback/uninstall validation, and packaged GUI plus consented JA/EN human-speech
acceptance. Therefore local developer/demo use remains **ready**, while end-user distribution
remains **not ready**. The original damaged `<legacy-meetily-worktree>` tree was not used or modified.
