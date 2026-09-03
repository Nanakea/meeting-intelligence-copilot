import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dashboardUrl = new URL(
  '../../src/components/MeetingDetails/SolutionLeadDashboardDialog.tsx',
  import.meta.url,
);
const serviceUrl = new URL('../../src/services/assuranceService.ts', import.meta.url);
const sidecarUrl = new URL(
  '../../src-tauri/src/meeting_intelligence_sidecar.rs',
  import.meta.url,
);
const scheduleUrl = new URL(
  '../../src-tauri/src/meeting_intelligence_assurance.rs',
  import.meta.url,
);

test('v9 assurance is local, authenticated, and review gated', async () => {
  const [dashboard, service, sidecar] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
    readFile(sidecarUrl, 'utf8'),
  ]);

  assert.match(sidecar, /EXPECTED_API_VERSION: u32 = 13/);
  assert.match(service, /buildIntelligenceHeaders/);
  assert.match(service, /\/assurance\/findings\//);
  assert.match(service, /\/assurance\/trusted-keys/);
  assert.match(service, /\/assurance\/rule-packs/);
  assert.match(dashboard, /Unreviewed findings never change meeting truth/);
  assert.match(dashboard, /Confirm finding/);
  assert.match(dashboard, /Verify signed ZIP/);
  assert.match(dashboard, /Ed25519 public key/);
  assert.doesNotMatch(service, /submit|PATCH|access_token|refresh_token/);
  assert.doesNotMatch(dashboard, />\s*\{finding\.finding_id\}/);
});

test('traceability relationships require explicit revision-safe review', async () => {
  const [dashboard, service] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
  ]);

  assert.match(service, /\/solution-thread\/edges\/suggested/);
  assert.match(service, /expected_revision: proposal\.revision/);
  assert.match(dashboard, /Confirm link/);
  assert.match(dashboard, /Not related/);
  assert.match(dashboard, /link\.source_label/);
  assert.match(dashboard, /link\.target_label/);
  assert.doesNotMatch(dashboard, />\s*\{link\.edge_id\}/);
});

test('scheduled assurance uses bounded signed sidecar headless mode', async () => {
  const [service, schedule] = await Promise.all([
    readFile(serviceUrl, 'utf8'),
    readFile(scheduleUrl, 'utf8'),
  ]);

  assert.match(service, /configure_meeting_intelligence_assurance_schedule/);
  assert.match(service, /runIfMissed: saved\.run_if_missed/);
  assert.match(schedule, /--run-scheduled-assurance/);
  assert.match(schedule, /schtasks/);
  assert.match(schedule, /CREATE/);
  assert.match(schedule, /\/Delete/);
  assert.match(schedule, /StartWhenAvailable/);
});
