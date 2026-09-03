# Signed JA/EN Team Pilot Operations

This runbook is for the single-user Windows 11 x64 `0.6.0` pilot. It does not authorize public
distribution. The application remains local-first; configured Microsoft 365 and Dynamics 365
connectors contact only those source systems and never upload transcripts.

## Release custody

- Build only from frozen, clean `codex/release-0.6-pilot` branches. Keep Meetily build output under
  `$env:CARGO_TARGET_DIR`.
- Store the Authenticode private key in an organization-controlled signing service or hardware-backed
  certificate store. Do not export it into either repository, environment files, or manifests.
- Sign the backend sidecar, helper executables, `meetily.exe`, NSIS installer, and MSI. Hash and scan
  the signed bytes; signing changes artifact hashes.
- Distribute the exact manifest-bound installers manually. Automatic update checks stay disabled for
  the pilot. A rollback uses the preceding signed, manifest-bound installer.
- Before company access is available, follow `docs/FIRST_WORKDAY_SETUP.md` and run
  `scripts/check-precompany-readiness.ps1`. A pre-company pass is preparation evidence only; it
  never satisfies company policy, signing, tenant, or human-pilot gates.

## Microsoft 365 setup

1. Register a Microsoft Entra public client for delegated device-code authentication. Allow only the
   delegated permissions required to read the signed-in user's OneDrive and selected SharePoint
   drives; do not grant application-wide or write permissions.
2. Enter the tenant and client identifiers in Connected context. These identifiers are configuration,
   not credentials. Complete device-code sign-in as the Windows user who will run Meetily.
3. Leave drive scope empty for the signed-in user's OneDrive only, or add explicit SharePoint drive
   IDs reviewed by the tenant administrator. Do not use tenant-wide discovery in the pilot.
4. Run **Check connection** and confirm `ready`, the selected scope, and a successful timestamp.
   Remote records are queried live and are not written to the encrypted local document index.
5. Revoke the delegated grant during acceptance and verify citations stop immediately and ASK NOW
   continues in meeting-only mode.

## Dynamics 365 setup

1. Create a non-production identity with read-only access to only the accepted entities and projected
   fields. The pilot must not have create, update, delete, workflow, or administrative privileges.
2. Configure the HTTPS Dynamics OData endpoint and store its bearer credential through the connector
   form. Meetily sends the secret to the loopback backend once; the backend stores it in Windows
   Credential Manager and never returns it to the webview.
3. Review versioned entity mappings before activation. Each mapping declares the entity, business
   key, display field, searchable fields, projected fields, and server-filter mode. Start with
   customers, vendors, sales orders, inventory, projects, owners, and system definitions that exist
   in the non-production environment.
4. Run **Check connection** before use. A zero-result check is not healthy unless the configured
   acceptance query is itself expected to have no records.
5. Exercise throttling, expired credentials, ACL removal, circuit opening, recovery, and meeting-only
   fallback. Disconnect must remove the definition and Credential Manager entry.

## Recording operation

- Select Japanese or English explicitly and run recording preflight. Confirm local STT, microphone,
  meeting-output capture, disk space, retention, backend readiness, and the optional spoken pipeline.
- Inform participants before recording and check the confirmation box. Meetily stores only confirmation
  state, application version, and epoch timestamp; it stores no participant identity in that record.
- Intelligence or connector unavailability never blocks recording. ASK NOW remains immediate; connected
  context has a two-second deadline and falls back to meeting-only operation.
- Use aggregate diagnostics for support. Do not request transcript, audio, meeting title, speaker name,
  token, or raw exception exports.

## Acceptance and promotion

- Complete the 90-scenario routed/platform ledger, the four-hour fault soak, accessibility review, clean
  Sandbox lifecycle, Microsoft 365 acceptance, and Dynamics 365 acceptance against the same signed
  executable.
- Run a consented 5-10 user pilot for at least two weeks and 50 usable JA/EN meetings. Record aggregate
  counts only using the schema enforced by `app.evals.team_pilot_release`.
- Generate the final manifest with `scripts/create-team-pilot-release-manifest.py`. It fails unless all
  artifacts have valid Authenticode signatures, Defender evidence covers the exact hashes, connector
  precision is at least 95% with zero unauthorized citations, all 90 scenarios pass, and human-pilot
  thresholds pass.
- Any release fix changes a source commit. Rebuild, resign, rehash, rescan, and repeat all evidence that
  binds to the executable; never edit a completed manifest.

```powershell
apps\api\.venv\Scripts\python.exe scripts\create-team-pilot-release-manifest.py `
  --meetily-root $env:MEETILY_ROOT `
  --backend <signed-backend.exe> `
  --meetily-exe $env:CARGO_TARGET_DIR\release\meetily.exe `
  --nsis $env:CARGO_TARGET_DIR\release\bundle\nsis\meetily_0.6.0_x64-setup.exe `
  --msi $env:CARGO_TARGET_DIR\release\bundle\msi\meetily_0.6.0_x64_en-US.msi `
  --corpus-manifest $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\routed-audio-corpus\MANIFEST.json `
  --acceptance-ledger $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\pilot-acceptance.json `
  --connector-acceptance $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\connector-acceptance.json `
  --human-pilot-summary $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\human-pilot-summary.json `
  --defender-evidence $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\defender-evidence.json `
  --output $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\meetily-0.6.0-release-manifest.json
```

## Removal

Disconnect both remote connectors, use **Delete all local meeting data**, uninstall Meetily, and verify
the application data directory and Credential Manager entries are removed. Explicitly exported files
are outside Meetily retention and must be removed separately by the user.
