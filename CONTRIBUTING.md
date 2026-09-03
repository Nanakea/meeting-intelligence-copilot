# Contributing

Changes require repository-owner approval and must preserve the
local-first, evidence-backed question-copilot contract.

## Before changing domain logic

Read `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md`, and
`docs/PRIVACY_AND_SAFETY.md`.

## Required invariants

- Every pain point and meeting fact cites real transcript event IDs.
- Evidence and inference remain distinct.
- Superseded and contradicted facts are retained.
- Gaps reopen only through explicit fact invalidation or contradiction.
- At most one ASK NOW and two FOLLOW UP questions are active.
- Domain and service code remains free of platform, vendor, network, database, and FastAPI imports.
- External context, assurance findings, and semantic proposals cannot mutate `MeetingState`.
- No cloud AI, direct external writes, or new credential scope is added without explicit approval.

## Workflow

1. Branch from `main` using `codex/<short-purpose>` or another reviewed work branch.
2. Keep commits scoped. Do not combine product changes with generated packages or local data.
3. Update backend and TypeScript contracts together when a wire shape changes.
4. Add deterministic tests for state transitions, stale revisions, negative cases, and privacy.
5. Run the full gate before requesting review:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Meetily integration changes additionally require its frontend tests, TypeScript, production build,
and Rust tests from the clean distribution worktree with the external Cargo target directory.

## Pull request checklist

- [ ] The change has a concrete user or reliability outcome.
- [ ] Evidence provenance and historical lifecycle are preserved.
- [ ] New I/O is behind an adapter port.
- [ ] Sensitive data is absent from logs, fixtures, snapshots, and exports.
- [ ] Schema files are regenerated and synchronized when contracts change.
- [ ] Regression and negative tests cover the behavior.
- [ ] `scripts/check.ps1` passes.
- [ ] Package/tenant/human gates are not claimed unless their evidence exists for this commit.

## Files that must stay local

Do not stage `.claude` session state, local plans, `.env` files, credentials, models, databases,
recordings, transcripts, `dist/`, `out/`, `tmp/`, or generated installers. Inspect `git status` and
run a secret scan before every push.
