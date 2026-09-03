# NetSuite and Collaboration Assurance (API v9)

API v9 adds live-only NetSuite, selected Microsoft Teams spaces, and selected Slack channels to
the local evidence broker. These sources are supplemental: they cannot become meeting evidence,
fill a meeting gap, or create a durable governance record without review.

## NetSuite setup

1. Create a dedicated least-privilege integration role with SuiteAnalytics Workbook and the exact
   record/metadata permissions needed by the configured mappings.
2. Create an OAuth 2.0 client-credentials integration and enroll its RSA certificate. Store the
   matching private key through Meetily; it is written only to Windows Credential Manager.
3. Configure the account ID, client ID, certificate ID, and allow-listed record mappings. Mappings
   identify the record type, business key, display field, searchable fields, and projected fields.
4. Review-record mappings from older configurations are accepted for migration but are not executed.
   Current production code has no NetSuite writer, PATCH, or DELETE path; handoff is export-only.
5. Test metadata and mapped queries with a sandbox account before enabling data-quality runs.

NetSuite retrieval uses the account-specific SuiteTalk host, metadata catalog, and bounded SuiteQL.
It is not treated as OData. Business records are kept in memory only and disappear as soon as the
request completes or access is revoked.

## Teams and Slack setup

Teams requires resource-specific consent for each selected team/channel or selected meeting chat.
Slack search uses rotating user-only OAuth v2 PKCE through the registered
`meeting-intelligence://oauth/slack` deep link. Only public/private channel search scopes are requested. Bot
credentials, posting, direct messages, group direct messages, and file search are not supported.
The access and rotation material is retained only in Windows Credential Manager. Provider IDs are
DPAPI-encrypted and the webview receives only an opaque selection ID and safe label.

## Failure and revocation behavior

Connector timeout, throttling, revoked consent, certificate expiry, and Slack channel removal
produce safe degraded states and meeting-only fallback. They never interrupt recording. Ambiguous
delivery is recorded as `delivery_unknown` and is not retried automatically. Disconnecting a
connector removes its encrypted selected-space metadata and Credential Manager entry.

## Document routing

Routing targets are existing approved local folders. Proposals always require review, default to
copy, reject linked/escaped paths, verify the copied file hash, and only delete the source after a
verified move. No remote document destination is supported in API v9.
