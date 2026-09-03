# Desktop Privacy Boundary

Meeting Intelligence Copilot records and transcribes on the local device. The intelligence sidecar
binds to loopback and uses capability authentication in managed mode. Saved recordings and local
indexes remain under the current Windows user until the configured retention or explicit deletion
workflow removes them.

Remote connectors are optional, selected-source, read-only evidence providers. Remote content is
not meeting evidence and is not retained where the capability matrix marks it live-only. Connector
credentials are stored in Windows Credential Manager or an approved gateway secret store.

The pilot runtime does not upload transcripts, enable cloud AI, or send remote telemetry. Rust
command boundaries reject cloud summary/transcription providers. Fresh installs use the verified
multilingual Whisper artifact; optional summaries may use only a loopback Ollama endpoint.

See [`../../docs/PRIVACY_AND_SAFETY.md`](../../docs/PRIVACY_AND_SAFETY.md) and
[`../../docs/COMPANY_APPROVAL_READ_ONLY_CONNECTORS.md`](../../docs/COMPANY_APPROVAL_READ_ONLY_CONNECTORS.md)
for the complete retention, approval, revocation, and residual-risk model.
