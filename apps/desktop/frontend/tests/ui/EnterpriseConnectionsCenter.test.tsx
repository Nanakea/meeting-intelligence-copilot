import { expect, test, vi } from 'vitest';

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn() }));
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn() }));

import { buildEnterpriseConnectorDefinition } from '@/components/EnterpriseConnectionsCenter';

test('builds an SFTP definition from the separately entered pinned host', () => {
  const definition = buildEnterpriseConnectorDefinition({
    connectorId: 'company-sftp',
    kind: 'sftp',
    displayName: 'Partner drop',
    sourceAuthority: 50,
    baseUrl: 'sftp.company.example:2222',
    gatewayUrl: 'https://gateway.company.example',
    clientId: 'readonly-user',
    approvedValues: '/incoming,/archive',
    hostKey: 'SHA256:trusted-host-key',
  });

  expect(definition.configuration).toEqual({
    kind: 'sftp',
    host: 'sftp.company.example',
    port: 2222,
    username: 'readonly-user',
    host_key_sha256: 'SHA256:trusted-host-key',
    approved_roots: ['/incoming', '/archive'],
    gateway_url: 'https://gateway.company.example',
  });
});
