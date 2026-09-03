# Security Policy

## Supported status

Only the latest commit on the public `main` branch and its explicitly recorded release candidate
are eligible for security fixes. Historical package hashes in `docs/` are evidence, not supported
downloads.

This source is an internal candidate. Signing, real-tenant acceptance, and a consented human pilot
remain release gates.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting for this repository or
contact the repository owner through an approved private channel.

Include only the minimum reproduction needed. Do not attach real transcripts, audio, company
documents, credentials, access tokens, ERP records, or collaboration messages. Replace sensitive
values with synthetic equivalents and identify the affected commit and API compatibility version.

## Secrets and sensitive data

The following must never be committed or uploaded to Actions artifacts:

- Meeting audio, real transcripts, participant names, or meeting titles
- Company documents, source exports, ERP records, chat messages, or retrieval excerpts
- OAuth tokens, capability tokens, passwords, client secrets, certificates, or private keys
- Credential Manager or DPAPI exports, SQLite/WAL files, indexes, caches, or diagnostics with content
- Model weights, packaged executables, installers, crash dumps, or unrestricted operational logs

Use synthetic fixtures under `evals/` and bounded aggregate diagnostics only. Credentials belong in
Windows Credential Manager or the approved enterprise gateway secret store. Local encrypted data is
still protected only within the current Windows user/device threat boundary.

## Security invariants

- Meeting intelligence binds to loopback and uses capability authentication in managed mode.
- The pure domain and service layers do not import platform, FastAPI, connector, or model adapters.
- External context never becomes transcript evidence or authoritative meeting state.
- Remote connector access is selected, ACL-aware, lease-bounded, and read-only.
- Direct external writes and operational ERP mutations fail closed.
- Optional local semantic extraction is review-gated and cannot mutate state automatically.
- Revocation and deletion must purge scoped local data without broad filesystem operations.

Any change to authentication, connector scopes, retention, deletion, encryption, logging, or
external-action policy requires focused tests and a security review before merge.

## Release response

For a confirmed issue:

1. Disable or revoke the affected connector or candidate artifact.
2. Preserve aggregate evidence without copying sensitive content.
3. Fix the root cause and add a regression test.
4. Run `scripts/check.ps1` and the applicable Meetily/package gates.
5. Rebuild every affected artifact; prior hashes and acceptance evidence become invalid.
6. Rotate credentials or certificates when exposure cannot be ruled out.
