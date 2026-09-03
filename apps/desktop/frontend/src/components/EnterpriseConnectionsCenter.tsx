'use client';

import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { Cable, KeyRound, RefreshCw, ShieldOff, Trash2 } from 'lucide-react';

import {
  completeEnterpriseAuthorization,
  deleteEnterpriseSource,
  listContextConnectors,
  listEnterpriseConnectorCatalog,
  listEnterpriseSources,
  refreshEnterpriseLease,
  revokeEnterpriseAccess,
  saveContextConnector,
  saveEnterpriseSource,
  startEnterpriseAuthorization,
} from '@/services/contextConnectorService';
import type {
  ConnectorCatalogEntry,
  ConnectorDefinition,
  ConnectorHealth,
  ConnectorKind,
  SourceSelection,
  SourceSelectionKind,
} from '@/services/intelligenceContracts';

type EnterpriseKind = Extract<ConnectorKind,
  'github' | 'gitlab' | 'jira' | 'confluence' | 'azure_devops' | 'servicenow' | 'sql' | 'sftp' | 'openapi'>;

const SOURCE_KIND: Record<EnterpriseKind, SourceSelectionKind> = {
  github: 'repository',
  gitlab: 'repository',
  jira: 'project',
  confluence: 'space',
  azure_devops: 'project',
  servicenow: 'service',
  sql: 'table',
  sftp: 'folder',
  openapi: 'service',
};

const DEFAULT_ORIGINS: Partial<Record<EnterpriseKind, string>> = {
  github: 'https://api.github.com',
  gitlab: 'https://gitlab.com',
};

function safeHealthCopy(health: ConnectorHealth): string {
  if (health.phase === 'ready') return 'Authorized and ready';
  if (health.phase === 'auth_required') return 'Authorization or lease renewal required';
  if (health.phase === 'degraded') return 'Partially available; meeting intelligence is unaffected';
  if (health.phase === 'connecting') return 'Synchronizing selected sources';
  if (health.phase === 'unavailable') return 'Unavailable; use meeting-only intelligence';
  return 'Not connected';
}

export function buildEnterpriseConnectorDefinition(values: {
  connectorId: string;
  kind: EnterpriseKind;
  displayName: string;
  sourceAuthority: number;
  baseUrl: string;
  gatewayUrl: string;
  clientId: string;
  approvedValues: string;
  hostKey: string;
}): ConnectorDefinition {
  const approved = values.approvedValues.split(',').map((value) => value.trim()).filter(Boolean);
  const gatewayUrl = values.gatewayUrl.trim() || null;
  let configuration: ConnectorDefinition['configuration'];
  if (values.kind === 'github' || values.kind === 'gitlab') {
    configuration = {
      kind: values.kind,
      base_url: values.baseUrl,
      client_id: values.clientId,
      gateway_url: gatewayUrl,
      selected_source_ids: [],
      include_code: true,
      include_delivery_data: true,
    };
  } else if (['jira', 'confluence', 'azure_devops', 'servicenow'].includes(values.kind)) {
    configuration = {
      kind: values.kind as 'jira' | 'confluence' | 'azure_devops' | 'servicenow',
      base_url: values.baseUrl,
      client_id: values.clientId,
      gateway_url: gatewayUrl,
      selected_source_ids: [],
    };
  } else if (values.kind === 'sql') {
    configuration = {
      kind: 'sql',
      server_label: values.displayName,
      approved_views: approved,
      query_template_ids: [],
      gateway_url: gatewayUrl,
    };
  } else if (values.kind === 'sftp') {
    const parsed = new URL(`ssh://${values.baseUrl}`);
    configuration = {
      kind: 'sftp',
      host: parsed.hostname,
      port: parsed.port ? Number(parsed.port) : 22,
      username: values.clientId,
      host_key_sha256: values.hostKey,
      approved_roots: approved,
      gateway_url: gatewayUrl,
    };
  } else {
    configuration = {
      kind: 'openapi',
      base_url: values.baseUrl,
      specification_sha256: values.hostKey,
      approved_operation_ids: approved,
      gateway_url: gatewayUrl,
    };
  }
  return {
    connector_id: values.connectorId,
    kind: values.kind,
    display_name: values.displayName,
    source_authority: values.sourceAuthority,
    principal_id: 'current-windows-user',
    configuration,
  };
}

export function EnterpriseConnectionsCenter() {
  const [catalog, setCatalog] = useState<ConnectorCatalogEntry[]>([]);
  const [connectors, setConnectors] = useState<ConnectorHealth[]>([]);
  const [kind, setKind] = useState<EnterpriseKind>('github');
  const [connectorId, setConnectorId] = useState('company-github');
  const [displayName, setDisplayName] = useState('Company GitHub');
  const [sourceAuthority, setSourceAuthority] = useState(60);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_ORIGINS.github ?? '');
  const [gatewayUrl, setGatewayUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [approvedValues, setApprovedValues] = useState('');
  const [hostKey, setHostKey] = useState('');
  const [selectedConnector, setSelectedConnector] = useState('');
  const [sources, setSources] = useState<SourceSelection[]>([]);
  const [sourceLabel, setSourceLabel] = useState('');
  const [externalSourceId, setExternalSourceId] = useState('');
  const [pendingAuthorization, setPendingAuthorization] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'working' | 'saved' | 'error'>('idle');

  const enterpriseConnectors = connectors.filter((connector) => SOURCE_KIND[connector.kind as EnterpriseKind]);

  const refresh = useCallback(async () => {
    const [nextCatalog, nextConnectors] = await Promise.all([
      listEnterpriseConnectorCatalog().catch(() => []),
      listContextConnectors().catch(() => []),
    ]);
    setCatalog(nextCatalog);
    setConnectors(nextConnectors);
    if (!selectedConnector && nextConnectors.some((connector) => SOURCE_KIND[connector.kind as EnterpriseKind])) {
      setSelectedConnector(nextConnectors.find((connector) => SOURCE_KIND[connector.kind as EnterpriseKind])?.connector_id ?? '');
    }
  }, [selectedConnector]);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!selectedConnector) {
      setSources([]);
      return;
    }
    void listEnterpriseSources(selectedConnector).then(setSources).catch(() => setSources([]));
  }, [selectedConnector]);

  useEffect(() => {
    const unlisten = listen<string>('meeting-intelligence-enterprise-callback', async (event) => {
      if (!pendingAuthorization) return;
      setStatus('working');
      try {
        await completeEnterpriseAuthorization(pendingAuthorization, event.payload);
        setPendingAuthorization(null);
        setStatus('saved');
        await refresh();
      } catch {
        setStatus('error');
      }
    });
    return () => { void unlisten.then((dispose) => dispose()); };
  }, [pendingAuthorization, refresh]);

  const save = async () => {
    setStatus('working');
    try {
      await saveContextConnector(buildEnterpriseConnectorDefinition({
        connectorId,
        kind,
        displayName,
        sourceAuthority,
        baseUrl,
        gatewayUrl,
        clientId,
        approvedValues,
        hostKey,
      }), null);
      setSelectedConnector(connectorId);
      setStatus('saved');
      await refresh();
    } catch {
      setStatus('error');
    }
  };

  const authorize = async (id: string) => {
    setStatus('working');
    try {
      const authorization = await startEnterpriseAuthorization(id);
      setPendingAuthorization(id);
      await invoke('open_external_url', { url: authorization.authorization_url });
      setStatus('idle');
    } catch {
      setPendingAuthorization(null);
      setStatus('error');
    }
  };

  const addSource = async () => {
    const connector = connectors.find((candidate) => candidate.connector_id === selectedConnector);
    if (!connector || !sourceLabel.trim() || !externalSourceId.trim()) return;
    setStatus('working');
    try {
      const selection: SourceSelection = {
        selection_id: `source-${crypto.randomUUID().replaceAll('-', '')}`,
        connector_id: connector.connector_id,
        kind: SOURCE_KIND[connector.kind as EnterpriseKind],
        label: sourceLabel.trim(),
        enabled: true,
      };
      await saveEnterpriseSource(connector.connector_id, selection, externalSourceId.trim());
      setSources(await listEnterpriseSources(connector.connector_id));
      setSourceLabel('');
      setExternalSourceId('');
      setStatus('saved');
    } catch {
      setStatus('error');
    }
  };

  const selectedHealth = connectors.find((connector) => connector.connector_id === selectedConnector);

  return (
    <section className="rounded-2xl border border-slate-300 bg-[linear-gradient(135deg,#f8fafc_0%,#eef6f3_52%,#fff7ed_100%)] p-5" aria-labelledby="enterprise-connections-heading">
      <div className="flex items-start gap-3">
        <Cable className="mt-0.5 h-5 w-5 text-teal-800" aria-hidden="true" />
        <div>
          <h2 id="enterprise-connections-heading" className="font-semibold text-slate-950">Enterprise Connections Center</h2>
          <p className="mt-1 text-sm leading-relaxed text-slate-700">
            Read-only company sources are accessed through the optional mTLS gateway. Meeting audio,
            transcripts, and meeting evidence never leave this device. Authorization leases last 15 minutes.
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 rounded-xl border border-white/80 bg-white/80 p-4 md:grid-cols-2">
        <label className="text-sm font-medium text-slate-700">Source family
          <select value={kind} onChange={(event) => {
            const next = event.target.value as EnterpriseKind;
            setKind(next);
            setBaseUrl(DEFAULT_ORIGINS[next] ?? '');
          }} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
            {catalog.map((entry) => <option key={entry.kind} value={entry.kind}>{entry.label}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium text-slate-700">Connection name
          <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <label className="text-sm font-medium text-slate-700">Local connection key
          <input value={connectorId} onChange={(event) => setConnectorId(event.target.value)} pattern="[a-z0-9][a-z0-9_-]{0,63}" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        <label className="text-sm font-medium text-slate-700">Company gateway HTTPS URL
          <input value={gatewayUrl} onChange={(event) => setGatewayUrl(event.target.value)} placeholder="https://evidence-gateway.company.example" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>
        {!['sql', 'sftp'].includes(kind) && <label className="text-sm font-medium text-slate-700">Provider HTTPS origin
          <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://provider.example" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>}
        {kind === 'sftp' && <label className="text-sm font-medium text-slate-700">Pinned SFTP host
          <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="sftp.company.example:22" autoComplete="off" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>}
        {!['sql', 'openapi'].includes(kind) && <label className="text-sm font-medium text-slate-700">Public client ID / SFTP username
          <input value={clientId} onChange={(event) => setClientId(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>}
        {['sql', 'sftp', 'openapi'].includes(kind) && <label className="text-sm font-medium text-slate-700 md:col-span-2">Approved views, roots, or operation IDs
          <input value={approvedValues} onChange={(event) => setApprovedValues(event.target.value)} placeholder="Comma-separated administrator-approved values" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
        </label>}
        {['sftp', 'openapi'].includes(kind) && <label className="text-sm font-medium text-slate-700 md:col-span-2">{kind === 'sftp' ? 'Pinned host key SHA-256' : 'Validated specification SHA-256'}
          <input value={hostKey} onChange={(event) => setHostKey(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs" />
        </label>}
        <label className="text-sm font-medium text-slate-700 md:col-span-2">Source authority ({sourceAuthority}/100)
          <input type="range" min="0" max="100" step="5" value={sourceAuthority} onChange={(event) => setSourceAuthority(Number(event.target.value))} className="mt-2 w-full accent-teal-800" />
        </label>
        <div className="flex items-center gap-3 md:col-span-2">
          <button type="button" onClick={() => void save()} disabled={status === 'working'} className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">Save read-only connection</button>
          <span role="status" className="text-xs text-slate-600">{status === 'saved' ? 'Saved locally.' : status === 'error' ? 'Operation could not be completed.' : ''}</span>
        </div>
      </div>

      {enterpriseConnectors.length > 0 && <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
        <label className="text-sm font-medium text-slate-700">Manage connection
          <select value={selectedConnector} onChange={(event) => setSelectedConnector(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
            {enterpriseConnectors.map((connector) => <option key={connector.connector_id} value={connector.connector_id}>{connector.display_name}</option>)}
          </select>
        </label>
        {selectedHealth && <p className="mt-2 text-sm text-slate-700" role="status">{safeHealthCopy(selectedHealth)} · {selectedHealth.scope_summary ?? 'No sources selected'}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={() => void authorize(selectedConnector)} className="inline-flex items-center gap-2 rounded-lg border border-teal-700 px-3 py-2 text-sm font-semibold text-teal-900"><KeyRound className="h-4 w-4" aria-hidden="true" />Authorize</button>
          <button type="button" onClick={() => void refreshEnterpriseLease(selectedConnector).then(refresh).catch(() => setStatus('error'))} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-800"><RefreshCw className="h-4 w-4" aria-hidden="true" />Refresh lease</button>
          <button type="button" onClick={() => void revokeEnterpriseAccess(selectedConnector).then(refresh).catch(() => setStatus('error'))} className="inline-flex items-center gap-2 rounded-lg border border-rose-300 px-3 py-2 text-sm font-semibold text-rose-800"><ShieldOff className="h-4 w-4" aria-hidden="true" />Revoke and purge</button>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">Safe source label
            <input value={sourceLabel} onChange={(event) => setSourceLabel(event.target.value)} placeholder="Orders API" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
          </label>
          <label className="text-sm font-medium text-slate-700">Provider source identifier
            <input value={externalSourceId} onChange={(event) => setExternalSourceId(event.target.value)} autoComplete="off" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            <span className="mt-1 block text-xs font-normal text-slate-500">Encrypted locally and not shown after enrollment.</span>
          </label>
        </div>
        <button type="button" onClick={() => void addSource()} className="mt-3 rounded-lg bg-teal-800 px-3 py-2 text-sm font-semibold text-white">Add selected source</button>
        {sources.length > 0 && <ul className="mt-3 space-y-2">{sources.map((source) => <li key={source.selection_id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2"><span className="text-sm text-slate-800">{source.label} · {source.kind}</span><button type="button" title="Remove selected source" onClick={() => void deleteEnterpriseSource(selectedConnector, source.selection_id).then(() => listEnterpriseSources(selectedConnector).then(setSources)).catch(() => setStatus('error'))} className="rounded p-1 text-rose-700"><Trash2 className="h-4 w-4" aria-hidden="true" /></button></li>)}</ul>}
      </div>}
    </section>
  );
}
