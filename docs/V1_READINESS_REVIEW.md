# V1 Readiness Review

> **Historical readiness review:** packaged executable evidence in this document predates the
> current API v8 live transport. It is not current release approval; use
> `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for the API v8 acceptance boundary.
>
Review baseline: product commit `84b9cf8` before readiness fixes; clean Meetily UX commit
`ba06098ae2b9d249288aa75f61c2336cdcae2a1c`. The original `<legacy-meetily-worktree>` damaged tree was not
used. Verdict: **developer-preview and local acceptance ready; not yet end-user distributable**.

Runtime-hardened baselines are frozen by local tags
`meeting-intelligence-v1-runtime-hardened` (`c2e029d6c028251fc0784ff77b1d7bf472d5e47b`)
and `meeting-intelligence-meetily-runtime-hardened`
(`f05b2ca9238f33f957f5bb77089920f05eb387c5`).

## Executive assessment

The product identity, deterministic question loop, Meetily live ingress, active-recording recovery,
opaque session identity, bilingual panel, and executable test moat are credible V1 assets. The normal
runtime remains a local question copilot: transcript evidence folds into pains/facts/gaps, the reducer
and selector own state, one ASK NOW is shown, and answered questions retract.

No open Critical issue was found. One High issue was found and fixed during this review: the normal
verification script could bootstrap/install packages and run unconstrained `pnpm install`, contrary
to the required offline gate. Dependency setup now lives in explicit `scripts/bootstrap-dev.ps1`;
`scripts/check.ps1` performs no pip install and uses `pnpm install --offline --frozen-lockfile`.

The release blocker is packaging, not the intelligence loop. Post-freeze distribution work built and
smoke-tested both the real Meetily `llama-helper` and an ignored product backend executable. Clean
Meetily commit `f05b2ca` now consumes the compatibility endpoint, proves opt-in startup plus owned
process-tree shutdown against the real artifact, cleans up ended in-memory sessions, and carries the
reviewed dependency/runtime hardening. The Meetily pilot client and product API v3 add a per-launch
256-bit capability token across compatibility, ingest, cleanup, replay, and WebSocket transport.
Meetily commit `499cb87` adds three bounded crash restarts at 1/2/4 seconds, a two-health-failure
threshold, a 60-second stable reset, privacy-safe status, and manual retry after exhaustion. The
follow-up Meetily commit `6f90f71` distinguishes missing executable, incompatible backend, startup
  failure, and stopped/unhealthy backend with fixed reason codes and actionable JA/EN copy while
  keeping paths and exception text out of the webview. Meetily commits `a81c39d` and `b3b6b9c` add a
  separate unsigned installer flavor and keep remote update checks user initiated. The hardened
  successor at Meetily `58f26bb` adds bounded privacy-safe persistent diagnostics, narrows unused
  webview filesystem authority, removes the low Babel advisory, and proves unsigned NSIS lifecycle
  plus MSI payload extraction. Signed and packaged-GUI/audio evidence remain open.

## Readiness by area

| Area | Assessment | Evidence / remaining work |
|---|---|---|
| 1. Product identity | **Ready** | Specs, domain rules, UI hierarchy, and tests consistently implement a live question copilot rather than a summary feature. |
| 2. Architecture | **Ready with bounded debt** | Pure domain/services boundaries are mechanically tested; reducer/selector remain authoritative. Multi-pain ranking and durable storage remain deferred. |
| 3. Privacy / local-only | **Ready with caveats** | Backend, Rust bridge, panel, and optional Ollama adapter validate loopback destinations; transcript/token logs are redacted. Sensitive HTTP and WebSocket routes require an in-memory capability token in managed/packaged flows. Separate Meetily providers/telemetry must remain disabled for a fully offline session. |
| 4. Meetily integration | **Functionally ready** | Real Parakeet/Whisper ingress, HTTP push, WebSocket snapshots, and panel render were proven. Full native packaging is blocked independently. |
| 5. Recovery behavior | **Ready for active recording** | Reconnect clears stale UI and ordered transcript-history replay rebuilds state. Recovery is not durable after recording/app shutdown. |
| 6. Session identity | **Ready** | Opaque per-recording `meeting-intel-<UUID>` identity removes title/path collision and is shared across push, registry, and panel. |
| 7. Bilingual support | **Ready but bounded** | Real prior JA/EN STT acceptance, 13 deterministic goldens, and current packaged-executable EN/JA controlled transcript trajectories pass; all six templates have executable scenarios. Rule coverage remains language/template-specific, packaged GUI/audio was not rerun, and mixed code-switching is provisional. |
| 8. Semantic analyzer | **Correctly held back** | Candidate validation, offline eval, and mocked loopback adapter are green. Baseline is 5/18 exact deterministic candidates; no live model quality run exists. Recommendation remains do not enable. |
| 9. Packaging | **Hardened unsigned candidate / human review required** | Real `llama-helper`, backend candidate, compatibility handshake, managed lifecycle, bounded restart/manual retry, two-file bounded operational logs, NSIS install/upgrade/rollback/re-upgrade/uninstall, and MSI payload extraction pass. Signing and independent clean-machine packaged GUI/audio acceptance remain open. |
| 10. UI usability | **Ready for local preview** | ASK NOW dominance, at-most-two follow-ups, bilingual connection/recovery states, local-only cue, wrapping, and small-window behavior are tested in clean Meetily UX worktree. |
| 11. Tests and goldens | **Ready** | Offline Windows gate: 216 API tests, schema sync, 13/13 goldens, 21 web tests, TypeScript, and Vite production build. Distribution Meetily: 13 Node tests, full TypeScript/Next builds, locked/offline Rust checks, 202 Rust unit tests with 4 ignored by default, one doc test, explicitly run real authenticated start/stop and crash/restart tests, and dual-format Tauri bundling. One upstream Starlette/httpx deprecation warning remains. |

## Post-freeze distribution evidence

| Distribution concern | Current classification |
|---|---|
| Real Meetily `llama-helper` | **Resolved for local Windows validation** — built from repository source, protocol-smoked, and kept ignored. |
| Backend executable | **Candidate proven** — ignored 15,540,634-byte PyInstaller 6.21.0 one-file artifact (`FA8C7E4BE262CEF897DD747CB28E1C84D5B2F7BB8F2A234286CDB0D3C36E0C48`) is built from hash-locked inputs and passes authenticated health, missing/invalid-token rejection, HTTP, WebSocket, loopback, bounded log/redaction, shutdown, occupied-port rejection, bilingual acceptance, and zero-detection Defender checks. It remains unsigned; cross-machine reproducibility is not claimed. |
| Tauri `externalBin` and lifecycle | **Bounded lifecycle proven** — clean Meetily commit `58f26bb` includes in-memory token handling, exact authenticated compatibility, opt-in startup, owned process-tree cleanup, three crash retries at 1/2/4 seconds, manual retry, actionable privacy-safe status, and fixed event codes in two operational files capped at 256 KiB each. |
| Signing and installer | **Unsigned lifecycle proven / release blocking** — NSIS (58,741,887 bytes, `1A9356623521803DB7F1C22A1039B44EF447B9B1FD9307E7B3957744C21D2C59`) and MSI (85,483,520 bytes, `2B2CD35FA5102CBD29DC2FA4913B8259C55C16513247E461C0E85EFD760230B7`) contain all real sidecars, report `NotSigned`, and pass Defender. NSIS install/upgrade/rollback/re-upgrade/uninstall and MSI extraction/hash checks pass; signed-payload evidence does not exist. |
| Packaged bilingual acceptance | **Partial** — the real executable passes controlled authenticated EN manual-work and JA owner-answer trajectories, including stale retraction, `倉庫側`, business impact, owner behavior, evidence caps, no mojibake, redacted bounded logs, and cleanup. Packaged Meetily GUI, routed Parakeet/Whisper audio, and consented human speech remain open. |
| Local-only packaged mode | **Preserved in automated evidence** — the executable binds only `127.0.0.1`; Rust URL validation, per-launch capability token, and best-effort recording independence remain covered. `/health` alone stays public and minimal. |
| Offline/reproducible checks | **Product gate passes offline; first native bundle needs prepared caches** — frozen pnpm install and all product checks pass. Initial native setup required cached/downloaded Cargo crates and repository-verified FFmpeg. The first NSIS/MSI builds downloaded Tauri's hash-validated NSIS 3.11 and WiX 3.14.1 tools; subsequent builds used the prepared caches. |
| Version coordination | **Prototype consumed** — `/health` remains minimal and authenticated `/health/compatibility` API v3 is required by the pilot client. |
| Dependency security | **Current executable paths clear** — product Python/pnpm and full Meetily pnpm audits report no known vulnerabilities. The unused direct Remirror packages that pulled the low Babel advisory were removed. The locked Windows SQLite dependency graph contains no RSA crate path. Tracked private-key scans are clean. |
| Recovery artifacts | **Preserved** — work was performed in `$env:MEETILY_ROOT`; commit `6f90f71` has patch backup `<legacy-patch-backup>\meetily-managed-sidecar-6f90f71.patch`. The damaged tree was not used. |

## Architectural and behavioral guarantees reviewed

- Every deterministic pain/fact remains evidence-linked; inference provenance is separate.
- Candidate analyzers cannot mutate `MeetingState`, gaps, lifecycle status, or ASK NOW.
- `MeetingEngine` remains deterministic and provider-free in normal composition.
- Template slot order remains the priority source; one ASK NOW and at most two FOLLOW UP questions
  are asserted across every registered demo snapshot.
- Active facts close slots, answered gaps are not suggested, and historical superseded facts remain.
- Live transcript ordering uses Meetily sequence IDs and a per-session buffer; language comes only
  from real ingest, not a WebSocket subscriber or Unicode inference.
- Backend/semantic URLs reject non-loopback hosts. Browser WebSocket origins are allowlisted.

## Known limitations and risks

| Severity | Limitation / risk | Disposition |
|---|---|---|
| Low | Capability auth is not an OS sandbox against a malicious same-user process that can inspect process memory/environment. | Keep tokens ephemeral and redacted; rely on OS account isolation and endpoint security for that stronger threat model. |
| Medium | Registry and session state are in memory and not durable. Ended Meetily recordings now request explicit cleanup; abnormal termination can still leave state until backend exit. | Add retention expiry before broad deployment. |
| Medium | A permanently missing sequence can hold at most 1,024 later events until history replay supplies the gap. | Add observability and a recovery/replay trigger; never silently reorder. |
| Medium | Deterministic extraction is intentionally grammar-bounded and uneven by language/template. | Expand only from observed failures; keep semantic runtime off pending precision evidence. |
| Medium | Real acceptance used synthetic TTS routed through real STT, not diverse live human speech. | Run consented human pilot sessions in JA/EN without committing transcripts. |
| Low | Starlette warns that the current TestClient/httpx integration is deprecated. | Upgrade deliberately with the full gate; do not suppress the warning. |
| Low | Older title-keyed clients do not share the opaque-session guarantee. | Treat them as incompatible with the current V1 integration. |
| Medium | Unsigned installers can be blocked or strongly warned by SmartScreen, Smart App Control, or enterprise policy. | Keep the candidate local, verify hashes, and perform protected real signing plus final scan before any end-user publication. |
| Low | The updater endpoint is remote, though automatic mount-time checks are disabled. | Keep update checks explicit and user initiated; never send meeting content or tokens, and publish only signed update metadata. |

## Launch blockers

1. Obtain protected credentials, sign the final candidate, and retest Authenticode, updater
   signatures, SmartScreen/UAC, update/rollback, Defender, and process-tree shutdown.
2. Human-test the exact candidate on an independent clean Windows account and run end-to-end
   packaged Meetily GUI/audio acceptance (Parakeet EN and Whisper small JA) plus a
   small consented human-speech pilot. Controlled packaged transcript acceptance already passes;
   recording must remain functional when the intelligence backend is absent or crashes.

## Recommended next priorities

1. Obtain protected signing credentials, sign every executable/installer/updater artifact, then
   rehash, rescan, and verify SmartScreen/UAC/update/rollback behavior.
2. Human install-test the exact candidate on an independent clean Windows account and run packaged
   GUI/audio plus consented speech acceptance; record the final data-retention decision.
3. Address only failures observed by that acceptance evidence.
4. Keep Ollama semantic extraction eval-only until a live local comparison proves high evidence-ID,
   provenance, slot, and negative-guard precision. Do not add RAG or cloud AI.

## Final V1 decision

Proceed with local developer/demo use and controlled installer review. The **unsigned local
distribution candidate is ready for human install/signing review**, but do **not** label it an
end-user packaged V1 and do not enable the semantic provider. The next release decision should be
made after the signing and independent human/clean-machine blockers above are closed with evidence.
