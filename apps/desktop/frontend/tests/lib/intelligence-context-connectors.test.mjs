import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const settingsUrl = new URL(
  '../../src/components/ContextConnectorSettings.tsx',
  import.meta.url,
);
const serviceUrl = new URL(
  '../../src/services/contextConnectorService.ts',
  import.meta.url,
);
const panelUrl = new URL(
  '../../src/components/IntelligencePanel/IntelligencePanel.tsx',
  import.meta.url,
);
const hookUrl = new URL('../../src/hooks/useProblemContext.ts', import.meta.url);
const draftDialogUrl = new URL(
  '../../src/components/MeetingDetails/StructuredIssueDraftsDialog.tsx',
  import.meta.url,
);
const draftServiceUrl = new URL(
  '../../src/services/intelligenceIssueDraft.ts',
  import.meta.url,
);

test('connector settings keep secrets ephemeral and describe read-only behavior', async () => {
  const source = await readFile(settingsUrl, 'utf8');

  assert.match(source, /type="password"/);
  assert.match(source, /autoComplete="off"/);
  assert.match(source, /setCredential\(''\)/);
  assert.match(source, /Windows Credential Manager/);
  assert.match(source, /never become\s+meeting evidence or\s+update ERP data/);
  assert.match(source, /including Zoom, Google Meet, and Teams/);
  assert.match(source, /entity_mappings/);
  assert.match(source, /\['odata', 'sap', 'dynamics365', 'wms'\]\.includes\(kind\)/);
  assert.match(source, /drive_ids/);
  assert.match(source, /NetSuite SuiteTalk/);
  assert.match(source, /Selected Microsoft Teams spaces/);
  assert.match(source, /Selected Slack channels/);
  assert.match(source, /Selected Notion pages/);
  assert.match(source, /Warehouse management system \(REST\)/);
  assert.match(source, /Amazon FBA Selling Partner API/);
  assert.match(source, /saveEnterpriseSource/);
  assert.match(source, /Notion page UUID/);
  assert.match(source, /saveConnectorCertificate/);
  assert.match(source, /saveExternalSpace/);
  assert.match(source, /server_filter/);
  assert.match(source, /syncContextConnector\(authorizationConnectorId\)/);
  assert.match(source, /remote records are queried live and are not cached/);
  assert.match(source, /Disconnect all and delete connected data/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|console\.|Slack bot/);
  assert.doesNotMatch(source, /device_code|access_token|refresh_token/);
});

test('context transport has no external write or submit operation', async () => {
  const source = await readFile(serviceUrl, 'utf8');

  assert.match(source, /\/context\/question-context/);
  assert.match(source, /\/context\/connectors/);
  assert.match(source, /method: 'DELETE'/);
  assert.match(source, /\/configuration\/entity-glossary[\s\S]{0,120}method: 'PUT'/);
  assert.doesNotMatch(source, /submit|PATCH/);
  assert.doesNotMatch(source, /console\./);
  assert.doesNotMatch(source, /device_code|access_token|refresh_token/);
});

test('panel labels connector results as external and hides raw record identifiers', async () => {
  const source = await readFile(panelUrl, 'utf8');

  assert.match(source, /External context/);
  assert.match(source, /not a statement made in the meeting/);
  assert.match(source, /citation\.source_label/);
  assert.match(source, /citation\.source_reference/);
  assert.match(source, /citation\.excerpt/);
  assert.match(source, /citation\.source_kind/);
  assert.match(source, /citation\.retrieved_at/);
  assert.match(source, /confirmOpen/);
  assert.match(source, /open_external_url/);
  assert.doesNotMatch(source, /citation\.record_id|citation\.connector_id/);
});

test('connected context has a strict deadline and visible meeting-only fallback', async () => {
  const [hook, panel] = await Promise.all([
    readFile(
      new URL('../../src/hooks/useProblemContext.ts', import.meta.url),
      'utf8',
    ),
    readFile(
      new URL('../../src/components/IntelligencePanel/IntelligencePanel.tsx', import.meta.url),
      'utf8',
    ),
  ]);

  assert.match(hook, /DEADLINE_MS = 2_000/);
  assert.match(hook, /controller\.abort\(\)[\s\S]{0,160}setStatus\('unavailable'\)/);
  assert.match(panel, /problemContext\.status === 'unavailable'/);
  assert.match(panel, /Meeting questions remain available/);
});

test('problem context is bound to authoritative session, problem, and state version', async () => {
  const source = await readFile(hookUrl, 'utf8');

  assert.match(source, /session_id: sessionId/);
  assert.match(source, /pain_id: painId/);
  assert.match(source, /state_version: stateVersion/);
  assert.match(source, /next\.status === 'stale'/);
  assert.match(source, /controller\.abort\(\)/);
  assert.doesNotMatch(source, /searchContext\(/);
});

test('post-meeting identity review is authoritative and always cleanup-bounded', async () => {
  const [dialog, service] = await Promise.all([
    readFile(draftDialogUrl, 'utf8'),
    readFile(draftServiceUrl, 'utf8'),
  ]);

  assert.match(dialog, /Post-meeting problem identity review/);
  assert.match(dialog, /Same problem/);
  assert.match(dialog, /Keep separate/);
  assert.match(dialog, /decideRegeneratedProblemIdentity/);
  assert.match(service, /decideProblemIdentity\(/);
  assert.match(service, /closeIssueDraftRegeneration/);
  assert.match(service, /method: 'DELETE'/);
  assert.doesNotMatch(dialog, /candidate\.(?:first_pain_id|second_pain_id)\}/);
});
