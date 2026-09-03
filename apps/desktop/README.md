# Meeting Intelligence Copilot Desktop

This directory contains the Windows desktop recorder, local STT runtime, live intelligence panel,
connector settings, governance/assurance UI, and sidecar lifecycle for Meeting Intelligence Copilot.
It is part of the parent monorepo and is not a separately released product.

## Product role

The desktop captures microphone and selected system audio, transcribes locally, and sends ordered
events to the authenticated loopback API. It renders the compact authoritative projection and keeps
recording independent from intelligence, RAG, and connector failures.

Supported configured transcript languages are Japanese, English, and Korean. Zoom, Google Meet,
and Teams support means local audio capture; the desktop does not call their meeting APIs.

The Connections UI supports local files, SharePoint/OneDrive, selected Notion pages, NetSuite,
generic WMS REST, Amazon FBA, SAP/Dynamics/OData, selected Teams/Slack spaces, and governed gateway
connections. Production composition is read-only and direct external writes are disabled.

## Development

```powershell
cd apps\desktop\frontend
pnpm install --frozen-lockfile
pnpm test
pnpm exec tsc --noEmit
pnpm build

cd ..\src-tauri
$env:CARGO_TARGET_DIR = "E:\cargo-targets\meeting-intelligence-copilot"
cargo test --locked
```

Ignored sidecar binaries and optional local models must be prepared through the reviewed root
scripts. Do not commit binaries, models, audio, transcripts, credentials, or local databases.

## Release status

This is an unsigned source candidate. Korean deterministic source coverage is complete, but routed
Korean audio and human validation remain open. Enterprise connectors require real non-production
tenant acceptance and company approval before use.

See the parent [capability matrix](../../docs/CAPABILITY_MATRIX.md) and
[read-only connector approval guide](../../docs/COMPANY_APPROVAL_READ_ONLY_CONNECTORS.md).

## Attribution and license

The desktop capture foundation is derived from Meetily and remains subject to the MIT License in
[`LICENSE.md`](LICENSE.md). The product modifications and compatibility boundary are described in
[`NOTICE.md`](NOTICE.md). Meeting Intelligence Copilot is maintained by Nanakea and is not endorsed
by the upstream project.
