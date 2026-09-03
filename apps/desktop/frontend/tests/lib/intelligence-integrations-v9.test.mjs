import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dashboardUrl = new URL(
  '../../src/components/MeetingDetails/SolutionLeadDashboardDialog.tsx',
  import.meta.url,
);
const serviceUrl = new URL('../../src/services/assuranceService.ts', import.meta.url);
const connectorServiceUrl = new URL(
  '../../src/services/contextConnectorService.ts',
  import.meta.url,
);
const rustUrl = new URL('../../src-tauri/src/lib.rs', import.meta.url);

test('v9 NetSuite and collaboration workflows remain reviewed and scoped', async () => {
  const [dashboard, service, connectorService, rust] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
    readFile(connectorServiceUrl, 'utf8'),
    readFile(rustUrl, 'utf8'),
  ]);

  assert.match(service, /\/data-quality\/runs/);
  assert.match(service, /\/external-actions/);
  assert.match(service, /expected_revision: action\.revision/);
  assert.match(service, /\/notification-policies/);
  assert.match(service, /\/document-routing\/proposals/);
  assert.match(dashboard, /NetSuite anomaly inbox/);
  assert.match(dashboard, /Reviewed handoff only/);
  assert.match(dashboard, /Export review ZIP/);
  assert.match(service, /\/external-actions\/\$\{encodeURIComponent\(action\.action_id\)\}\/export/);
  assert.doesNotMatch(dashboard, /Approve delivery/);
  assert.doesNotMatch(dashboard, /Enable policy/);
  assert.match(dashboard, /Document routing review/);
  assert.match(connectorService, /meeting-intelligence:\/\/oauth\/slack/);
  assert.doesNotMatch(connectorService, /\/slack\/bot-credential/);
  assert.match(connectorService, /\/spaces/);
  assert.doesNotMatch(dashboard, />\s*\{(?:finding|action|proposal)\.(?:finding_id|action_id|proposal_id)\}/);
  assert.doesNotMatch(connectorService, /console\./);
  assert.match(rust, /meeting-intelligence-slack-callback/);
  assert.doesNotMatch(rust, /Second app instance requested with args:/);
});
