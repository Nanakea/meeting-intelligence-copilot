# Adaptive ERP Governance and Document Learning (API v10)

API v10 adds supervised local improvement around the existing deterministic meeting,
assurance, and NetSuite workflows. It does not self-modify code, change connector scopes,
admit governance automatically, or write operational ERP records.

## Improvement lifecycle

1. Existing bounded review actions emit encrypted `ImprovementSignal` records. Remote source
   content is never retained. Reviewed local excerpts are capped at 600 characters and expire
   after 90 days by default.
2. The backend creates declarative proposals only after the configured minimum evidence count.
3. Every proposal receives a deterministic fingerprint-based holdout evaluation. Ranking changes
   require at least 95% holdout precision, at least 3% NDCG@5 improvement, no cohort regression
   above 2%, and zero authorization failures.
4. A user must approve an eligible proposal. The resulting profile runs for one complete
   assurance or ERP-governance cycle in shadow mode before activation.
5. Previous profiles remain available for rollback. Exported packs contain declarative profiles,
   compatibility metadata, aggregate metrics, and hashes only; they contain no examples or source
   content and are signed with a user-scoped DPAPI-protected Ed25519 key.

Learning can change only bounded source-authority weights, declarative mappings, glossary aliases,
terms, and thresholds. Active retrieval deltas are clamped to plus or minus 10 points and are
applied after ACL filtering. Pausing learning stops new signal capture without deleting history.

## Managed documents

The document inbox classifies each indexed revision, proposes safe filenames and collections,
groups exact content hashes, and suggests possible revisions using bounded lexical similarity.
Every classification and disposition uses optimistic revisions. Approval records the proposal;
it does not move or delete a file. Existing reviewed document-routing commands remain the only
path that can perform a confirmed local copy or move, and copy remains the default.

Supported extraction remains PDF, DOCX, XLSX, CSV, JSON, Markdown, and text. Optional local OCR can
be enabled without downloading software:

- `MEETING_INTELLIGENCE_TESSERACT_PATH`: absolute path to `tesseract.exe`.
- `MEETING_INTELLIGENCE_PDFTOPPM_PATH`: optional absolute path to `pdftoppm.exe` for scanned PDFs.
- `MEETING_INTELLIGENCE_OCR_LANGUAGES`: Tesseract languages, default `jpn+eng`.

OCR is bounded to 50 PDF pages, 150 DPI, fixed process arguments, 45-second process limits, and the
same extracted-text and file-size ceilings as normal indexing. OCR output is untrusted document
content and never becomes meeting evidence.

## NetSuite governance

Built-in templates cover Order-to-Cash, Procure-to-Pay, Inventory, Project-to-Cash, and Financial
Close. A template activates only when every required NetSuite record type has a validated mapping.
Runs reuse the read-only v9 data-quality boundary and return review-required risk, action, issue,
and mapping proposals. Only safe source references, rule identifiers, hashes, timestamps, and
reviewed findings persist. NetSuite records remain live-only. This historical v10 design included
a separately confirmed custom review-record handoff; the current API v13 production composition
has removed that writer and permits reviewed exports only.

## Failure isolation

The v10 SQLite/WAL store is optional at application composition. DPAPI, disk, OCR, connector,
evaluation, import, or governance failures disable only the affected feature. Recording,
transcription ingestion, deterministic `MeetingState`, and ASK NOW do not wait for or depend on the
improvement workspace.
