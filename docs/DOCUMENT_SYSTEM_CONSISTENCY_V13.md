# API v13 Documentation-to-System Consistency

## Purpose

The consistency engine answers: **what does the approved documentation claim, what does the
selected system expose, and exactly where do they disagree?** It compares two cited, revisioned
sources without assuming that either side is automatically correct.

## Supported first-wave observations

- Selected GitHub/GitLab repository artifacts, symbols, manifests, OpenAPI contracts, tests,
  pipelines, releases, and deployment configuration available through the v12 connector gateway.
- NetSuite SuiteTalk metadata plus configured SAP, Dynamics, and generic OData metadata snapshots.
- Selected ServiceNow CMDB/service metadata and approved GET-only API artifacts.
- Approved local, SharePoint, Confluence, repository, SFTP, and other selected documentation.

Only selected sources are inspected. Remote sources require a current access lease and their ACLs
remain authoritative. Revocation makes leased values unavailable and purges the connector index.

## Review model

1. Deterministic extraction creates confirmed claims from explicit tables, mappings, qualified
   fields, versions, environments, configuration, tests, deployments, ownership, and controls.
2. Optional local semantic extraction creates only proposed claims tied to an exact literal span.
3. The comparison service matches canonical subject/property plus compatible environment/version.
4. A finding contains documented and observed values or policy-safe hashes, both citations,
   revisions, freshness, a deterministic rule, impact, and recommended verification.
5. Attribution remains `neutral` unless a reviewed authority policy applies. Users may confirm,
   dismiss, resolve, classify, assign a role/team, request a source refresh, or create an expiring
   approved exception.
6. Reviewed findings export as Markdown, canonical JSON, Jira CSV, or Azure Boards CSV. No direct
   external write endpoint is enabled.

## Safety properties

- Findings and claims are outside `MeetingState` and cannot fill gaps or become transcript evidence.
- Cross-environment and multi-source ambiguity is surfaced; the engine does not silently choose a
  system record.
- Unchanged scheduled scans reuse the prior evaluated source fingerprint. Changed source revisions,
  authority policies, or confirmed semantic claims force a fresh comparison.
- Reviewed findings are retained and superseded, not deleted, when source revisions change.
- Suggested solution-thread links remain unconfirmed until separately reviewed.
- ASK NOW and recording never wait for consistency, RAG, connectors, or the optional local model.

## Compact evidence map

`POST /solution-thread/mind-map` projects the existing review-gated solution thread into a bounded
navigation map. It returns safe public node identifiers, labels, source references, confirmed or
suggested relationship status, a stable source fingerprint, compact retrieval hints, an outline,
and Mermaid flowchart text. Clients can reuse an unchanged fingerprint instead of rebuilding the
same context, and can seed retrieval with the returned hints instead of repeatedly processing full
documents.

The projection excludes dismissed links and never confirms a suggested relationship. It does not
copy transcript text, document bodies, ERP rows, connector identifiers, or graph-internal IDs into
display labels. Impact analysis continues to use confirmed edges only.

## Operational status

Source and deterministic simulator tests can establish implementation correctness. They do not
establish compatibility with a real company tenant, repository policy, NetSuite customization,
ServiceNow schema, or authority model. Those require non-production tenant acceptance, approved
mappings, package rebuild, signing, clean-machine validation, and supervised human review.
