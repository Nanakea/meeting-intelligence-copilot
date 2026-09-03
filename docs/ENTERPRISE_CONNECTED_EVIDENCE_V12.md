# Enterprise Connected Evidence (API v12)

API v12 adds a hybrid evidence plane without changing meeting authority. Live transcripts,
`MeetingState`, local indexes, and ASK NOW stay on the Windows device. The optional company gateway
holds enterprise credentials and returns only bounded, ACL-filtered evidence for explicitly selected
sources.

## Authority boundary

- External results never become transcript evidence, fill a meeting gap, or mutate `MeetingState`.
- ASK NOW never waits for a connector. Contextual retrieval has a two-second deadline; global search
  has a ten-second maximum.
- Direct external writes are disabled. Historical approval endpoints return
  `direct_writes_disabled`; reviewed exports are the only handoff.
- Slack, Teams, NetSuite business records, SQL rows, and OpenAPI responses remain live-only.
- Selected repository and work-management content may be encrypted locally only while a current
  15-minute authorization lease permits access. Revocation purges that connector index.

## Gateway deployment

The separately deployed `python -m app.gateway_main` process requires:

- an HTTPS server certificate, private key, and client CA for mandatory mTLS;
- OIDC introspection URL, client credentials, expected issuer, and expected audience;
- an administrator-owned connector configuration file referenced by
  `MEETING_INTELLIGENCE_GATEWAY_CONNECTORS`;
- provider credentials supplied by gateway secret injection, never by the desktop configuration
  file.

The desktop OIDC authorization-code/PKCE flow uses a fixed `meeting-intelligence://oauth/enterprise` callback.
Authorization endpoint, token endpoint, public client ID, and scopes are managed through the
`MEETING_INTELLIGENCE_GATEWAY_OIDC_*` environment settings. PKCE verifiers remain in memory. Access
and refresh material is stored in Windows Credential Manager and is never sent to the webview.

## Supported source model

Built-in read plans cover GitHub, GitLab, Jira, Confluence, Azure DevOps, ServiceNow, and validated
OpenAPI GET operations. SQL views and SFTP roots require an approved out-of-process adapter; they
remain fail-closed when that adapter is absent. NetSuite, Microsoft 365, Teams, Slack, SAP,
Dynamics, OData, selected Notion pages, and local documents continue through their existing bounded
adapters. Notion and collaboration results remain live-only.

Every source is selected with a safe local label and an encrypted provider identifier. Search
results expose only source label, business/reference path, freshness, relation, retrieval time, and
explainable rank reasons. Provider tokens, connector IDs, database keys, and opaque record IDs are
not rendered.

## Acceptance boundary

Simulator and protocol tests are development evidence only. Each real company tenant requires
separate non-production acceptance for authorization denial, pagination, throttling, revocation,
webhook reconciliation, source deletion, and citation ACLs. The v12 source change invalidates every
older sidecar and installer hash. Do not describe v12 as packaged or tenant-compatible until a new
artifact manifest and the tenant-specific gates pass.
