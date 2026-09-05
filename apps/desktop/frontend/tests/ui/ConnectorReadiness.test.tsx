import React from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ConnectorReadiness } from '@/components/ConnectorReadiness';
import type { ConnectorHealth } from '@/services/intelligenceContracts';

afterEach(cleanup);
const health: ConnectorHealth = {
  connector_id: 'private-connector', kind: 'notion', display_name: 'Knowledge',
  phase: 'ready', auth_enabled: true, indexed_documents: 0,
};
it('does not equate a successful check with tenant acceptance', () => {
  render(<ConnectorReadiness health={health} />);
  expect(screen.getByText(/selected-source validation still required/)).toBeInTheDocument();
  expect(screen.getByText(/tenant acceptance is not certified/)).toBeInTheDocument();
});
it('shows expiration without displaying internal detail or identifiers', () => {
  render(<ConnectorReadiness health={{ ...health, phase: 'auth_required', detail_code: 'access_lease_expired' }} />);
  expect(screen.getByText(/Access expired/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain('private-connector');
  expect(document.body.textContent).not.toContain('access_lease_expired');
});
it('renders only fixed copy for unavailable and malformed diagnostics', () => {
  render(<ConnectorReadiness health={{ ...health, phase: 'unavailable', detail_code: 'SECRET exception', last_success_at: 'PRIVATE_PATH' }} />);
  expect(screen.getByText(/Unavailable; meeting-only/)).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/SECRET|PRIVATE_PATH/);
});
