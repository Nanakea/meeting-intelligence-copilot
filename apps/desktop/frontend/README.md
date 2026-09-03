# Desktop Frontend

Next.js UI and Tauri/Rust host for Meeting Intelligence Copilot. Run commands from this directory:

```powershell
pnpm install --frozen-lockfile
pnpm test
pnpm exec tsc --noEmit
pnpm build
```

The UI renders backend contracts, manages local recording and retention, and configures approved
read-only evidence sources. Do not hand-edit a wire contract without updating the backend schema
and schema-sync checks.

The containing desktop component is Meetily-derived and covered by
[`../LICENSE.md`](../LICENSE.md). See [`../NOTICE.md`](../NOTICE.md).
