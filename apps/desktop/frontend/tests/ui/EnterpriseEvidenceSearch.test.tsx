import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { EnterpriseEvidenceSearch } from '@/components/EnterpriseEvidenceSearch';

const searchEvidence = vi.fn();
const openExternal = vi.fn();

vi.mock('@/services/contextConnectorService', () => ({
  listContextConnectors: vi.fn(async () => [{
    connector_id: 'internal-github-connector',
    kind: 'github',
    display_name: 'Company GitHub',
    phase: 'ready',
    auth_enabled: true,
    indexed_documents: 4,
  }]),
  searchEnterpriseEvidence: (...args: unknown[]) => searchEvidence(...args),
}));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => openExternal(...args),
}));

beforeEach(() => {
  searchEvidence.mockReset();
  openExternal.mockReset();
});

afterEach(cleanup);

test('renders safe ranked evidence without connector or provider identifiers', async () => {
  searchEvidence.mockResolvedValue({
    status: 'current',
    citations: [{
      citation_id: 'a'.repeat(24),
      source_kind: 'github',
      source_label: 'Company GitHub',
      source_reference: 'orders-api@a1b2c3:src/orders.ts#L12-L24',
      entity_type: 'repository_symbol',
      excerpt: 'Currency must match the subsidiary code list.',
      uri: 'https://github.example/orders/src/orders.ts',
      relation: 'reference',
      freshness: 'current',
      retrieved_at: '2026-08-31T00:00:00Z',
      source_updated_at: '2026-08-30T00:00:00Z',
      rank: 1,
      score_reasons: ['lexical_match', 'source_acl'],
      access_expires_at: '2026-08-31T00:15:00Z',
      local_indexed: true,
      not_stated_in_meeting: true,
    }],
    unavailable_connector_ids: [],
    expired_lease_connector_ids: [],
    elapsed_ms: 42,
  });
  const user = userEvent.setup();
  render(<EnterpriseEvidenceSearch />);
  await user.type(screen.getByLabelText('Search enterprise evidence'), 'currency rule');
  await user.click(screen.getByRole('button', { name: 'Search evidence' }));
  expect(await screen.findByText(/orders-api@a1b2c3/)).toBeInTheDocument();
  expect(screen.getByText('Not stated in this meeting')).toBeInTheDocument();
  expect(screen.getByText(/matched your words, authorized source/)).toBeInTheDocument();
  expect(screen.queryByText('internal-github-connector')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /Open confirmed source host/ }));
  expect(openExternal).toHaveBeenCalledWith('open_external_url', {
    url: 'https://github.example/orders/src/orders.ts',
  });
});

test('shows lease expiry without leaking backend identifiers or errors', async () => {
  searchEvidence.mockResolvedValue({
    status: 'partial',
    citations: [],
    unavailable_connector_ids: ['internal-github-connector'],
    expired_lease_connector_ids: ['internal-github-connector'],
    elapsed_ms: 4,
  });
  const user = userEvent.setup();
  render(<EnterpriseEvidenceSearch />);
  await user.type(screen.getByLabelText('Search enterprise evidence'), 'order');
  await user.click(screen.getByRole('button', { name: 'Search evidence' }));
  expect(await screen.findByText(/Some sources require authorization renewal/)).toBeInTheDocument();
  expect(screen.queryByText('internal-github-connector')).not.toBeInTheDocument();
});
