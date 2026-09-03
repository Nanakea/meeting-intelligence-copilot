# Trusted ERP Governance and Document Operations (API v11)

API v11 hardens the supervised v10 workbench. It remains local-first and does not
claim production NetSuite acceptance without a company sandbox.

## Improvement trust boundary

- Proposal changes are built from the deterministic 80% training partition only.
- The untouched holdout, active-profile revision, and immutable synthetic corpus are
  hashed into every evaluation. Changed inputs make the result stale.
- Language, source, process, and document-kind cohorts are reported separately.
- Approval creates a shadow profile. Three explicit comparisons with at least 30
  applicable cases are required before activation.
- Unrelated assurance or ERP runs cannot promote or block a profile.
- API v10 profiles remain encrypted historical records and are marked for revalidation.
- Independent safety cases actively verify both accepted declarative values and rejected
  authorization-shaped mutations; they are not recorded as an automatic pass.
- Every profile type has a bounded runtime consumer. Profiles may change ranking, recognition,
  review ordering, or a reviewed routing proposal, but cannot change authorization or execute work.

## NetSuite boundary

The test-only SuiteTalk simulator covers certificate-token failure, metadata catalogs,
SuiteQL pagination, throttling, malformed responses, role revocation, schema drift, and
ambiguous review-record delivery. Synthetic success is not production acceptance.

The production read provider exposes metadata, allow-listed SuiteQL, and selected record
reads. This historical v11 design allowed a separately injected custom review-record writer; the
current API v13 production composition removes it and fails closed to reviewed exports only.

## Document operations

Document scans are explicit, bounded, incremental through the existing source adapters,
and report `idle | scanning | ready | degraded | unavailable`. Optional Tesseract OCR
preflight verifies the configured JA/EN language packs without downloading software.

Classification review and file execution are separate actions. A reviewed disposition
still cannot change a file until the user selects an approved local destination and
explicitly executes copy or move. Copy remains the default; the existing router enforces
root containment, link rejection, hash verification, and an encrypted operation ledger.

## External release gates

Real NetSuite sandbox mapping and authorization, signed installers, clean-machine OCR,
company rules, and consented human-meeting validation remain external acceptance gates.

## Verified source gates

- Assistant: 517 tests passed, one environment-specific skip; 18/18 deterministic goldens,
  schema synchronization, frontend tests, typecheck, and production build passed.
- Meetily: frontend contract and rendered interaction tests, TypeScript, scoped zero-warning lint,
  the production Next.js build, 301 Rust tests, and one Rust doc-test passed. Eleven hardware or
  artifact-dependent Rust tests remain explicitly ignored. Repository-wide lint still reports
  inherited upstream warnings outside the intelligence scope; they are not suppressed.
