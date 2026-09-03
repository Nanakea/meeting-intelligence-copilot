# Repository Operations

## Repository role

`Nanakea/meeting-intelligence-copilot` is the public product/backend source of truth. The
Meetily desktop integration remains a separate repository derived from upstream Meetily. Never
push Meetily changes to its public upstream remote unless they are intentionally prepared for
upstream contribution.

## Access policy

- Keep repository visibility `PUBLIC` only while tracked-secret and privacy scans remain green.
- Grant access to named users only after confirming their need and company policy.
- Require multi-factor authentication on every account with access.
- Use least-privilege GitHub Apps and deploy keys; do not use personal tokens in repository files.
- Do not grant Actions write permissions unless a reviewed release workflow requires them.
- Review collaborators, deploy keys, webhooks, Actions secrets, and installed apps monthly.

## Branch and review policy

- `main` is the stable source branch.
- Development uses short-lived `codex/*` or reviewed feature branches.
- Require a pull request, passing `windows-ci`, resolved conversations, and one approval before
  merging when another reviewer is available.
- Do not force-push or delete release-evidence branches after artifacts have been distributed.
- Enable automatic deletion of merged feature branches.

For a single-owner preparation period, direct emergency work may occur on a feature branch, but the
full gate and a self-review diff are still required before updating `main`.

## Source and artifact separation

Git stores source, lock files, schemas, synthetic fixtures, and documentation. It must not store
sidecar executables, installers, model weights, audio, databases, indexes, caches, real transcripts,
company documents, source exports, credentials, private keys, signing material, or capability
tokens.

Release artifacts belong in a separately controlled private release location only after hashes,
signatures, Defender results, and acceptance evidence are bound to exact source commits.

## Backup and recovery

1. Push reviewed source branches to the GitHub repository.
2. Keep an encrypted offline Git bundle or mirror on a separate device for disaster recovery.
3. Store signing keys and connector credentials outside GitHub and repository backups.
4. Test restoration into a clean directory quarterly.
5. Re-run `scripts/check.ps1` after restoration before trusting the workspace.

Example encrypted-backup input:

```powershell
git bundle create meeting-intelligence-copilot.bundle --all
Get-FileHash meeting-intelligence-copilot.bundle -Algorithm SHA256
```

Encrypt and move the bundle using an approved tool; do not commit it.

## Before every push

```powershell
git status --short
git diff --check
git diff --cached --stat
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Confirm the remote before pushing:

```powershell
git remote -v
gh repo view Nanakea/meeting-intelligence-copilot --json visibility,url
```

Expected visibility is `PUBLIC`.

## Release boundary

Source tests do not authorize company data or production use. A release additionally requires a
provenance-bound backend/installer rebuild, signing, clean-machine lifecycle, real non-production
tenant acceptance, consent workflow approval, and supervised JA/EN human validation.
