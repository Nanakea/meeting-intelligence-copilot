import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { SystemDocumentationConsistency } from '@/components/MeetingDetails/SystemDocumentationConsistency';

const runConsistencyCheck = vi.fn();
const reviewConsistencyFinding = vi.fn();
const reviewConsistencyClaim = vi.fn();

const finding = {
  finding_id: 'internal-finding-id',
  fingerprint: 'f'.repeat(64),
  run_id: 'r'.repeat(64),
  mismatch_kind: 'type_mismatch',
  severity: 'high',
  status: 'open',
  subject: 'customer',
  property_name: 'field.email.type',
  document_claim: {
    claim_id: 'internal-claim-id',
    fingerprint: 'c'.repeat(64),
    canonical_subject: 'customer',
    canonical_property: 'field.email.type',
    expected_value: 'String',
    expected_value_sha256: 'a'.repeat(64),
    value_type: 'type',
    language: 'en',
    document_kind: 'interface',
    origin: 'deterministic',
    status: 'confirmed',
    confidence: 1,
    impact_flags: ['production'],
    citation: {
      connector_id: 'private-doc-connector',
      source_kind: 'local_files',
      source_label: 'Approved design',
      source_reference: 'Order interface.md',
      source_revision: 'abc123',
      locator: { page: 4 },
      retrieved_at: '2026-08-31T00:00:00Z',
      freshness: 'current',
    },
    revision: 1,
    created_at: '2026-08-31T00:00:00Z',
  },
  observed_fact: {
    fact_id: 'internal-observed-id',
    canonical_subject: 'customer',
    canonical_property: 'field.email.type',
    observed_value: 'Integer',
    observed_value_sha256: 'b'.repeat(64),
    value_type: 'type',
    persistence: 'metadata_safe',
    scope_complete: true,
    citation: {
      connector_id: 'private-system-connector',
      source_kind: 'netsuite',
      source_label: 'NetSuite production',
      source_reference: 'Customer metadata',
      source_revision: '2026.2',
      environment: 'production',
      retrieved_at: '2026-08-31T00:00:00Z',
      freshness: 'current',
      locator: {},
    },
  },
  comparison: {
    expected_value: 'String',
    expected_value_sha256: 'a'.repeat(64),
    observed_value: 'Integer',
    observed_value_sha256: 'b'.repeat(64),
    comparison_rule: 'type_mismatch',
    attribution: 'neutral',
    explanation: "Documentation states 'String', while the selected system exposes 'Integer'.",
    likely_impact: 'The integration mapping may fail.',
    recommended_verification: 'Verify both revisions and the production environment.',
  },
  affected_solution_node_ids: [],
  revision: 1,
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
};

vi.mock('@/services/assuranceService', () => ({
  getConsistencyDashboard: vi.fn(async () => ({
    generated_at: '2026-08-31T00:00:00Z',
    open_findings: 1,
    confirmed_findings: 0,
    approved_exceptions: 0,
    ambiguous_findings: 0,
    by_mismatch_kind: { type_mismatch: 1 },
    by_severity: { high: 1 },
    by_attribution: { neutral: 1 },
    proposed_semantic_claims: [],
    recent_findings: [finding],
  })),
  getConsistencyFindings: vi.fn(async () => [finding]),
  getAuthorityPolicies: vi.fn(async () => []),
  runConsistencyCheck: (...args: unknown[]) => runConsistencyCheck(...args),
  reviewConsistencyFinding: (...args: unknown[]) => reviewConsistencyFinding(...args),
  reviewConsistencyClaim: (...args: unknown[]) => reviewConsistencyClaim(...args),
  saveAuthorityPolicy: vi.fn(),
  exportConsistencyFinding: vi.fn(),
}));

vi.mock('@/services/contextConnectorService', () => ({
  listContextConnectors: vi.fn(async () => [
    {
      connector_id: 'private-doc-connector',
      kind: 'local_files',
      display_name: 'Approved design library',
      phase: 'ready',
      auth_enabled: false,
      indexed_documents: 3,
    },
    {
      connector_id: 'private-system-connector',
      kind: 'netsuite',
      display_name: 'NetSuite production',
      phase: 'ready',
      auth_enabled: true,
      indexed_documents: 0,
    },
  ]),
}));

beforeEach(() => {
  runConsistencyCheck.mockReset();
  reviewConsistencyFinding.mockReset();
  reviewConsistencyClaim.mockReset();
  runConsistencyCheck.mockResolvedValue({ finding_count: 1, status: 'completed' });
  reviewConsistencyFinding.mockResolvedValue({ ...finding, status: 'confirmed', revision: 2 });
});

afterEach(cleanup);

test('renders exact two-sided discrepancy evidence without internal identifiers', async () => {
  render(<SystemDocumentationConsistency isJapanese={false} />);
  expect(await screen.findByText('Documentation states')).toBeInTheDocument();
  expect(screen.getByText('String')).toBeInTheDocument();
  expect(screen.getByText('Integer')).toBeInTheDocument();
  expect(screen.getByText(/Order interface\.md/)).toBeInTheDocument();
  expect(screen.getAllByText(/NetSuite production/).length).toBeGreaterThan(0);
  expect(screen.getByText(/Neither side wins by default/)).toBeInTheDocument();
  expect(screen.queryByText('internal-finding-id')).not.toBeInTheDocument();
  expect(screen.queryByText('private-doc-connector')).not.toBeInTheDocument();
  expect(screen.queryByText('private-system-connector')).not.toBeInTheDocument();
  expect(screen.queryByText('a'.repeat(64))).not.toBeInTheDocument();
});

test('runs selected sources and submits revision-safe review', async () => {
  const user = userEvent.setup();
  render(<SystemDocumentationConsistency isJapanese={false} />);
  await screen.findByText('Approved design library');
  await user.click(screen.getByRole('button', { name: 'Run comparison' }));
  await waitFor(() => expect(runConsistencyCheck).toHaveBeenCalledWith({
    document_connector_ids: ['private-doc-connector'],
    observed_connector_ids: ['private-system-connector'],
    language: 'en',
    environment: undefined,
  }));
  await user.click(screen.getByRole('button', { name: 'Confirm discrepancy' }));
  await waitFor(() => expect(reviewConsistencyFinding).toHaveBeenCalledWith(
    expect.objectContaining({ finding_id: 'internal-finding-id', revision: 1 }),
    { action: 'confirm' },
  ));
});

test('assigns a safe role and requests a source refresh without rendering source ids', async () => {
  const user = userEvent.setup();
  render(<SystemDocumentationConsistency isJapanese={false} />);
  await screen.findByText('Documentation states');
  await user.type(screen.getByLabelText('Owner role or team'), 'ERP platform team');
  await user.click(screen.getByRole('button', { name: 'Save owner' }));
  await waitFor(() => expect(reviewConsistencyFinding).toHaveBeenCalledWith(
    expect.objectContaining({ finding_id: 'internal-finding-id', revision: 1 }),
    { action: 'assign', owner_role: 'ERP platform team' },
  ));
  await user.click(screen.getByRole('button', { name: 'Request refresh' }));
  await waitFor(() => expect(reviewConsistencyFinding).toHaveBeenCalledWith(
    expect.objectContaining({ finding_id: 'internal-finding-id', revision: 1 }),
    { action: 'request_refresh' },
  ));
  expect(screen.queryByText('private-system-connector')).not.toBeInTheDocument();
});
