# Enterprise Evidence and Assurance (API v8)

API v8 adds a local-first evidence and assurance workbench for a Global Solution Lead. It does not
change the deterministic meeting engine or its evidence lifecycle.

## Authority boundaries

- `MeetingState` is still a fold over transcript events and remains the only meeting authority.
- Local RAG, Microsoft Graph, ERP metadata, and document findings are supplemental evidence only.
- Retrieval cannot fill a meeting gap, create a fact, confirm governance, or change issue readiness.
- Findings and solution-thread links require explicit, revision-checked review.
- External handoff remains export-only. No connector has write or submission authority.

## Local evidence

Supported local files are PDF, DOCX, XLSX, CSV, JSON, Markdown, and text. Extraction is bounded by
file size, archive expansion, page, sheet, cell, section, and text limits. Macros, embedded objects,
scripts, external OOXML relationships, unsupported files, and symlink escapes are ignored or
rejected. Revisions, section text, and optional embeddings are encrypted for the current Windows
user. Retrieval uses an in-memory FTS5/BM25 index with deterministic reciprocal-rank fusion.

Optional semantic reranking uses a manually installed, SHA-256-pinned INT8 ONNX
`multilingual-e5-small` profile. The application never downloads a model automatically and falls
back to lexical retrieval when the runtime or model is unavailable. Set
`MEETING_INTELLIGENCE_EMBEDDING_MODEL_DIR` to the local bundle directory and
`MEETING_INTELLIGENCE_EMBEDDING_MODEL_SHA256` to the verified bundle hash to enable it. Connector
administrators can assign a bounded 0-100 source-authority preference; this affects ranking only and
never changes evidence or review state.

## Live connected evidence

Microsoft Graph is limited to the signed-in user's OneDrive and explicitly selected SharePoint
drives. Dynamics, SAP, and generic OData connectors use read-only allow-listed metadata and entity
mapping endpoints. Remote document and business-record content is held in memory for the bounded
request and is not added to the persistent local evidence index. Connector timeout, revocation, or
throttling produces a degraded result and never interrupts recording or ASK NOW.

## Assurance workflow

Built-in deterministic rules check requirements, architecture, interfaces, mappings, NFR/security,
tests, cutover, rollback, operations, ownership, acceptance criteria, placeholders, terminology,
references, and ERP metadata drift. Company packs are signed ZIP files containing a strict manifest,
JSON rules, hashes, and an Ed25519 signature. Only keys explicitly enrolled by the current user are
trusted.

Assurance runs are incremental by document revision. Findings are append-only and can be confirmed,
dismissed, resolved, reopened, or proposed for later governance review without automatic admission.
The digital solution thread extracts explicit requirement/NFR/test references, but inferred links
remain suggested until reviewed. Impact analysis traverses confirmed edges only and defaults to two
hops.

## Scheduling and operations

Meetily registers an explicit per-user Windows Task Scheduler entry. It launches the signed backend
in bounded headless mode for the selected schedule and exits after the run. The task inherits only
the current user's Credential Manager and DPAPI scope. Failures expose fixed safe status codes, not
tokens, document content, raw exceptions, or development paths.

API v8 source is an internal candidate. Real tenant access, company rule packs, signed packaging,
clean-machine lifecycle, routed audio, soak testing, and supervised human validation remain release
gates rather than claims established by source code.
