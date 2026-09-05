# First Workday Setup

This guide prepares the local JA/EN/KO question copilot before employment starts, without pretending
that company approval, credentials, or human acceptance already exist. The current compatibility
contract is API v13. The product remains single-user and local-first.

## Before joining

1. Keep the candidate on a personal or dedicated test device and use synthetic or non-sensitive
   material only. **Do not use company data** before the company approves the device, recording
   workflow, retention policy, and build.
2. In Meeting Intelligence Copilot Settings, choose the intended microphone and meeting-output device. Run **First
   workday readiness** once in every language you plan to use: English, Japanese, and/or Korean.
3. Run **Test transcript to panel** for each locally installed STT model. A failed copilot test must
   not prevent local recording, but it must be corrected before relying on ASK NOW.
4. Review the retention setting explicitly: 7/30/90 days or forever. Saved meetings default to
   forever until changed; transient intelligence recovery data is purged on normal stop or after
   its crash-recovery TTL. Confirm BitLocker and Microsoft Defender are enabled.
5. Run the source/artifact audit after every candidate rebuild:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check-precompany-readiness.ps1 `
  -CargoTargetRoot $env:CARGO_TARGET_DIR `
  -EvidenceRoot $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT `
  -OutputPath $env:MEETING_INTELLIGENCE_EVIDENCE_ROOT\readiness-0.6.2.json
```

Run this from the monorepo root. Build outputs default to the external Cargo cache, not a second
desktop repository. A readiness report is an inventory, not proof of audio, Sandbox lifecycle,
or human acceptance. Those require the exact-candidate evidence in the packaged checklist.
`ready_for_company_data` remains false while company gates are pending.

## First day with company IT

1. Confirm the company's recording/consent policy, approved meeting categories, retention limits,
   support owner, and incident-removal procedure. The participant confirmation is a workflow
   control, not legal advice.
2. Install an organization-signed build whose hashes match the immutable release manifest. Do not
   carry forward the unsigned preparation installer onto a company device.
3. Register delegated Microsoft Graph access for the signed-in user's OneDrive and explicitly
   selected SharePoint drives. Do not request application-wide or write permissions.
4. Configure a sandbox NetSuite read-only integration role and certificate, then review selected
   SuiteTalk/SuiteQL mappings. WMS and Amazon FBA require separate approved read-only credentials
   and field mappings. Keep create, update, delete, workflow, and administrative permissions disabled.
   For Notion, share only approved selected pages with the integration; the current connector reads
   page Markdown, not an unrestricted workspace crawl. Test revocation and meeting-only fallback.
5. Ask company IT whether the optional enterprise gateway is approved. Its OIDC application,
   mTLS device certificate, source allow-list, GitHub/GitLab projects, Jira/Confluence spaces,
   Azure DevOps projects, ServiceNow tables, SQL views, SFTP roots, and OpenAPI GET operations must
   be configured by an administrator. Do not enter company credentials into an unapproved build.
6. Revoke each connector during acceptance. Citations must stop immediately while meeting-only ASK
   NOW continues.
7. Run the offline Sandbox lifecycle, Defender scan, signed clean-machine lifecycle, bilingual audio
   paths, accessibility check, and the supervised pilot against the same hashes.
8. In **Solution Lead > System vs Documentation**, select approved documentation separately from
   the code/API/ERP/CMDB sources to observe. Review source revisions and environment before
   confirming any discrepancy. The default classification is neutral until company authority
   policies are approved.

## Before every real meeting

1. Select Japanese, English, or Korean explicitly; do not use auto-detection for the copilot path.
   Korean intelligence requires the signed-manifest multilingual local Whisper provider. Legacy
   Parakeet models are English-only and are not downloaded by this build.
2. Confirm the microphone and meeting-output meters move and the correct local STT model is ready.
3. Inform participants and complete the recording confirmation.
4. Start recording even if connected context is unavailable. Connector failure is nonblocking and
   the copilot falls back to meeting-only questions.
5. Stop recording normally and wait for the final transcript flush. If recovery appears, keep
   recording and use the compact retry action; never expose raw diagnostics in the meeting.

## Support boundary

- Export aggregate diagnostics only. They contain versions and counters, not transcript, audio,
  participant identity, tokens, meeting title, raw exceptions, or development paths.
- External context is supplemental. It cannot fill a meeting gap or become transcript evidence.
- No external write is performed. Issue and action output remains draft/export-only.
- Consistency findings are two-source review records, not meeting evidence. Semantic prose claims
  remain excluded until explicitly confirmed.
- Public distribution and organization-wide deployment remain separate milestones after the team
  pilot passes.
