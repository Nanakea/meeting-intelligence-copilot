# Historical Project Status Through 0.6.1

> **Current release note (API v13 / 0.6.1 pilot line):** source adds review-gated
> documentation-to-system consistency over selected documents, repositories, APIs, pipelines,
> ERP metadata, and CMDB records. Every discrepancy retains both citations, source revisions,
> environment, freshness, and a deterministic explanation. Attribution is neutral until a reviewed
> authority policy applies. Optional local semantic extraction remains proposal-only. A rebuilt v13
> package and real tenant acceptance remain open. The v12 baseline added lease-gated enterprise
> evidence for selected GitHub, GitLab, Jira, Confluence, Azure DevOps, ServiceNow, approved SQL,
> SFTP, and OpenAPI sources through an optional mTLS/OIDC gateway. Source selection is encrypted,
> revocation purges indexed connector data, global evidence search is bilingual, and direct external
> writes fail closed in favor of reviewed exports. The v11 baseline included trusted adaptive ERP governance,
> a revision-aware document inbox with optional bounded local OCR, encrypted review-signal retention,
> deterministic proposal evaluation, signed portable improvement packs, shadow activation, and rollback.
> Selected Teams/Slack/Notion evidence, deterministic data-quality checks, export-only action drafts, and
> confirmed local document routing remain available from API v9. API v8 evidence/assurance remains
> the foundation; all remote records and messages are live-only and supplemental. API v11
> adds isolated train/holdout evaluation, immutable corpus hashes, measured three-cycle shadow
> promotion, synthetic SuiteTalk validation, explicit document scanning, OCR preflight, and
> two-step reviewed file operations. See `docs/TRUSTED_ERP_DOCUMENT_OPERATIONS_V11.md`.
>
> The prior API v8 release note included stable provisional-to-anchored
> problem promotion, an encrypted user-managed entity glossary, explicit local semantic-hint review,
> immutable structured issue drafts with reviewer overlays, and state-bound problem-context
> retrieval, safe citation projection, scoped Graph/ERP mappings, bounded Office/PDF indexing, and a
> confirmation-gated solution-governance workspace for decisions, RAID, dependencies, architecture
> impact, ownership, portfolio alerts, and confirmed-only two-hop impact analysis. API v8 adds
> encrypted revisioned document evidence, hybrid FTS5 retrieval, bounded in-memory Graph assurance,
> deterministic document and ERP-metadata checks, signed company rule packs, review-gated digital
> traceability, and per-user headless assurance scheduling. See
> `docs/ENTERPRISE_EVIDENCE_ASSURANCE.md` for the authority and privacy boundaries.
> The pass table and API v4 / 0.5.0 hashes below are historical evidence only. They are not proof
> that the current 0.6.1 release line is accepted. The earlier API v4 source gates were green at freeze; use
> `docs/INTERNAL_PILOT.md` and `docs/PACKAGED_ACCEPTANCE_CHECKLIST.md` for the remaining packaged and
> real-environment acceptance gates, and do not reuse the hashes or test counts below.
>
> Living status snapshot. For the full research trail and real-ingress evidence, see
> `docs/MEETILY_INTEGRATION_RESEARCH.md`. For the domain model and architecture, see
> `docs/DOMAIN_MODEL.md` / `docs/ARCHITECTURE.md`. The reproducible local acceptance procedure is
> frozen in `docs/MEETILY_V1_RUNBOOK.md`. Local-only privacy guarantees are in
> `docs/LOCAL_ONLY_PRIVACY.md`. The frozen local-demo release boundary is recorded in
> `docs/V1_LOCAL_DEMO_MILESTONE.md`. The current distribution classification and exact artifact
> evidence are in `docs/V1_DISTRIBUTION_HARDENED_CANDIDATE.md`.

## Current state

The Meeting Intelligence Copilot engine (Slices 0-2) is complete, tested, and now integrated as a
live intelligence panel inside the monorepo Windows desktop app via a real HTTP push + WebSocket
production adapter (not file-tap/CDP — those remain acceptance-test infrastructure only). The active
recording panel also recovers its intelligence context after a backend restart by replaying the desktop's
ordered transcript history. Explicit Japanese person/team ownership extraction is supported across
owner-bearing templates, including evidence-backed promotion after an owner answer. The Meetily
panel now has a clearer question-first hierarchy, bilingual operational states, a visible local-only
indicator, and bounded small-window behavior without changing engine semantics. Japanese integration
failure analysis now extracts explicit source system, target system, and failure point when the
utterance states a complete directional transfer that fails. Phase 4A now adds a provider-neutral,
observation-only semantic candidate schema, fail-closed structural validation, and a null provider.
Phase 4B adds an offline annotation validator and deterministic coverage report. API v8 composes the
loopback-only Ollama adapter only when the user enables the semantic beta. Its asynchronous hints are
validated against literal transcript spans and never alter state until explicit confirmation; the
deterministic engine and ASK NOW path never wait for it. Release enablement still requires the live
`qwen3:8b` corpus gate for precision, provenance, evidence linking, and negative-guard safety.

Connected-context, issue-draft, governance, assurance, supervised improvement, federated search,
and documentation consistency source is now API v13: the backend resolves the targeted problem
before retrieval, rejects stale frontend requests, ranks bounded Graph/ERP/local results
deterministically, and returns a safe public citation projection. External context remains outside
`MeetingState`, cannot fill a gap, and cannot become transcript evidence. Packaging, real tenant
acceptance, signing, Sandbox lifecycle, and supervised human validation remain open release gates.
NetSuite now has a dedicated SuiteTalk/SuiteQL path, selected Teams, Slack, and Notion sources are live-only
evidence sources. API v13 disables all direct external delivery, including previously configured
alerts and NetSuite review records are disabled; reviewed exports are the only handoff. NetSuite operational
records remain strictly non-writable.
API v13 also includes a vendor-neutral, GET-only WMS profile and deterministic NetSuite/WMS
descriptive analytics for prices, inventory, volume, fulfillment, and service levels. Remote rows
remain live-only; currencies are isolated; discrepancies require review; real WMS/NetSuite tenant
acceptance remains open.
Amazon FBA now has a fixed-origin, read-only SP-API adapter for inventory summaries and inbound
shipment/item records plus transient NetSuite → WMS → FBA reconciliation. The source gate is local
and synthetic only; company SP-API authorization, marketplace mapping, and non-production Seller
Central acceptance remain open.
Durable governance records require explicit confirmation, retain transcript provenance and revision
history in encrypted local SQLite/WAL storage, and remain separate from `MeetingState`. The desktop now
provides live and post-meeting candidate review, portfolio alerts, revisioned record changes, and
review-only JSON/RAID/dependency/ZIP exports without external submission credentials.
The Korean language extension adds explicit `ko` provenance, a fixed-seed 300-case deterministic
corpus across all ten problem templates, Korean question and governance wording, and six Korean
scenario families with goldens. The routed-audio matrix now defines 108 scenarios (36 per language),
and the platform-path matrix defines 24 runs including six Korean positive/no-pain paths. These are
synthetic acceptance definitions; routed Korean STT, packaging, natural speech, and human-language
acceptance remain open. Historical JA/EN packaged acceptance below is unchanged.

Phase 7 expanded the earlier deterministic scenario moat to 18 bilingual goldens, including English
manual volume/time, English integration transfer, process delay, and unclear ownership.
Post-freeze distribution work now proves a real ignored backend executable, exact compatibility
handshake, per-launch capability-token authentication, clean Meetily-owned process-tree lifecycle,
automated packaged transport/bilingual acceptance, bounded privacy-safe operational logs, and
unsigned NSIS/MSI candidates containing the three real sidecars. Automated NSIS first install,
upgrade, rollback, re-upgrade, uninstall and MSI payload extraction pass. This does not supply signed
payloads, independent clean-machine GUI/audio evidence, or packaged human-speech acceptance.
Clean Meetily commits `499cb87` and `6f90f71` additionally prove bounded sidecar restart/backoff,
privacy-safe actionable lifecycle reasons, manual retry, recording-independent failure handling,
  and stale-question clearing. Clean Meetily commits `a81c39d` and `b3b6b9c` add a fail-closed
  unsigned installer build flavor and make remote updater checks explicit/user initiated. Meetily
  commit `58f26bb` adds bounded privacy-safe lifecycle logs, narrows unused webview filesystem
  authority, removes the low Babel advisory, and automates the Windows installer lifecycle. Product
  commit `4d7afad` adds exact wheel-hash locked Python runtime/dev bootstrap. The unsigned artifacts
  are ready for controlled human review, not end-user distribution.

## Frozen integration references

- Runtime-hardened product tag: `meeting-intelligence-v1-runtime-hardened` at
  `c2e029d6c028251fc0784ff77b1d7bf472d5e47b`.
- Runtime-hardened Meetily tag: `meeting-intelligence-meetily-runtime-hardened` at
  `f05b2ca9238f33f957f5bb77089920f05eb387c5` in the clean distribution worktree.
- V1 local-demo product tag: `meeting-intelligence-v1-local-demo` at
  `2eecf1ae2b13cbaf68dc4776c267e4274ef305e8`. This tag is ready for local developer/demo use, not
  end-user distribution.
- Product baseline before local-only privacy hardening: `$env:ASSISTANT_ROOT`
  at `a21bcb4` (`docs: plan local semantic analyzer boundary`).
- Product baseline before the current runtime/security hardening pass: `dba7b5f`
  (`docs: update distribution readiness review`).
- Current runtime-hardened product commit: `c2e029d6c028251fc0784ff77b1d7bf472d5e47b`
  (`fix: harden local demo runtime`).
- Current hardened distribution product source commit:
  `4d7afad3c0bd902eb1bdfba9b96173089c522d4b` (`build: lock Python runtime dependencies`).
- Current product baseline before deterministic transfer-detail coverage:
  `$env:ASSISTANT_ROOT` at `7e5a6b1`
  (`docs: record IntelligencePanel UX hardening`).
- Integration milestone commit: `1810f99` (`feat: integrate live meeting intelligence with Meetily`).
- Meetily integration: `<legacy-meetily-worktree>` at `09cf3b7`, branch `spike/real-transcript-ingress`.
- Pinned upstream parent: `0281737d87d26352fb0adc78c8c0975f691b23d1`.
- Patch backup: `<legacy-patch-backup>\meetily-integration-09cf3b7.patch`.
- Meetily recovery worktree: `<legacy-meetily-worktree>-recovery` at
  `6045188e89ccc6bce61769b136f36b8071c914f8`, branch `spike/backend-recovery`.
- Recovery patch backup: `<legacy-patch-backup>\meetily-recovery-6045188.patch`.
- Meetily local-only worktree: `<legacy-meetily-worktree>-local-only` at `5534dc8`, branch `spike/local-only`.
- Meetily packaging worktree: `<legacy-meetily-worktree>-packaging` at `5534dc8`, branch
  `spike/packaging-startup`.
- Meetily panel UX worktree: `<legacy-meetily-worktree>-panel-ux` at `ba06098`, branch
  `spike/intelligence-panel-ux`.
- Meetily distribution worktree: `$env:MEETILY_ROOT` at
  `58f26bbbf8827d3bba427b10749ebe25ec9a6d4e`, branch
  `spike/distribution-readiness`; this contains `ba06098`, real-helper setup commit `b8b99d9`, the
  opt-in backend lifecycle prototype, dependency/runtime hardening, authenticated transport, and
  bounded restart supervision, actionable missing/incompatible/startup/stopped status, bounded
  privacy-safe operational logging, narrowed webview capabilities, unsigned NSIS/MSI lifecycle
  guards, and user-initiated updater behavior. Historical patch backups:
  `<legacy-patch-backup>\meetily-unsigned-installer-a81c39d.patch` and
  `<legacy-patch-backup>\meetily-user-initiated-updater-b3b6b9c.patch`.
- Panel UX patch backup: `<legacy-patch-backup>\meetily-panel-ux-ba06098.patch`.
- Live intelligence now uses one opaque `meeting-intel-<UUID>` session id per recording; meeting
  titles remain display metadata only.
- The Meetily working tree has known, unrelated damaged-tree deletions. They are intentionally not
  part of the integration; use the clean panel UX clone/worktree from `ba06098` for future Meetily
  changes. Current distribution source should start from `58f26bb`, not the damaged tree.

## Gates

| Gate | Status |
|---|---|
| REAL ENGLISH INGRESS | **PASS** — Parakeet, real audio, real UI render (see MEETILY_INTEGRATION_RESEARCH.md) |
| REAL JAPANESE INGRESS | **PASS** — Whisper `small` + VOICEVOX, real audio, real UI render |
| JA GOLDEN/EVALS | **PASS** — included in 18/18 deterministic fixtures, `scripts/eval_golden.py` |
| EN GOLDEN/EVALS | **PASS** — manual volume/time, integration transfer, process delay, ownership, negative small talk included in 18/18 |
| REALISTIC SCENARIOS | **PASS** — 13 deterministic goldens cover all six templates, JA/EN negative guards, mixed terms, explicit owner promotion, and directional transfer; semantic annotations link to registered demos |
| MEETILY ADAPTER | **PASS** — `MeetilyLiveNormalizer` + `LiveMeetingRegistry`, real HTTP push confirmed via server logs |
| INTELLIGENCE UI | **PASS** — real DOM render confirmed via CDP in both languages |
| INTELLIGENCE UI UX | **PASS** — question-first hierarchy, bilingual connection/recovery states, local-only badge, responsive width, 3/3 presentation tests, production Next build |
| JA INTEGRATION TRANSFER DETAILS | **PASS** — bounded directional grammar extracts explicit source/target/failure point, question and ambiguity guards, 3-checkpoint golden |
| BACKEND RESTART RECOVERY | **PASS** — ordered active-recording transcript history replay, localized reset/recovered UI, no stale ASK NOW |
| JA OWNER EXTRACTION | **PASS** — explicit Japanese person/team ownership, split-role value, negative question/action guards, evidence-backed promotion |
| LOCAL SEMANTIC ANALYZER | **API v8 OPT-IN BETA SOURCE PASS / RELEASE ENABLEMENT OPEN** — `qwen3:8b` is loopback-only, bounded, asynchronous, exact-span validated, and confirmation-gated. Default is off and the deterministic runtime remains authoritative. No live model corpus was run, so the beta must remain disabled for release acceptance until its quality thresholds pass. |
| LOCAL-ONLY PRIVACY HARDENING | **PASS** — strict loopback URL validation, local CORS/WS Origin allow-list, 256-bit in-memory capability token, authenticated sensitive HTTP/WS transport, opt-in diagnostic tap, and transcript/token log redaction |
| LOCAL V1 STARTUP LAUNCHER | **SOURCE UPDATED FOR API v13** — backend-first Windows launcher, loopback `/health` check, cryptographic token generation, authenticated handshake, temp operational logs, optional sidecar-gated Meetily launch; rebuild evidence is required |
| BACKEND SIDECAR PACKAGING | **V13 REBUILD REQUIRED** — all v12 and earlier artifact hashes are historical. The changed source requires a new provenance-bound sidecar, Defender scan, and acceptance run. Real tenant and signed clean-machine acceptance remain open. |
| PACKAGED BACKEND ACCEPTANCE | **OPEN FOR API V13** — prior API v12 and earlier packaged evidence cannot approve the modified source. Re-run authentication, compact WS, bilingual ingest, lease expiry/revocation, global search, two-sided consistency, disabled-write, cleanup, and process-tree gates. |
| PACKAGED BILINGUAL TRANSCRIPT ACCEPTANCE | **PASS / CONTROLLED INPUT** — real packaged executable passed authenticated EN manual-work and JA owner-answer trajectories, stale ASK NOW retraction, `source_of_truth = 倉庫側`, JA business impact/owner behavior, evidence/cap guards, no mojibake, session/process cleanup, and redacted bounded logs; this does not claim packaged STT or GUI evidence |
| WINDOWS INSTALLER | **V13 REBUILD REQUIRED** — prior NSIS/MSI artifacts are invalidated by source changes. Sandbox execution, signing, SmartScreen, real tenant, GUI/audio, and human gates remain open. |
| STALE QUESTION RETRACTION | **PASS** — gap identity is `pain_id::slot`, not text; answered gaps produce no suggestion |
| FACT SUPERSEDE TRACEABILITY | **PASS** — demonstrated live in both EN (`every morning`→`every business day`) and JA (`ほぼ毎朝`→`毎日`) runs |
| ENGINE FAILURE ISOLATION | **CANDIDATE / STOP GATE OPEN** — the bounded dispatcher keeps HTTP work off the transcription hot path and backend failures are contained, but stop must still prove that a full queue or stuck task cannot outlive its ten-second drain budget |
| TYPECHECK/TESTS/BUILD | **V13 / 0.6.1 SOURCE PASS / PACKAGE GATE OPEN** — `scripts/check.ps1` passes with 588 API tests passed, 1 environment skip, synchronized schemas, 300 Korean pilot cases meeting release thresholds, 24/24 goldens, and assistant web tests/typecheck/build. Meetily passes 148 frontend tests, TypeScript, production build, 305 Rust tests with 12 hardware/artifact-dependent ignores, and the Rust doc test. Scoped ESLint exits zero with 218 inherited upstream warnings and no errors. A v13 sidecar/installer rebuild, routed Korean audio, real-tenant acceptance, signing, and human validation remain open. |
| DEPENDENCY SECURITY | **PASS FOR CURRENT EXECUTABLE PATHS** — product Python/pnpm and full Meetily pnpm audits report no known vulnerabilities; the unused direct packages behind the low Babel advisory were removed; the Windows SQLite graph contains no RSA path; tracked private-key scans are clean; unused broad webview filesystem permissions were removed; generated candidates remain ignored |
| V1 READINESS | **HARDENED UNSIGNED LOCAL CANDIDATE / END-USER DISTRIBUTION BLOCKED** — all locally actionable hardening and unsigned lifecycle checks pass; only protected signing plus independent clean-machine GUI/audio/human evidence and final data-retention acceptance remain launch blockers |

## Architecture (current)

```
apps/desktop (Rust/Tauri, same monorepo commit)
  audio/transcription/worker.rs
    -> real TranscriptUpdate (Parakeet or Whisper)
    -> bounded ordered intelligence dispatcher (recording hot path does not await HTTP)
         POST http://127.0.0.1:8000/ingest/live/{session_id}
         retry + acknowledgement + bounded batch gap repair

apps/api (this repo, FastAPI, 127.0.0.1:8000 only)
  app/adapters/transcript/meetily_live.py   MeetilyLiveNormalizer (dedupes by sequence_id)
  app/adapters/transcript/live_registry.py  ordered registry + SQLite/WAL recovery
  app/api/main.py                           API v13 ingest/context/search/consistency/issues/governance/assurance/improvement + compact live WS
  app/gateway_main.py                       optional mTLS/OIDC enterprise connector gateway
  app/adapters/evidence/                    encrypted revisions + local hybrid retrieval
  app/adapters/assurance/                   rule packs, findings, schedules, digital thread
  app/adapters/governance/                  encrypted review-gated records + bounded impact graph
  app/domain/, app/services/                pure engine (unchanged architecture boundary)

Desktop frontend (Next.js)
  src/hooks/useIntelligencePanel.ts          WS client, bounded reconnect, ordered history replay
  src/components/IntelligencePanel/          ASK NOW dominant, ja/en/ko operational states, local-only cue
  src/app/page.tsx                           mounts the panel, gated on recording + explicit ja/en/ko
```

## Known, accepted limitations (non-blocking)

- Japanese owner extraction now supports explicit person/team ownership clauses for every template with
  an `owner` slot. Questions, vague future actions, and unresolved-owner statements are rejected;
  implicit or highly elliptical ownership language remains a limitation.
- `LiveMeetingRegistry` restores valid active state from a bounded SQLite/WAL cache after backend
  restart. Meetily's ordered transcript history is retained for gap repair when cache writes fail;
  normal stop purges transient state and crash remnants expire after 24 hours.
- The legacy meeting-title session-reuse collision is eliminated by the current opaque per-recording
  session id path, but older title-keyed clients are not compatible with that guarantee.
- Real end-to-end audio testing used synthetic TTS (Windows SAPI, then VOICEVOX neural TTS) via
  VB-Audio Virtual Cable, not a live human speaker.
- The optimized Meetily `pnpm run build`, full frontend `tsc --noEmit`, Node test suite, clean
  distribution `cargo check --locked`, and full Rust library suite now pass. The former `bun:test`
  portability issue was converted to the built-in Node test runner.
- The measured PyInstaller candidate is ignored and uncommitted. Its 1,184–1,297 ms measured cold
  readiness, missing and invalid token rejection, bounded/redacted acceptance logs, and zero-detection
  Defender scan pass. It is unsigned. Unsigned NSIS lifecycle and MSI extraction/hash checks pass.
  Clean Meetily commit `f05b2ca` proves explicit process-tree shutdown,
  listener disappearance, and ended-session cleanup; clean Meetily `f9d4aa7` adds authenticated
  transport and in-memory token lifecycle; clean Meetily `499cb87` adds three bounded automatic
  retries with 1/2/4-second backoff, manual retry after exhaustion, and a shutdown wake-up guard;
  clean Meetily `6f90f71` adds fixed privacy-safe failure reasons and actionable JA/EN guidance;
  `a81c39d` adds the scoped unsigned build flavor; `b3b6b9c` prevents automatic remote updater checks
  on normal startup; and `58f26bb` adds bounded operational diagnostics, capability narrowing,
  dependency cleanup, and automated installer lifecycle verification.
- Mixed JA/EN code-switching (e.g. a Japanese meeting with embedded English technical terms) is
  **PROVISIONAL**: a deterministic fixture (`integration-failure-mixed-terms-ja`) proves the analysis
  pipeline preserves Latin-script terms without corruption, and one real Japanese STT segment was
  observed to preserve "EC" as literal Latin text — but no dedicated multi-term real-audio test was
  run. Not classified NOT SUPPORTED, not classified PROVEN.
- Semantic candidates currently have no reducer adaptation or production runtime composition.
  Structural validity proves registry/evidence-reference shape only, not that transcript meaning
  supports a value; the Ollama adapter has not been run against a live model.

## Next planned spike

The product is local-acceptance ready but not end-user distributable. The real helper, measured
backend artifact, version handshake, managed process-tree lifecycle, bounded restart/diagnostics,
packaged bilingual backend harness, Python runtime lock, narrowed webview authority, and unsigned
NSIS/MSI build plus MSI payload identity are proven locally. The next priorities are the prepared
clean Sandbox lifecycle run, protected signing, and an independent clean-account/machine installed
GUI/audio/human-speech run with final data-retention acceptance. Capability-token authentication is
complete. Connected context is read-only and remains outside authoritative meeting evidence;
semantic extraction remains eval-only.

## Independent review

An independent Codex review found 4 HIGH/mixed and 4 MEDIUM real issues (event ordering,
thread-safety, URL-injection via meeting titles, language-provenance race, analyzer overfitting,
stale-state-after-restart, an always-on diagnostic tap, and unrestricted backend URL). The local-only
follow-up adds strict URL parsing, WebSocket browser-Origin checking, transcript-content log
redaction, and capability-token authentication for sensitive loopback routes. The former meeting-title
session-reuse collision is resolved by the opaque per-recording session id. See
`docs/MEETILY_INTEGRATION_RESEARCH.md` "Independent review (Codex)" for the complete list and
fixes. All fixes are covered by regression tests; `scripts/check.ps1` and a live confirmation
re-run both pass after the fix pass.

## Local-only / privacy posture

The Meeting Intelligence path added in this integration (`apps/api`, the Rust push, the frontend
panel) is documented and guarded as loopback-only (`127.0.0.1:8000`), calls no cloud or remote
service, and uses no LLM as the source of truth for meeting state. Meetily's separate PostHog
telemetry is disabled by default but opt-in, and its optional cloud transcription/summary providers
remain available outside this path. See `docs/LOCAL_ONLY_PRIVACY.md` for exact mitigations and
persistence boundaries.
