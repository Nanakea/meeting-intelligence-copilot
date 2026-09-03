# Meetily Workspace Migration

Set the canonical Meetily development root through `MEETILY_ROOT`. Keep Rust build output outside
the repository through `CARGO_TARGET_DIR`; do not create a repository-local `target` directory in
`$env:MEETILY_ROOT`.

## Verified cutover

- The pre-migration inventory contained 417,567 files and 51,548,048,570 bytes.
- Repository branch, HEAD, porcelain-v2 status, untracked files, ignored files, and worktree
  registrations were captured in a private migration snapshot.
- A final read-only Robocopy mirror audit reported 417,567 files, zero copied files, zero mismatches,
  zero failures, and zero extras. The audit is preserved as `robocopy-verify-final.log` in that
  migration evidence directory.
- `git worktree repair` registered the primary repository and all seven migrated worktrees under
  the configured workspace; their branches, commits, tracked changes, and untracked files matched
  the snapshot.
- `<legacy-workspace>` no longer exists and no compatibility junction was created.

The reversible source quarantine at `<legacy-workspace>-migration-source-20260822` may be removed only after
the full assistant, Meetily frontend, and external-target Rust gates pass from the new paths. Frozen
historical evidence documents may continue to show their original `<legacy-workspace>` paths; executable
scripts and current setup documentation must use `MEETILY_ROOT`.

## Current commands

```powershell
$env:MEETILY_ROOT = "<path-to-meetily-intelligence-desktop>"
$env:CARGO_TARGET_DIR = "<path-to-external-cargo-target>"

cd $env:ASSISTANT_ROOT
powershell -ExecutionPolicy Bypass -File scripts\check.ps1

cd $env:MEETILY_ROOT\frontend
pnpm test
pnpm exec tsc --noEmit
pnpm lint
pnpm build

cd $env:MEETILY_ROOT\frontend\src-tauri
cargo test --locked
```
