import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const serviceUrl = new URL('../../src/services/assuranceService.ts', import.meta.url);
const dashboardUrl = new URL(
  '../../src/components/MeetingDetails/SolutionLeadDashboardDialog.tsx',
  import.meta.url,
);
const netsuiteUrl = new URL(
  '../../src/components/MeetingDetails/NetSuiteGovernancePanel.tsx',
  import.meta.url,
);
const sidecarUrl = new URL(
  '../../src-tauri/src/meeting_intelligence_sidecar.rs',
  import.meta.url,
);

test('v11 uses measured shadow cycles and explicit document execution', async () => {
  const [service, dashboard, netsuite, sidecar] = await Promise.all([
    readFile(serviceUrl, 'utf8'),
    readFile(dashboardUrl, 'utf8'),
    readFile(netsuiteUrl, 'utf8'),
    readFile(sidecarUrl, 'utf8'),
  ]);
  assert.match(service, /\/shadow-runs/);
  assert.match(service, /\/documents\/scan/);
  assert.match(service, /\/documents\/dispositions\/.*\/execute/);
  assert.match(service, /\/erp\/governance\/mappings\/validate/);
  assert.match(dashboard, /three measured shadow cycles/);
  assert.match(netsuite, /Validate mappings/);
  assert.match(sidecar, /EXPECTED_API_VERSION: u32 = 13/);
  assert.doesNotMatch(service, /console\./);
});
