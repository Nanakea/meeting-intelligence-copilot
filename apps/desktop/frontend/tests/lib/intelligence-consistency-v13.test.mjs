import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const service = readFileSync(
  new URL('../../src/services/assuranceService.ts', import.meta.url),
  'utf8',
);
const panel = readFileSync(
  new URL('../../src/components/MeetingDetails/SystemDocumentationConsistency.tsx', import.meta.url),
  'utf8',
);
const dialog = readFileSync(
  new URL('../../src/components/MeetingDetails/SolutionLeadDashboardDialog.tsx', import.meta.url),
  'utf8',
);
const sidecar = readFileSync(
  new URL('../../src-tauri/src/meeting_intelligence_sidecar.rs', import.meta.url),
  'utf8',
);

test('v13 keeps consistency review two-sided, revision-safe, and export-only', () => {
  assert.match(sidecar, /EXPECTED_API_VERSION: u32 = 13/);
  assert.match(service, /\/consistency\/runs/);
  assert.match(service, /expected_revision: finding\.revision/);
  assert.match(service, /\/consistency\/authority-policies/);
  assert.match(service, /\/consistency\/findings\/\$\{encodeURIComponent\(finding\.finding_id\)\}\/export/);
  assert.doesNotMatch(service, /\/consistency\/[\s\S]{0,120}\/(submit|send|execute)/);
  assert.match(panel, /Documentation states/);
  assert.match(panel, /System exposes/);
  assert.match(panel, /Neither side wins by default/);
  assert.match(panel, /Refresh authorization to view/);
  assert.doesNotMatch(panel, /observed_value_sha256\}/);
  assert.match(dialog, /SystemDocumentationConsistency/);
});
