# Pilot Warning Baseline

The `0.6.0` intelligence and connector files must pass scoped ESLint with zero warnings. Node's
TypeScript test commands suppress only `MODULE_TYPELESS_PACKAGE_JSON`, an inherited test-loader
notice caused by the CommonJS-compatible Next.js configuration; all other Node warnings remain visible.

The following Rust warnings predate the `codex/release-0.6-pilot` branch and are outside the live
intelligence, connector, consent, transport, and sidecar surfaces:

- unused imports in the FFmpeg build helper, performance macro exports, audio device fallback,
  diagnostics, and summary sidecar;
- an unnecessary test expression in the audio decoder and an unused mutable binding in recording
  preferences;
- existing summary visibility, dead-code, pipeline-field, template, and system-audio test warnings.

Release review must compare warnings against this category list. A warning in
`meeting_intelligence_*`, `audio/transcription/intelligence_dispatcher.rs`,
`audio/recording_commands.rs` consent calls, connected-context frontend files, or the `0.6.0`
release scripts is a regression and fails the candidate. Do not run `cargo fix` across the damaged
upstream baseline as part of pilot hardening.
