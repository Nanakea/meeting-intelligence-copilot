import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const panelUrl = new URL(
  '../../src/components/IntelligencePanel/IntelligencePanel.tsx',
  import.meta.url,
);
const serviceUrl = new URL('../../src/services/governanceService.ts', import.meta.url);
const portfolioUrl = new URL(
  '../../src/components/MeetingDetails/GovernancePortfolioDialog.tsx',
  import.meta.url,
);
const projectionUrl = new URL(
  '../../src/services/intelligenceProjection.ts',
  import.meta.url,
);
const sidebarUrl = new URL('../../src/components/Sidebar/index.tsx', import.meta.url);
const pilotUrl = new URL('../../src-tauri/src/meeting_intelligence_pilot.rs', import.meta.url);

test('governance candidates require explicit confirmation and remain separate from summaries', async () => {
  const [panel, service] = await Promise.all([
    readFile(panelUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
  ]);

  assert.match(panel, /not a record until you confirm it/);
  assert.match(panel, /reviewGovernanceCandidate/);
  assert.match(panel, /Confirm record/);
  assert.match(panel, /Dismiss/);
  assert.match(service, /\/governance\/candidates\//);
  assert.doesNotMatch(service, /summary|submit|PATCH/);
});

test('portfolio is revisioned, export-only, and does not render internal identifiers', async () => {
  const [portfolio, service] = await Promise.all([
    readFile(portfolioUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
  ]);

  assert.match(portfolio, /expectedRevision|record\.revision/);
  assert.match(portfolio, /Mark resolved/);
  assert.match(portfolio, /Complete review ZIP/);
  assert.match(service, /export_governance_review_pack/);
  assert.doesNotMatch(portfolio, />\s*\{record\.record_id\}|>\s*\{.*evidence_event_ids/);
  assert.doesNotMatch(service, /jira.*submit|azure.*submit|credential/i);
});

test('same-version governance republish changes the visible snapshot fingerprint', async () => {
  const projection = await readFile(projectionUrl, 'utf8');
  assert.match(projection, /governance_candidates: state\.governance_candidates/);
  assert.match(projection, /governance_alerts: state\.governance_alerts/);
});

test('per-meeting and delete-all privacy controls purge governance separately from live stop', async () => {
  const [service, sidebar, pilot] = await Promise.all([
    readFile(serviceUrl, 'utf8'),
    readFile(sidebarUrl, 'utf8'),
    readFile(pilotUrl, 'utf8'),
  ]);
  assert.match(service, /\/governance\/meetings\//);
  assert.match(sidebar, /deleteGovernanceMeeting\(itemId\)[\s\S]*api_delete_meeting/);
  assert.match(pilot, /delete\(format!\("\{base_url\}\/governance"\)\)/);
  assert.match(pilot, /recovery_deleted && governance_deleted/);
});
