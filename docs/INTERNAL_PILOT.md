# Synthetic-Tested Windows Internal Pilot

## Release label

The `0.6.0` release line targets a **signed JA/EN team pilot** for Windows 11 x64 business and IT
meetings in Japanese and English. It is not human-validated and must not be described as a public
release. Generated testing cannot establish natural turn timing, accents, spontaneous phrasing,
social usefulness, consent behavior, or whether a person will actually ask a suggested question.

The deterministic engine remains authoritative. The optional loopback semantic provider is
shadow-only and cannot affect `MeetingState`, questions, or ranking.

## Candidate surfaces present

- Opaque `meeting-intel-<32 lowercase hex>` recording identity across Rust, HTTP, WebSocket,
  recovery cache, feedback, cleanup, and saved metadata.
- Ordered bounded Meetily dispatcher with acknowledgements, retry, replay, batch gap repair, and a
  ten-second stop-drain target. The release gate must prove a full dispatcher queue or stuck request
  cannot keep the stop future alive after that budget.
- Authenticated API v8 managed-sidecar handshake and release-build automatic management, with
  manual loopback development mode and explicit environment overrides.
- SQLite/WAL active-session recovery with append-only events, compact snapshots, 24-hour crash TTL,
  corrupt-entry quarantine, UTF-8 byte-accurate 64 MiB per-session ceilings, and in-memory
  fallback. After any write failure, that session keeps the last consistent persisted prefix and
  disables later snapshot writes so replay repair can never recover facts without their evidence.
- Compact live WebSocket projection; demo/fixture WebSockets retain the full transcript contract.
- Non-blocking recording-preflight and lifecycle UI surfaces, multi-topic ASK NOW focus, local
  Useful/Dismiss feedback, retention controls, delete-all controls, and aggregate-only diagnostics.
  Actual microphone/system-audio level sampling, synthetic transcript-to-panel preflight, and
  ownership-checked per-meeting artifact deletion are implemented; packaged acceptance remains
  required rather than inferring behavior from static tests.
- Positive participant-notification confirmation is required before recording starts. Its local
  record contains only confirmation state, application version, and epoch timestamp and is removed
  by delete-all; it is operational evidence, not jurisdiction-specific legal advice.
- Deterministic JA/EN rules for all six pilot categories, including guarded Japanese
  `unclear_ownership`.
- Authenticated, state-bound `/context/question-context` retrieval that rejects stale ASK NOW
  requests, applies deterministic JA/EN terms and entity hints, and returns only supplemental
  citations outside `MeetingState`.
- Versioned ERP entity mappings, scoped OneDrive/SharePoint drive search, bounded live-only remote
  retrieval with retries/circuit breaking, and encrypted local PDF/DOCX/XLSX/CSV/JSON/Markdown/text
  indexing. Remote content is never added to the local index.
- Public citations expose business references or filenames rather than provider record IDs, require
  deterministic structured comparisons for supporting/conflicting labels, and collect only
  aggregate relevance feedback.
- Deterministic per-problem instance keys keep same-category problems isolated. Ambiguous follow-on
  facts remain unassigned, while evidence, supersede, contradict, and reopen histories stay bound
  to their exact problem instance.
- Authoritative `/issues/drafts` projections support reviewed Markdown, canonical JSON, Jira CSV,
  Azure Boards CSV, and selected-problem ZIP export. Jira/Azure work types are local configurable
  settings; exports never submit issues or persist connected excerpts unless explicitly requested.

## Executable gates

```powershell
# Assistant
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
apps\api\.venv\Scripts\python scripts\eval-pilot-corpus.py
apps\api\.venv\Scripts\python scripts\run-pilot-fault-test.py --events 10000
cd apps\api
.\.venv\Scripts\python scripts\eval_semantic_shadow.py `
  --fixture ..\..\evals\fixtures\manual-work-volume-en.jsonl --lang en

# Meetily
cd $env:MEETILY_ROOT\frontend\src-tauri
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
cargo test --locked

cd $env:MEETILY_ROOT\frontend
pnpm test
pnpm exec tsc --noEmit
pnpm lint
pnpm build
```

Run the packaged Meetily process/audio soak from the clean distribution worktree. The harness uses
the real managed backend, VB-Cable output capture, local STT, CDP-visible panel state, occupied-port
startup, clean and corrupt-cache backend restarts, pause/resume, concurrent stop, and exact-session
cache/process cleanup. It writes aggregate-only evidence and refuses to overwrite an existing
output file.

Build the executable through the Tauri CLI so `beforeBuildCommand` runs and the exported Next.js
assets are embedded in the application context. A plain `cargo build --release` is not a packaged
candidate gate; it can produce an executable whose webview opens a `chrome-error:` document even
though the Rust binary compiled successfully.

```powershell
$env:CARGO_TARGET_DIR = "$env:CARGO_TARGET_DIR"
$env:LIBCLANG_PATH = "<directory-containing-libclang.dll>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"

cd $env:MEETILY_ROOT\frontend
pnpm exec tauri build --no-bundle
Get-FileHash $env:CARGO_TARGET_DIR\release\meetily.exe -Algorithm SHA256
```

```powershell
$assistantCommit = git -C $env:ASSISTANT_ROOT rev-parse HEAD
$meetilyCommit = git -C $env:MEETILY_ROOT rev-parse HEAD

cd $env:MEETILY_ROOT\frontend
pnpm pilot:soak -- `
  --meetily-exe $env:CARGO_TARGET_DIR\release\meetily.exe `
  --audio $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus\audio\audio-manual_work-en-clean-1.wav `
  --ffplay <path-to-ffplay.exe> `
  --cache-directory $env:LOCALAPPDATA\com.meetily.ai\meeting-intelligence-cache `
  --output $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\SOAK_4H_EVIDENCE.json `
  --assistant-commit $assistantCommit `
  --meetily-commit $meetilyCommit `
  --duration-seconds 14400 `
  --backend-port 8124 `
  --cdp-port 9224 `
  --language en `
  --pause-interval-seconds 600 `
  --crash-interval-seconds 900 `
  --initial-crash-seconds 30 `
  --corrupt-crash-seconds 75 `
  --sample-interval-seconds 60 `
  --audio-segment-seconds 30
```

A run shorter than four hours is only a harness smoke. A passing full run requires a fully
acknowledged final dispatcher flush, zero current queue depth, no dropped/rejected events, no stale
ASK NOW while recovering, exact-session cache purge, bounded stop, and stopped candidate/sidecar
process trees. The cumulative `missing` counter records detected gaps that triggered repair and is
not an outstanding-loss count. Cache corruption covers quarantine and replay; physical disk
exhaustion remains a separate operator fault-injection gate.

The fixed-seed text corpus contains 1,200 conversations: 100 for each category/language pair and
30% hard negatives. Every category/language pair has at least five independently worded positive
pain statements and at least ten negative semantic forms covering questions, hypotheticals,
quoted examples, resolved statements, and unrelated context; fixed-seed neutral turns make each
conversation distinct. Checkpoint expectations come from independent golden trajectories rather
than analyzer implementation details. The semantic-diversity floor and release thresholds are
enforced by `test_pilot_corpus.py`. The
routed-audio and platform matrices are generated by `app/evals/pilot_matrix.py`: 72
category/language/acoustic sessions and 18 Zoom/Google Meet/Teams local-capture sessions.

Build the corresponding 72 hashed WAV assets from the same independent fixture/golden source:

```powershell
apps\api\.venv\Scripts\python.exe scripts\build-routed-audio-corpus.py `
  --output $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus `
  --ffmpeg <reviewed-ffmpeg.exe>
```

The generator uses two installed English SAPI voices and two deterministic Japanese SAPI rate
profiles, then emits clean, office-noise, and compressed profiles. Pass
`--voicevox-engine <local-VOICEVOX-vv-engine-run.exe>` to use two VOICEVOX styles for Japanese
instead. `MANIFEST.json` records the source fixture/golden,
expected final ASK NOW slot, synthesizer identity, and SHA-256 for every WAV.
Generated audio assets are test inputs, not passed acceptance scenarios.

Run Meetily's opt-in corpus checks against the generated directory. These tests
use the production audio decoder and installed local Parakeet/Whisper models,
but emit only scenario IDs and transcript character counts:

```powershell
cd $env:MEETILY_ROOT\frontend\src-tauri
$env:CARGO_TARGET_DIR = "$env:CARGO_TARGET_DIR"
$env:LIBCLANG_PATH = "<path-to-LLVM-bin>"
$env:CMAKE_GENERATOR = "Visual Studio 17 2022"
$env:MEETILY_ROUTED_AUDIO_CORPUS = "$env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus"

cargo test --locked meeting_intelligence_pilot::tests::routed_audio_corpus_decodes_all_declared_scenarios `
  -- --ignored --exact
cargo test --locked meeting_intelligence_pilot::tests::real_routed_audio_corpus_transcribes_all_english_scenarios `
  -- --ignored --exact --nocapture
cargo test --locked meeting_intelligence_pilot::tests::real_routed_audio_corpus_transcribes_all_japanese_scenarios `
  -- --ignored --exact --nocapture
```

A green run proves that all 72 assets decode and produce non-empty local STT
output. It does not prove analyzer accuracy, ASK NOW quality, VB-Cable routing,
or Zoom/Google Meet/Teams capture, so it must not update acceptance-ledger
scenario status.

Use the fail-closed wrapper to validate all asset hashes, run all three tests,
require exact 72-scenario output coverage, and write a commit-bound aggregate
report without transcript text:

```powershell
apps\api\.venv\Scripts\python.exe scripts\run-routed-audio-stt-acceptance.py `
  --meetily-root $env:MEETILY_ROOT `
  --corpus $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus `
  --output $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-stt-evidence.json `
  --cargo-target-dir $env:CARGO_TARGET_DIR `
  --libclang-path <path-to-LLVM-bin>
```

Create the aggregate-only acceptance ledger beside the release artifacts, then explicitly record
each scenario after running it. The ledger schema rejects transcript text, paths, meeting titles,
tokens, free-form notes, missing scenarios, descriptor changes, and generated-as-passed results.

```powershell
python scripts/pilot-acceptance-ledger.py init $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json `
  --candidate-sha256 <meetily-executable-sha256> `
  --assistant-commit <full-40-hex-assistant-commit> `
  --meetily-commit <full-40-hex-meetily-commit> `
  --corpus $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus

python scripts/pilot-acceptance-ledger.py describe `
  $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json platform-zoom_desktop-en-2 `
  --corpus $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus

python scripts/pilot-acceptance-ledger.py pass `
  $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json <scenario-id>

python scripts/pilot-acceptance-ledger.py validate `
  $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json --require-complete --require-all-passed

python scripts/pilot-acceptance-ledger.py validate-release `
  $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json `
  $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\meetily-<version>\MANIFEST.json
```

Ledger schema v2 binds the SHA-256 of `MANIFEST.json` and an exact source-audio
scenario to every row. For each platform and language, run 1 uses the clean
`data_mismatch` seed-1 asset, run 2 uses the office-noise `manual_work` seed-2
asset, and run 3 uses the compressed `integration_failure` seed-1 asset. The
`describe` command prints the relative WAV, its SHA-256, expected final ASK NOW
slot (or `null` for a resolved state), and the five required checks without
printing transcript text or an absolute path. Record `pass` only after the
described asset travelled through the named application and all five checks
were observed.

The final command fails unless all 90 scenarios pass and the ledger's exact
Meetily executable SHA-256 plus both full source commits match the immutable
artifact/Defender manifest. It also requires the same routed-audio corpus
manifest SHA-256 in both artifacts, so results from a substituted corpus cannot
be attached to the release.

The semantic shadow command prints aggregate candidate/agreement/validation counters only. It
does not print prompts, transcript text, candidate values, evidence text, or provider output, and
its observations are never composed into the meeting engine.

The deterministic fault command verifies a 10,000-event ordered final transcript under shuffled
delivery, duplicate requests, repaired gaps, repeated SQLite/WAL reconstruction, a simulated
cache-write failure followed by durable-prefix replay repair, corrupt-session quarantine,
subscriber disconnect/reconnect, idempotent cleanup, and rejection of a late post-cleanup event.
It enforces the backend processing, recovery-time, and 64 MB active-cache limits. It does not run
for four hours or create real process, network-port, WebSocket, audio, or sidecar failures; those
remain external acceptance work below.

## External acceptance still required

Do not mark the installer ready until a release operator records:

1. All 72 SAPI/VOICEVOX + FFmpeg + VB-Cable routed-audio sessions.
2. All 18 synthetic Zoom desktop, Google Meet browser, and Teams desktop path sessions.
3. A four-hour soak including sidecar crashes, occupied ports, disk-full behavior, corrupt cache,
   WebSocket disconnects, pause/resume, and stop races.
4. Windows scaling at 125% and 150%, keyboard and screen-reader flow, and long JA/EN strings.
5. An unsigned installer build from clean branches, SHA-256 hashes for every bundled executable,
   Defender scan output, install/uninstall/rollback notes, and tracked-secret/dependency audits.

Until those artifacts exist, use generic **locally captured meeting audio** wording. The unsigned
installer is for a trusted internal group only, with no automatic updater request.

After synthetic and clean-machine gates pass, follow `docs/SIGNED_TEAM_PILOT_OPERATIONS.md` for
protected Authenticode signing, Microsoft 365 and Dynamics 365 non-production acceptance, the
two-week 5-10 user pilot, and immutable release-manifest generation. A source or binary change
invalidates evidence bound to the previous candidate.

## Implementation status

The clean Meetily pilot branch closes the former code blockers for bounded dispatcher shutdown,
cleanup acknowledgement diagnostics, replay recovery validation, ASK NOW topic anchoring,
per-meeting artifact deletion, sampled device preflight, spoken transcript-to-panel preflight,
atomic Windows settings replacement, generic support copy, aggregate reconnect/recovery/sidecar
diagnostics, and delete-all transient-state purge. Those automated checks do not waive the external
installer, routed-audio, platform-path, accessibility, soak, or human-pilot gates above.
