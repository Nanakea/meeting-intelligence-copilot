import type { ConnectorHealth } from '@/services/intelligenceContracts';

export function connectorReadinessCopy(health: ConnectorHealth): string {
  if (health.detail_code === 'access_lease_expired') return 'Access expired; renew authorization';
  if (health.phase === 'disconnected') return 'Disabled / disconnected';
  if (health.phase === 'auth_required') return 'Configured; authorization required';
  if (health.phase === 'connecting') return 'Checking selected sources';
  if (health.phase === 'degraded') return 'Partially available; continue meeting-only if needed';
  if (health.phase === 'ready') return 'Connection available; selected-source validation still required';
  return 'Unavailable; meeting-only intelligence remains available';
}

export function ConnectorReadiness({ health }: { health: ConnectorHealth }) {
  const checked = health.last_success_at ? new Date(health.last_success_at) : null;
  return <div className="mt-1 space-y-1 text-xs text-gray-600" role="status">
    <p>{connectorReadinessCopy(health)}</p>
    <p>Source/simulator-tested capability. Company tenant acceptance is not certified by a connection check.</p>
    {checked && Number.isFinite(checked.getTime()) && <p>Last successful access: <time dateTime={checked.toISOString()}>{checked.toLocaleString()}</time></p>}
    <p>Read-only external access. Retrieved content was not stated in this meeting.</p>
  </div>;
}
