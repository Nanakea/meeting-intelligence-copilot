# Current Product Status

Target: **0.6.2 / API v13**, unsigned Windows 11 x64 internal candidate.
Release metadata lives in `apps/api/app/release.json`; component manifests are checked against it.

This is one monorepo: `apps/api` is the local intelligence backend, `apps/desktop` is the desktop
application, and `apps/web` is the fixture/demo renderer. Do not create a second product repository.
Third-party licensing notices remain mandatory.

## Current Work

- Release scripts share version/artifact metadata and reject mismatched backend source or locks.
- Preflight treats unknown retention as a warning and invalidates stale language/device results.
- The editor uses compatible BlockNote/ProseMirror/Tailwind dependencies and behavioral tests.
- API v13, meeting evidence invariants, read-only external access, and JA/EN/KO remain unchanged.

Source implementation and tests are **not** packaged acceptance. No 0.6.2 installer, live company
tenant, routed-audio matrix, or human usability result is approved by this status file.

Local verification on September 5: 600 backend tests passed (1 skipped), 24 golden fixtures passed,
300 Korean deterministic cases met thresholds, and the 22-test demo UI passed. Desktop behavior,
TypeScript, production build, and zero-warning scoped lint passed. Rust: 306 application tests,
2 helper tests, and 1 documentation test passed; 12 real-STT tests remain ignored.
The deterministic 10,000-event fault harness passed; this is not the four-hour routed-audio soak.
Inherited lint debt is isolated by a 207-warning per-file/rule baseline, not hidden or called clean.

## Release Gates

Follow [the current packaged checklist](PACKAGED_ACCEPTANCE_CHECKLIST.md) against one frozen commit.
See [capability boundaries](CAPABILITY_MATRIX.md), [first workday setup](FIRST_WORKDAY_SETUP.md),
and [daily-use support](DAILY_USE_SUPPORT.md). Notion is selected-page read access; NetSuite,
WMS/FBA, and collaboration acceptance still need approved real sources.

Historical test counts, commit IDs, and artifact evidence are retained in
[the archived status](history/PROJECT_STATUS_THROUGH_0.6.1.md). They do not approve this release.
