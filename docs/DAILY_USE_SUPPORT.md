# Daily-Use Support

## First Run

Use a trusted internal installer on a Windows 11 x64 test device. Review its hash and current
acceptance status. Select EN, JA, or KO explicitly, install a manifest-verified multilingual Local
Whisper model, select microphone and output capture, review retention, and run First workday readiness.
Run Test transcript to panel with non-sensitive synthetic speech. Korean source-test success is
not routed-audio or natural-human-speech validation.

## Recovery

- No transcript: check capture devices, output levels, selected language, and local model readiness.
- Recording works but no questions: check local backend readiness, use Retry, and wait for recovery.
  Do not disable capability authentication or use a remote backend to bypass an error.
- Search unavailable: continue meeting-only. Check the connection, selected sources, expiry, and
  company approval. A successful connection check does not certify tenant-wide compatibility.
- Notes: save and verify the history entry before closing. Export a reviewed copy before upgrading.
- Support: export aggregate diagnostics only. Do not attach audio, transcripts, tokens, meeting
  titles, raw errors, or company records to a public GitHub issue.

## Permissions and Privacy

Ask IT to approve the device, unsigned/signed build policy, participant notification, capture,
retention, local storage, encryption, and each selected source. Request NetSuite read-only SuiteTalk
metadata/SuiteQL scopes, WMS allow-listed GET endpoints, and FBA read permissions separately.
Notion requires selected shared pages; Slack/Teams require selected spaces; Graph requires selected
OneDrive/SharePoint scope. Never request posting, operational writes, issue submission, or automatic
file movement. Disconnect/purge controls remove local connector data, not remote records.

Remote search is supplemental and does not fill meeting gaps. Company data must not be used before
approval. No application can guarantee zero security risk; keep Windows and approved dependencies
patched, restrict source scopes, and verify revocation behavior.

## Manual Upgrade and Rollback

Stop recording, export any records needed for recovery through the local UI, and retain the previous
installer and its verified manifest. Install only a candidate whose evidence matches its hashes.
Test launch, existing note/history reads, one synthetic meeting, and cleanup before real use.
Rollback only to an accepted version with tested data compatibility; never overwrite a newer local
database with an older schema blindly. Keep backups local and access-controlled. Automatic updates
remain disabled. Uninstall behavior must match the accepted retention/deletion test, not an assumption.

## Editor Maintenance

Tailwind 4 uses its dedicated PostCSS plugin and explicit legacy theme configuration, following
[the official migration guide](https://tailwindcss.com/docs/upgrade-guide). Run real editor tests
and a packaged WebView visual/IME check whenever BlockNote or its editor dependencies change.
