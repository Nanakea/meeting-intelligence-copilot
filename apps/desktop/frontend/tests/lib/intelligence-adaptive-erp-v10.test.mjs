import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dashboardUrl = new URL(
  '../../src/components/MeetingDetails/SolutionLeadDashboardDialog.tsx',
  import.meta.url,
);
const workflowUrl = new URL(
  '../../src/components/MeetingDetails/ImprovementWorkflowCard.tsx',
  import.meta.url,
);
const documentsUrl = new URL(
  '../../src/components/MeetingDetails/DocumentOperationsPanel.tsx',
  import.meta.url,
);
const netsuiteUrl = new URL(
  '../../src/components/MeetingDetails/NetSuiteGovernancePanel.tsx',
  import.meta.url,
);
const serviceUrl = new URL('../../src/services/assuranceService.ts', import.meta.url);
const contractsUrl = new URL('../../src/services/intelligenceContracts.ts', import.meta.url);
const rustUrl = new URL(
  '../../src-tauri/src/meeting_intelligence_sidecar.rs',
  import.meta.url,
);

test('v10 improvement remains supervised, revisioned, and NetSuite-safe', async () => {
  const [dashboard, workflow, documents, netsuite, service, contracts, rust] = await Promise.all([
    readFile(dashboardUrl, 'utf8'),
    readFile(workflowUrl, 'utf8'),
    readFile(documentsUrl, 'utf8'),
    readFile(netsuiteUrl, 'utf8'),
    readFile(serviceUrl, 'utf8'),
    readFile(contractsUrl, 'utf8'),
    readFile(rustUrl, 'utf8'),
  ]);

  assert.match(service, /\/improvements\/proposals/);
  assert.match(service, /expected_revision: proposal\.revision/);
  assert.match(service, /\/improvements\/profiles/);
  assert.match(service, /\/documents\/inbox/);
  assert.match(service, /routing_action: 'copy'/);
  assert.match(service, /\/erp\/governance\/runs/);
  assert.match(dashboard, /Supervised improvement/);
  assert.match(workflow, /Approve shadow cycle/);
  assert.match(documents, /Review never moves a file automatically/);
  assert.match(netsuite, /No operational NetSuite record is changed/);
  assert.match(contracts, /ImprovementProposalStatus/);
  assert.match(contracts, /ERPGovernanceDashboard/);
  assert.match(rust, /const EXPECTED_API_VERSION: u32 = 13/);
  assert.doesNotMatch(
    dashboard,
    />\s*\{(?:proposal|profile|classification|disposition|recommendation)\.(?:proposal_id|profile_id|classification_id|disposition_id|recommendation_id)\}/,
  );
  assert.doesNotMatch(service, /console\./);
});
