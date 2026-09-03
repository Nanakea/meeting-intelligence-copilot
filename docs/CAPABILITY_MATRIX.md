# Capability and Acceptance Matrix

This matrix is the source of truth for what the API v13 repository implements and what still needs
external validation. `Implemented` means the source path and automated tests exist. It does not mean
a company tenant, natural conversation, or signed installer has accepted it.

## Live meeting intelligence

| Capability | Source status | Acceptance status | Important boundary |
|---|---|---|---|
| English meetings | Implemented | Deterministic, routed-audio, and prior local path evidence | Human pilot still required |
| Japanese meetings | Implemented | Deterministic, routed-audio, and prior local path evidence | Human pilot still required |
| Korean meetings | Implemented | 300-case deterministic synthetic corpus and source tests | Routed audio, packaging, and human validation remain open |
| Zoom | Implemented through local capture | Synthetic/local path only | No Zoom API or Marketplace app |
| Google Meet | Implemented through local capture | Synthetic/local path only | No Google Meet API |
| Microsoft Teams meetings | Implemented through local capture | Synthetic/local path only | Connector permissions are unrelated to audio capture |
| Direct Zoom/Meet/Teams transcript adapters | Stubs only | Not shipped | Disabled feature seams |

Language is supplied explicitly as `ja`, `en`, or `ko`. The production path does not infer language
from text or Unicode ranges. Fresh installs use the signed-manifest multilingual Whisper artifact.
Legacy preinstalled Parakeet remains English-only; its unverified network download path is disabled.

## Knowledge, documents, and RAG

| Source or workflow | Implementation | Persistence | External acceptance |
|---|---|---|---|
| Local files: PDF, DOCX, XLSX, CSV, JSON, Markdown, text | Section-aware extraction, FTS5/BM25 search, bounded assurance | Encrypted local index | Source-tested |
| Local OCR | Optional Tesseract adapter with preflight | Encrypted reviewed output | Installed OCR/runtime acceptance required |
| Notion | Read-only adapter and desktop selected-page setup | Page IDs encrypted; retrieved markdown live-only | Real workspace approval and acceptance required |
| SharePoint / OneDrive | Microsoft Graph read path and desktop setup | Remote content live-only | Tenant registration and selected-drive acceptance required |
| Confluence | Governed gateway contract/plans | Lease-bounded selected content | Real tenant acceptance required |
| Mind maps / solution thread | Bounded review-gated projection | Confirmed local graph only | Source-tested |
| Optional local vector reranking | Adapter/profile seam | Local model and embeddings only when configured | Model artifact not bundled |

Notion is working at source level: the adapter can issue only bounded `GET` requests for explicitly
enrolled page UUIDs, the credential is stored outside plain configuration, redirects are disabled,
and the UI now exposes Notion setup. It has not been tested against the user's future company
workspace and must not be described as company-ready before that acceptance.

## ERP, fulfillment, and analytics

| System | Implementation | Allowed operations | Acceptance |
|---|---|---|---|
| NetSuite | SuiteTalk metadata, bounded SuiteQL, mappings, governance, trend inputs | Read-only in production | Protocol simulator only; real sandbox required |
| Generic WMS | Allow-listed HTTPS REST entities and mappings | GET/read-only | Protocol/source tests; company WMS required |
| Amazon FBA | Selected SP-API inventory and inbound shipment model | Read-only | Protocol/source tests; Amazon approval required |
| NetSuite -> WMS -> FBA | Deterministic handoff reconciliation and safe references | Findings and exports only | Mapping-specific sandbox acceptance required |
| SAP / Dynamics / OData | Metadata and mapped read connectors | Read-only | Real non-production tenants required |
| Price and operational trends | Currency-isolated deterministic aggregates | Analysis only | Not financial advice; source completeness must be reviewed |

No connector can patch, delete, or create operational ERP records. Historical custom review-record
and notification flows are disabled in production and return `direct_writes_disabled`.

## Repositories, delivery, and collaboration

| Source | Implementation | Scope | Acceptance |
|---|---|---|---|
| GitHub / GitLab | Gateway plans, selected repository artifacts, code/symbol retrieval contracts | Read-only selected repositories | Real organization acceptance required |
| Jira / Azure DevOps | Read/search and reviewed CSV/Markdown handoff | No issue creation | Real project acceptance required |
| ServiceNow | Allow-listed table/service/CMDB read contract | No change creation | Real instance acceptance required |
| Slack | Selected public/private channel search | No DMs, files, posting, update, or delete | Real workspace approval required |
| Microsoft Teams context | Selected team/channel and meeting-chat retrieval | No DMs or posting | Resource-consent acceptance required |
| SQL / SFTP / OpenAPI | Approved views, roots, host keys, and GET/HEAD operation manifests | Read-only | Company gateway acceptance required |

Enterprise gateway simulators and source tests prove contract behavior, not production compatibility.
Every connector needs least-privilege approval, source selection, revocation testing, and a negative
test proving unauthorized records cannot be cited.

## Governance and consistency

- Deterministic problem instances and facts remain authoritative only when backed by transcript IDs.
- Decisions, risks, dependencies, actions, assumptions, constraints, architecture impacts, semantic
  hints, document claims, and consistency findings require explicit review.
- Documentation-to-system findings contain two source revisions and neutral attribution by default.
- Authority policies may classify drift only after review; they never modify documents or systems.
- Reviewed exports include Markdown, JSON, Jira CSV, Azure Boards CSV, RAID/ADR formats, and ZIP
  packs. Direct submission is disabled.

## Current release gates

Open gates before ordinary workplace use:

1. Rebuild and verify the API v13 sidecar plus renamed Windows NSIS/MSI artifacts from this monorepo.
2. Run a clean Windows Sandbox install/upgrade/rollback/uninstall lifecycle.
3. Complete routed Korean audio and JA/EN/KO packaged WebSocket acceptance.
4. Obtain company consent, source permissions, and non-production connector acceptance.
5. Run a supervised consented human pilot and document usefulness/false-prompt results.
6. Sign final evidence-matched binaries before broader distribution.
