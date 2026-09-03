# Connected Context

Connected context is an optional, read-only extension to the Meeting Intelligence Copilot. It can
show relevant knowledge or ERP records beside an ASK NOW question and include cited context in a
local issue draft. It does not change `MeetingState`, fill a gap, create a meeting fact, or write to
an external system.

## Security model

- The loopback context API requires the same per-launch capability token as live ingestion.
- Remote connectors accept only HTTPS URLs without embedded credentials, query strings, or
  fragments. Requests cannot escape the configured origin.
- Microsoft refresh tokens and manually supplied ERP bearer tokens are stored in Windows
  Credential Manager and are never returned by the API. Microsoft access tokens stay in memory.
- Credential targets are fingerprinted by connector kind and HTTPS base URL, or by Microsoft tenant
  and client ID. Changing that security boundary discards the old token instead of reusing it.
- Connector definitions and cached documents are encrypted with current-user Windows DPAPI inside
  a bounded SQLite/WAL index under `%LOCALAPPDATA%\MeetingIntelligenceCopilot`.
- Every retrieved document carries an explicit principal. The broker repeats the ACL check and the
  backend replaces caller-supplied principals with the Windows account running the sidecar.
- Operational errors are reduced to fixed status/detail codes. Tokens, content, paths, and raw
  exceptions are not rendered or logged.
- Disconnect deletes the connector definition, encrypted cache, and credential. If Credential
  Manager is temporarily unavailable, an encrypted cleanup handle survives metadata purge and is
  retried on the next disconnect, delete-all, or backend start; token material is never copied into
  the SQLite index. Delete-all removes all connector content without affecting saved meetings.

## Initial providers

- **Local files:** recursively indexes bounded UTF-8 `.txt`, `.md`, `.csv`, and `.json` files from
  one selected folder. Symlinks resolving outside that folder are skipped.
- **SharePoint / OneDrive:** uses Microsoft Graph delegated device-code sign-in. Source permissions
  remain authoritative; the application requests only `Files.Read.All`, `Sites.Read.All`, and
  `User.Read`, plus `offline_access` for local refresh.
- **Generic OData:** reads only explicitly configured entities and fields from an HTTPS endpoint.
  Same-origin `@odata.nextLink` pages are followed with duplicate suppression and a global maximum
  of ten 50-row pages per search, preventing unbounded ERP scans.
- **SAP / Dynamics 365:** OData presets with a small allow-list of common business entities.

Graph setup requires a Microsoft Entra public-client application with device-code sign-in enabled.
Enter its tenant ID and client ID in Connections, then follow the displayed Microsoft verification
link and one-time user code. The OAuth device code is retained only in backend memory while sign-in
is pending. Tenant consent and source ACLs remain administrator-controlled release gates.

Generic OData, SAP, and Dynamics connectors currently accept a read-only bearer token. Configure
the source-side identity with the narrowest entity and field permissions available. The connector
cannot technically upgrade a read-only token and exposes no external write method.

## Data use

Retrieval is deterministic lexical matching. There is no RAG model, cloud AI, autonomous action, or
semantic mutation path. The panel labels results as external and not stated in the meeting, shows a
freshness warning where applicable, and hides raw connector IDs and record IDs. Issue drafts are
copy/export artifacts only; no submit endpoint exists.

Remote connectors intentionally contact the configured source system. With no remote connectors
configured, the meeting intelligence runtime remains loopback-only and makes no remote request.
