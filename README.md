# Meeting Intelligence Copilot

Local-first Windows workbench for live meeting questions, solution governance,
enterprise evidence, document assurance, and ERP reconciliation. It supports explicit Japanese,
English, and Korean language paths and is designed for business and IT solution-lead work.

The product is a **question copilot, not primarily a summarizer**. It maintains deterministic
meeting state and shows one **ASK NOW** question plus at most two **FOLLOW UP** questions.

> **Release status:** API v13 source candidate. Source verification is green. The Windows package is
> unsigned and real-company tenants, routed Korean audio, clean-machine installation, and consented
> human use remain acceptance gates.

## What is implemented

- Live JA/EN/KO transcript ingest, ordered delivery, deduplication, gap repair, restart recovery,
  and opaque per-recording session identity.
- Evidence-backed multi-instance problem detection, non-destructive fact history, deterministic
  information gaps, and structured issue drafts.
- A Windows Tauri desktop recorder and intelligence panel in `apps/desktop`, shipped in this same
  repository.
- Local document indexing and search for PDF, DOCX, XLSX, CSV, JSON, Markdown, and text, with
  bounded extraction and review-gated assurance findings.
- Read-only selected-page Notion retrieval, exposed in the desktop Connections UI.
- Read-only connector implementations or governed gateway seams for Microsoft 365, Teams, Slack,
  NetSuite, generic WMS REST, Amazon FBA, SAP, Dynamics/OData, GitHub, GitLab, Jira, Confluence,
  Azure DevOps, ServiceNow, approved SQL views, SFTP, and GET/HEAD-only OpenAPI services.
- NetSuite -> WMS -> Amazon FBA reconciliation for missing handoffs, quantity mismatches, duplicate
  keys, and bounded price/inventory/fulfillment trends.
- Documentation-to-system consistency checks across approved documents, repository/API metadata,
  pipelines/releases, ERP metadata, and CMDB records.
- Review-gated governance, issue/ADR/RAID exports, mind maps, traceability, and supervised local
  improvement profiles.

See [Capability Matrix](docs/CAPABILITY_MATRIX.md) for the exact distinction between implemented,
simulator-tested, source-only, and externally unverified features.

## Non-negotiable boundaries

- Every pain point and meeting fact cites real transcript event IDs.
- Evidence and inference are separate; superseded and contradicted facts are retained.
- External RAG and connector content never becomes transcript evidence, fills meeting gaps, or
  mutates authoritative `MeetingState`.
- Remote systems are read-only in the production composition. Direct issue submission, chat
  posting, ERP mutation, remote deletion, and automatic file movement are disabled.
- Meeting processing does not wait for RAG, connectors, or optional local semantic models.
- The pilot enables only built-in local inference and loopback Ollama. Cloud model providers,
  transcript upload, remote telemetry, and direct external writes are disabled.

## Meeting and language support

Zoom, Google Meet, and Microsoft Teams work through audio captured locally by the desktop recorder;
there is no direct meeting-platform API integration. Japanese and English have source and routed
acceptance history. Korean is a first-class explicit `ko` path using signed-manifest multilingual local Whisper and
passes the 300-case deterministic synthetic corpus, but routed Korean audio and human validation are
not yet complete. Language is never inferred from Unicode ranges.

## Repository layout

```text
apps/api/       FastAPI API, domain, services, adapters, gateway, and tests
apps/web/       Contract/demo web client
apps/desktop/   Windows Tauri/Rust + Next.js desktop application
contracts/      Generated JSON Schemas shared with clients
evals/          Synthetic transcript fixtures and deterministic goldens
scripts/        Setup, checks, packaging, and acceptance tools
docs/           Product, security, connector, operations, and release documentation
```

The desktop capture layer is derived from the MIT-licensed Meetily project. Its required copyright
and license remain in [`apps/desktop/LICENSE.md`](apps/desktop/LICENSE.md), and the modification
boundary is documented in [`apps/desktop/NOTICE.md`](apps/desktop/NOTICE.md). The product integration,
meeting-intelligence backend, governance, assurance, connector, and reconciliation work in this
repository are maintained by Nanakea.

## Windows development

Prerequisites: Windows 11 x64, Git, PowerShell, Python 3.12, Node.js, pnpm 11, Rust, Visual Studio
C++ Build Tools, CMake, and LLVM/libclang for a full desktop build.

```powershell
git clone https://github.com/Nanakea/meeting-intelligence-copilot.git
cd meeting-intelligence-copilot
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-dev.ps1
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Desktop checks run from the monorepo:

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

The local launcher now discovers `apps/desktop` automatically:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local-v1.ps1 -LaunchDesktop
```

`MEETILY_ROOT`, `-MeetilyPath`, and `-LaunchMeetily` remain compatibility names for older automation, but none is
required by the monorepo layout.

## Approval and security

Do not connect company systems before approval. Start with
[Company Approval: Read-Only Connectors](docs/COMPANY_APPROVAL_READ_ONLY_CONNECTORS.md), which lists
the exact permissions requested and explicitly prohibited operations. Credentials belong in
Windows Credential Manager or an approved gateway secret store; local indexes and identifiers are
DPAPI-protected for the current Windows user.

No real audio, transcript, company document, ERP row, chat message, credential, certificate,
private key, local index, model weight, or packaged binary belongs in Git.

## Key documentation

- [Current capability and acceptance matrix](docs/CAPABILITY_MATRIX.md)
- [API v13 product brief](docs/presentations/meeting-intelligence-copilot-api-v13-brief.pdf)
- [Product specification](docs/PRODUCT_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Korean language support](docs/KOREAN_LANGUAGE_SUPPORT.md)
- [Connected-context security and permissions](docs/COMPANY_APPROVAL_READ_ONLY_CONNECTORS.md)
- [NetSuite/WMS/FBA reconciliation](docs/NETSUITE_WMS_FBA_RECONCILIATION.md)
- [Documentation-to-system consistency](docs/DOCUMENT_SYSTEM_CONSISTENCY_V13.md)
- [Fresh-machine setup](docs/FRESH_MACHINE_SETUP.md)
- [Project status](docs/PROJECT_STATUS.md)

## Licensing

Unless a file or component states otherwise, no license is granted for reuse or redistribution of
the Nanakea-authored product source. `apps/desktop` contains MIT-licensed Meetily-derived code and
must retain its component license and notice. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
