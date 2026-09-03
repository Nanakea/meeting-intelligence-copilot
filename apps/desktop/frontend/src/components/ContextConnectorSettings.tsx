'use client';

import { useEffect, useState } from 'react';
import { Database, FileText, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

import {
  deleteAllContextConnectors,
  deleteContextConnector,
  getEntityGlossary,
  listContextConnectors,
  pollMicrosoftDeviceCode,
  completeSlackAuthorization,
  saveConnectorCertificate,
  saveContextConnector,
  saveExternalSpace,
  saveEnterpriseSource,
  saveEntityGlossary,
  startMicrosoftDeviceCode,
  startSlackAuthorization,
  syncContextConnector,
} from '@/services/contextConnectorService';
import type {
  ConnectorDefinition,
  ConnectorHealth,
  ConnectorKind,
  EntityMapping,
  EntityGlossary,
  EntityGlossaryKind,
  ExternalSpaceKind,
  MicrosoftDeviceCodeAuthorization,
  NetSuiteEntityMapping,
  WmsEntityMapping,
} from '@/services/intelligenceContracts';
import { EnterpriseConnectionsCenter } from './EnterpriseConnectionsCenter';
import { EnterpriseEvidenceSearch } from './EnterpriseEvidenceSearch';

type DesktopConnectorKind = Exclude<ConnectorKind,
  'github' | 'gitlab' | 'jira' | 'confluence' | 'azure_devops' | 'servicenow' | 'sql' | 'sftp' | 'openapi'>;

const KIND_LABELS: Record<DesktopConnectorKind, string> = {
  local_files: 'Local knowledge files',
  microsoft_graph: 'SharePoint / OneDrive',
  odata: 'Generic OData ERP',
  sap: 'SAP OData',
  dynamics365: 'Dynamics 365',
  netsuite: 'NetSuite SuiteTalk',
  microsoft_teams: 'Selected Microsoft Teams spaces',
  slack: 'Selected Slack channels',
  notion: 'Selected Notion pages',
  wms: 'Warehouse management system (REST)',
  amazon_fba: 'Amazon FBA Selling Partner API',
};

function parseEntityMappings(value: string): EntityMapping[] {
  if (!value.trim()) return [];
  return value.split(';').map((entry) => {
    const [
      entityName,
      keyField,
      displayField,
      searchable,
      projected,
      mode = 'server_filter',
    ] = entry
      .split('|')
      .map((part) => part.trim());
    const searchableFields = searchable?.split(',').map((field) => field.trim()).filter(Boolean);
    const projectedFields = projected?.split(',').map((field) => field.trim()).filter(Boolean);
    if (
      !entityName
      || !keyField
      || !displayField
      || !searchableFields?.length
      || !projectedFields?.length
    ) {
      throw new Error('invalid_entity_mapping');
    }
    return {
      entity_name: entityName,
      key_field: keyField,
      display_field: displayField,
      searchable_fields: searchableFields,
      projected_fields: Array.from(new Set([
        keyField,
        displayField,
        ...searchableFields,
        ...projectedFields,
      ])),
      search_mode: mode === 'bounded_scan' ? 'bounded_scan' : 'server_filter',
    };
  });
}

function parseWmsMappings(value: string): WmsEntityMapping[] {
  if (!value.trim()) return [];
  return value.split(';').map((entry) => {
    const [entityName, endpointPath, keyField, displayField, searchable, projected] = entry
      .split('|')
      .map((part) => part.trim());
    const searchableFields = searchable?.split(',').map((field) => field.trim()).filter(Boolean);
    const projectedFields = projected?.split(',').map((field) => field.trim()).filter(Boolean);
    if (!entityName || !endpointPath || !keyField || !displayField || !searchableFields?.length || !projectedFields?.length) {
      throw new Error('invalid_wms_mapping');
    }
    return {
      entity_name: entityName,
      endpoint_path: endpointPath,
      key_field: keyField,
      display_field: displayField,
      searchable_fields: searchableFields,
      projected_fields: Array.from(new Set([keyField, displayField, ...searchableFields, ...projectedFields])),
      field_types: {},
      items_field: 'items',
      next_cursor_field: 'next_cursor',
      cursor_parameter: 'cursor',
      limit_parameter: 'limit',
      search_parameter: 'query',
      modified_since_parameter: null,
      page_size: 100,
      max_pages: 5,
    };
  });
}

export function ContextConnectorSettings() {
  const [connectors, setConnectors] = useState<ConnectorHealth[]>([]);
  const [kind, setKind] = useState<DesktopConnectorKind>('local_files');
  const [connectorId, setConnectorId] = useState('local-knowledge');
  const [displayName, setDisplayName] = useState('Local knowledge');
  const [sourceAuthority, setSourceAuthority] = useState(50);
  const [rootPath, setRootPath] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [entities, setEntities] = useState('');
  const [credential, setCredential] = useState('');
  const [tenantId, setTenantId] = useState('organizations');
  const [clientId, setClientId] = useState('');
  const [accountId, setAccountId] = useState('');
  const [certificateId, setCertificateId] = useState('');
  const [teamId, setTeamId] = useState('');
  const [reviewTarget, setReviewTarget] = useState('');
  const [workspaceLabel, setWorkspaceLabel] = useState('');
  const [wmsEnvironment, setWmsEnvironment] = useState('production');
  const [fbaRegion, setFbaRegion] = useState<'na' | 'eu' | 'fe'>('na');
  const [marketplaceIds, setMarketplaceIds] = useState('');
  const [graphDriveIds, setGraphDriveIds] = useState('');
  const [microsoftAuthorization, setMicrosoftAuthorization] =
    useState<MicrosoftDeviceCodeAuthorization | null>(null);
  const [authorizationConnectorId, setAuthorizationConnectorId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'working' | 'saved' | 'error'>('idle');
  const [glossary, setGlossary] = useState<EntityGlossary>({ revision: 0, entries: [] });
  const [glossaryName, setGlossaryName] = useState('');
  const [glossaryAliases, setGlossaryAliases] = useState('');
  const [glossaryKind, setGlossaryKind] = useState<EntityGlossaryKind>('system');
  const [glossaryStatus, setGlossaryStatus] = useState<'idle' | 'working' | 'saved' | 'error'>('idle');
  const [spaceId, setSpaceId] = useState('');
  const [spaceLabel, setSpaceLabel] = useState('');
  const [spaceKind, setSpaceKind] = useState<ExternalSpaceKind>('slack_private_channel');
  const [pendingSlackConnector, setPendingSlackConnector] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setConnectors(await listContextConnectors());
    } catch {
      setConnectors([]);
    }
  };

  const refreshGlossary = async () => {
    try {
      setGlossary(await getEntityGlossary());
    } catch {
      setGlossary({ revision: 0, entries: [] });
    }
  };

  const persistGlossary = async (next: EntityGlossary) => {
    setGlossaryStatus('working');
    try {
      setGlossary(await saveEntityGlossary(next));
      setGlossaryStatus('saved');
    } catch {
      setGlossaryStatus('error');
      await refreshGlossary();
    }
  };

  const addGlossaryEntry = async () => {
    const canonicalName = glossaryName.trim();
    if (!canonicalName) return;
    const aliases = glossaryAliases.split(',').map((value) => value.trim()).filter(Boolean);
    await persistGlossary({
      revision: glossary.revision,
      entries: [...glossary.entries, {
        entry_id: `entity-${crypto.randomUUID().replaceAll('-', '')}`,
        kind: glossaryKind,
        canonical_name: canonicalName,
        aliases,
        language: null,
        enabled: true,
      }],
    });
    setGlossaryName('');
    setGlossaryAliases('');
  };

  useEffect(() => {
    void refresh();
    void refreshGlossary();
  }, []);

  useEffect(() => {
    if (kind === 'microsoft_teams') setSpaceKind('teams_channel');
    if (kind === 'slack') setSpaceKind('slack_private_channel');
  }, [kind]);

  useEffect(() => {
    const unlisten = listen<string>('meeting-intelligence-slack-callback', async (event) => {
      if (!pendingSlackConnector) return;
      try {
        await completeSlackAuthorization(pendingSlackConnector, event.payload);
        setPendingSlackConnector(null);
        setStatus('saved');
        await refresh();
      } catch {
        setStatus('error');
      }
    });
    return () => { void unlisten.then((dispose) => dispose()); };
  }, [pendingSlackConnector]);

  useEffect(() => {
    if (!microsoftAuthorization || !authorizationConnectorId) return;
    let cancelled = false;
    const delay = Math.max(5, microsoftAuthorization.interval_seconds) * 1000;
    const timer = window.setTimeout(async () => {
      try {
        const result = await pollMicrosoftDeviceCode(authorizationConnectorId);
        if (cancelled) return;
        if (result === 'pending') {
          setMicrosoftAuthorization({ ...microsoftAuthorization });
          return;
        }
        if (result === 'complete') {
          try {
            await syncContextConnector(authorizationConnectorId);
          } catch {
            setMicrosoftAuthorization(null);
            setAuthorizationConnectorId(null);
            setStatus('error');
            await refresh();
            return;
          }
        }
        setMicrosoftAuthorization(null);
        setAuthorizationConnectorId(null);
        setStatus(result === 'complete' ? 'saved' : 'error');
        await refresh();
      } catch {
        if (!cancelled) {
          setStatus('error');
          setMicrosoftAuthorization({ ...microsoftAuthorization });
        }
      }
    }, delay);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [authorizationConnectorId, microsoftAuthorization]);

  const definition = (): ConnectorDefinition => {
    let configuration: ConnectorDefinition['configuration'];
    if (kind === 'local_files') {
      configuration = { kind, root_path: rootPath };
    } else if (kind === 'microsoft_graph') {
      configuration = {
        kind,
        tenant_id: tenantId,
        client_id: clientId || null,
        drive_ids: graphDriveIds.split(',').map((value) => value.trim()).filter(Boolean),
      };
    } else if (kind === 'netsuite') {
      const mappings: NetSuiteEntityMapping[] = parseEntityMappings(entities).map((mapping) => ({
        record_type: mapping.entity_name,
        key_field: mapping.key_field,
        display_field: mapping.display_field,
        searchable_fields: mapping.searchable_fields,
        projected_fields: Array.from(new Set([...mapping.projected_fields, 'lastmodifieddate'])),
        modified_field: 'lastmodifieddate',
      }));
      const [recordType, externalIdField, titleField, severityField,
        sourceReferenceField, statusField] = reviewTarget.split('|').map((value) => value.trim());
      configuration = {
        kind,
        account_id: accountId,
        client_id: clientId,
        certificate_id: certificateId,
        entity_mappings: mappings,
        review_target: recordType ? {
          record_type: recordType,
          external_id_field: externalIdField,
          title_field: titleField,
          severity_field: severityField,
          source_reference_field: sourceReferenceField,
          status_field: statusField || null,
        } : null,
      };
    } else if (kind === 'microsoft_teams') {
      configuration = {
        kind,
        tenant_id: tenantId,
        client_id: clientId,
        certificate_id: certificateId,
        selected_space_ids: [],
      };
    } else if (kind === 'slack') {
      configuration = { kind, client_id: clientId, team_id: teamId || null, selected_space_ids: [] };
    } else if (kind === 'notion') {
      configuration = { kind, workspace_label: workspaceLabel || displayName };
    } else if (kind === 'wms') {
      configuration = {
        kind,
        base_url: baseUrl,
        environment: wmsEnvironment,
        entity_mappings: parseWmsMappings(entities),
        analytics_mappings: [],
        fulfillment_mappings: [],
      };
    } else if (kind === 'amazon_fba') {
      configuration = {
        kind,
        region: fbaRegion,
        lwa_client_id: clientId,
        marketplace_ids: marketplaceIds.split(',').map((value) => value.trim()).filter(Boolean),
        include_inventory: true,
        include_inbound_shipments: true,
        inbound_lookback_days: 90,
        max_pages: 5,
        max_inbound_shipments: 25,
        analytics_mappings: [],
        fulfillment_mappings: [],
      };
    } else {
      configuration = { kind, base_url: baseUrl, entity_mappings: parseEntityMappings(entities) };
    }
    return {
      connector_id: connectorId,
      kind,
      display_name: displayName,
      source_authority: sourceAuthority,
      principal_id: 'current-windows-user',
      configuration,
    };
  };

  const save = async () => {
    setStatus('working');
    try {
      await saveContextConnector(
        definition(),
        ['netsuite', 'microsoft_teams', 'slack'].includes(kind) ? null : credential || null,
      );
      if (['netsuite', 'microsoft_teams'].includes(kind) && credential) {
        await saveConnectorCertificate(connectorId, credential);
      }
      if (kind === 'local_files' || ['odata', 'sap', 'dynamics365'].includes(kind)) {
        await syncContextConnector(connectorId);
      }
      setCredential('');
      setStatus('saved');
      await refresh();
    } catch {
      setStatus('error');
    }
  };

  const connectSlack = async () => {
    setStatus('working');
    try {
      await saveContextConnector(definition(), null);
      const authorization = await startSlackAuthorization(connectorId);
      setPendingSlackConnector(connectorId);
      await invoke('open_external_url', { url: authorization.authorization_url });
      setStatus('idle');
    } catch {
      setPendingSlackConnector(null);
      setStatus('error');
    }
  };

  const addSelectedSpace = async () => {
    const selectionId = `space-${crypto.randomUUID().replaceAll('-', '')}`;
    if (kind === 'notion') {
      await saveEnterpriseSource(connectorId, {
        selection_id: selectionId,
        connector_id: connectorId,
        kind: 'space',
        label: spaceLabel,
        enabled: true,
      }, spaceId);
    } else {
      await saveExternalSpace(connectorId, {
        selection_id: selectionId,
        label: spaceLabel,
        kind: spaceKind,
        enabled: true,
      }, spaceId);
    }
    setSpaceId('');
    setSpaceLabel('');
    await refresh();
  };

  const connectMicrosoft = async () => {
    setStatus('working');
    try {
      await saveContextConnector(definition(), null);
      setMicrosoftAuthorization(await startMicrosoftDeviceCode(connectorId));
      setAuthorizationConnectorId(connectorId);
      setStatus('idle');
      await refresh();
    } catch {
      setMicrosoftAuthorization(null);
      setAuthorizationConnectorId(null);
      setStatus('error');
    }
  };

  const deleteAll = async () => {
    if (!window.confirm('Disconnect every source and delete all encrypted connected-context data?')) {
      return;
    }
    setStatus('working');
    try {
      await deleteAllContextConnectors();
      setMicrosoftAuthorization(null);
      setAuthorizationConnectorId(null);
      setConnectors([]);
      setStatus('saved');
    } catch {
      setStatus('error');
    }
  };

  return (
    <section className="mt-6 space-y-5" aria-labelledby="context-connectors-heading">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-700" aria-hidden="true" />
          <div>
            <h2 id="context-connectors-heading" className="font-semibold text-emerald-950">
              Read-only connected context
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-emerald-800">
              Credentials stay in Windows Credential Manager. Indexed local files are encrypted
              for your Windows account; remote records are queried live and are not cached.
              External records can support a question, but never become meeting evidence or
              update ERP data.
            </p>
            <p className="mt-2 text-sm leading-relaxed text-emerald-800">
              Live intelligence works with meetings whose audio is captured locally by the desktop app,
              including Zoom, Google Meet, and Teams.
            </p>
          </div>
        </div>
      </div>

      <EnterpriseConnectionsCenter />
      <EnterpriseEvidenceSearch />

      <div className="rounded-2xl border border-sky-200 bg-sky-50/60 p-5">
        <h2 className="font-semibold text-sky-950">Company entity glossary</h2>
        <p className="mt-1 text-sm leading-relaxed text-sky-800">
          Add approved systems, objects, processes, capabilities, teams, regions, standards,
          and architecture patterns with Japanese, English, or Korean aliases. Entries are encrypted
          locally and are never learned from meeting text.
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <label className="text-sm font-medium text-gray-700">
            Entity type
            <select
              value={glossaryKind}
              onChange={(event) => setGlossaryKind(event.target.value as EntityGlossaryKind)}
              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2"
            >
              <option value="system">System</option>
              <option value="business_object">Business object</option>
              <option value="process">Process</option>
              <option value="interface">Interface</option>
              <option value="business_capability">Business capability</option>
              <option value="team">Team or role</option>
              <option value="region">Region</option>
              <option value="environment">Environment</option>
              <option value="standard">Standard</option>
              <option value="architecture_pattern">Architecture pattern</option>
            </select>
          </label>
          <label className="text-sm font-medium text-gray-700">
            Canonical name
            <input
              value={glossaryName}
              onChange={(event) => setGlossaryName(event.target.value)}
              maxLength={120}
              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2"
            />
          </label>
          <label className="text-sm font-medium text-gray-700">
            Aliases (comma separated)
            <input
              value={glossaryAliases}
              onChange={(event) => setGlossaryAliases(event.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2"
            />
          </label>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            disabled={glossaryStatus === 'working' || !glossaryName.trim()}
            onClick={() => void addGlossaryEntry()}
            className="rounded-lg bg-sky-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            Add approved entity
          </button>
          <span className="text-xs text-sky-800" role="status">
            {glossaryStatus === 'saved' && 'Glossary saved locally.'}
            {glossaryStatus === 'error' && 'Glossary was not saved. Check duplicate aliases.'}
          </span>
        </div>
        {glossary.entries.length > 0 && (
          <ul className="mt-4 space-y-2">
            {glossary.entries.map((entry) => (
              <li key={entry.entry_id} className="flex items-start justify-between gap-3 rounded-lg border border-sky-100 bg-white p-3">
                <div className="min-w-0">
                  <p className="break-words text-sm font-semibold text-gray-900">{entry.canonical_name}</p>
                  <p className="break-words text-xs text-gray-500">
                    {entry.kind.replace('_', ' ')}
                    {entry.aliases.length > 0 ? ` · ${entry.aliases.join(', ')}` : ''}
                  </p>
                </div>
                <button
                  type="button"
                  title="Remove glossary entry"
                  disabled={glossaryStatus === 'working'}
                  onClick={() => void persistGlossary({
                    revision: glossary.revision,
                    entries: glossary.entries.filter((candidate) => candidate.entry_id !== entry.entry_id),
                  })}
                  className="rounded-lg border border-rose-200 p-2 text-rose-700 disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid gap-4 rounded-2xl border border-gray-200 bg-white p-5 md:grid-cols-2">
        <label className="text-sm font-medium text-gray-700">
          Connector type
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as DesktopConnectorKind)}
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
          >
            {Object.entries(KIND_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label className="text-sm font-medium text-gray-700">
          Connector ID
          <input
            value={connectorId}
            onChange={(event) => setConnectorId(event.target.value)}
            pattern="[a-z0-9][a-z0-9_-]{0,63}"
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="text-sm font-medium text-gray-700 md:col-span-2">
          Display name
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="text-sm font-medium text-gray-700 md:col-span-2">
          Source authority ({sourceAuthority}/100)
          <input
            type="range"
            min="0"
            max="100"
            step="5"
            value={sourceAuthority}
            onChange={(event) => setSourceAuthority(Number(event.target.value))}
            className="mt-2 w-full accent-teal-700"
          />
          <span className="mt-1 block text-xs font-normal text-gray-500">
            A bounded ranking preference only. It never changes meeting evidence or confirms findings.
          </span>
        </label>
        {kind === 'local_files' && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            Knowledge folder
            <input
              value={rootPath}
              onChange={(event) => setRootPath(event.target.value)}
              placeholder="E:\\Knowledge"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
            />
          </label>
        )}
        {['odata', 'sap', 'dynamics365', 'wms'].includes(kind) && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            {kind === 'wms' ? 'HTTPS WMS REST base URL' : 'HTTPS OData base URL'}
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://erp.example.com/odata/v4"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
            />
          </label>
        )}
        {kind === 'notion' && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            Notion workspace label
            <input
              value={workspaceLabel}
              onChange={(event) => setWorkspaceLabel(event.target.value)}
              placeholder="Architecture workspace"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
            />
            <span className="mt-1 block text-xs font-normal text-gray-500">
              Use an internal integration with Read content only. Add only explicitly approved page IDs below.
            </span>
          </label>
        )}
        {kind === 'wms' && (
          <label className="text-sm font-medium text-gray-700">
            WMS environment
            <input
              value={wmsEnvironment}
              onChange={(event) => setWmsEnvironment(event.target.value)}
              placeholder="production"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
            />
          </label>
        )}
        {kind === 'amazon_fba' && (
          <>
            <label className="text-sm font-medium text-gray-700">
              Selling Partner API region
              <select value={fbaRegion} onChange={(event) => setFbaRegion(event.target.value as 'na' | 'eu' | 'fe')} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2">
                <option value="na">North America</option>
                <option value="eu">Europe</option>
                <option value="fe">Far East</option>
              </select>
            </label>
            <label className="text-sm font-medium text-gray-700">
              LWA application client ID
              <input value={clientId} onChange={(event) => setClientId(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2" />
            </label>
            <label className="text-sm font-medium text-gray-700 md:col-span-2">
              Approved marketplace IDs
              <input value={marketplaceIds} onChange={(event) => setMarketplaceIds(event.target.value)} placeholder="ATVPDKIKX0DER, A1F83G8C2ARO7P" className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2" />
            </label>
          </>
        )}
        {kind === 'microsoft_graph' && (
          <>
            <label className="text-sm font-medium text-gray-700">
              Microsoft tenant ID
              <input
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                placeholder="organizations or tenant GUID"
                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="text-sm font-medium text-gray-700">
              Public client application ID
              <input
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                placeholder="00000000-0000-0000-0000-000000000000"
                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
              />
            </label>
            <p className="text-xs leading-relaxed text-gray-500 md:col-span-2">
              The Microsoft application must allow public-client device sign-in and request only
              delegated Files.Read and User.Read access. Leave drive scope empty for your OneDrive.
            </p>
            <label className="text-sm font-medium text-gray-700 md:col-span-2">
              Selected SharePoint drive IDs (optional)
              <input
                value={graphDriveIds}
                onChange={(event) => setGraphDriveIds(event.target.value)}
                placeholder="drive-id-1, drive-id-2"
                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
              />
            </label>
          </>
        )}
        {['netsuite', 'microsoft_teams', 'slack'].includes(kind) && (
          <>
            {kind === 'netsuite' && (
              <label className="text-sm font-medium text-gray-700">
                NetSuite account ID
                <input
                  value={accountId}
                  onChange={(event) => setAccountId(event.target.value)}
                  placeholder="1234567_SB1"
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
                />
              </label>
            )}
            {kind === 'microsoft_teams' && (
              <label className="text-sm font-medium text-gray-700">
                Microsoft tenant ID
                <input
                  value={tenantId}
                  onChange={(event) => setTenantId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
                />
              </label>
            )}
            <label className="text-sm font-medium text-gray-700">
              OAuth application ID
              <input
                value={clientId}
                onChange={(event) => setClientId(event.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
              />
            </label>
            {kind === 'slack' ? (
              <label className="text-sm font-medium text-gray-700">
                Slack workspace ID (optional restriction)
                <input
                  value={teamId}
                  onChange={(event) => setTeamId(event.target.value)}
                  placeholder="T01234567"
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
                />
              </label>
            ) : (
              <label className="text-sm font-medium text-gray-700">
                Certificate ID
                <input
                  value={certificateId}
                  onChange={(event) => setCertificateId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
                />
              </label>
            )}
          </>
        )}
        {['odata', 'sap', 'dynamics365', 'netsuite', 'wms'].includes(kind) && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            ERP record mappings {['odata', 'netsuite', 'wms'].includes(kind) ? '(required)' : '(optional preset override)'}
            <input
              value={entities}
              onChange={(event) => setEntities(event.target.value)}
              placeholder={kind === 'wms'
                ? 'Inventory|v1/inventory|sku|sku|sku,warehouse|sku,warehouse,on_hand'
                : 'Orders|OrderId|Customer|OrderId,Customer|OrderId,Customer,Total|server_filter'}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2"
            />
          </label>
        )}
        {kind === 'netsuite' && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            Custom review record mapping (optional)
            <input
              value={reviewTarget}
              onChange={(event) => setReviewTarget(event.target.value)}
              placeholder="customrecord_copilot_review|custrecord_fingerprint|custrecord_title|custrecord_severity|custrecord_reference|custrecord_status"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"
            />
            <span className="mt-1 block text-xs font-normal text-gray-500">
              This is the only allowed NetSuite write target and still requires confirmation.
              Customer, vendor, order, inventory, and accounting records cannot be changed.
            </span>
          </label>
        )}
        {!['local_files', 'microsoft_graph', 'slack'].includes(kind) && (
          <label className="text-sm font-medium text-gray-700 md:col-span-2">
            {['netsuite', 'microsoft_teams'].includes(kind) ? 'Certificate private key (PEM)' : 'Read-only access credential'}
            <textarea
              autoComplete="off"
              value={credential}
              onChange={(event) => setCredential(event.target.value)}
              rows={['netsuite', 'microsoft_teams'].includes(kind) ? 4 : 1}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs"
            />
          </label>
        )}
        <div className="flex items-center gap-3 md:col-span-2">
          <button
            type="button"
            onClick={() => void save()}
            disabled={status === 'working'}
            className="rounded-lg bg-gray-950 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {status === 'working' ? 'Connecting…' : 'Save connector'}
          </button>
          {kind === 'microsoft_graph' && (
            <button
              type="button"
              onClick={() => void connectMicrosoft()}
              disabled={status === 'working' || !tenantId || !clientId}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-800 hover:bg-gray-50 disabled:opacity-50"
            >
              Sign in with Microsoft
            </button>
          )}
          {kind === 'slack' && (
            <button
              type="button"
              onClick={() => void connectSlack()}
              disabled={status === 'working' || !clientId}
              className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-800 hover:bg-gray-50 disabled:opacity-50"
            >
              Authorize selected Slack channels
            </button>
          )}
          <span className="text-sm text-gray-500" role="status">
            {status === 'saved' && 'Connector saved.'}
            {status === 'error' && 'Could not save this connector. Check its fields and access.'}
          </span>
        </div>
        {['microsoft_teams', 'slack', 'notion'].includes(kind) && (
          <div className="grid gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 md:col-span-2 md:grid-cols-2">
            <p className="text-xs leading-relaxed text-amber-900 md:col-span-2">
              {kind === 'notion'
                ? 'Add only an approved Notion page UUID. It is encrypted before storage and citations expose only the safe label.'
                : 'Add only an approved channel or meeting-chat identifier. It is encrypted before storage and never returned to this screen. Direct messages are not supported.'}
            </p>
            <label className="text-sm font-medium text-gray-700">
              {kind === 'notion' ? 'Safe page label' : 'Safe channel label'}
              <input value={spaceLabel} onChange={(event) => setSpaceLabel(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2" />
            </label>
            <label className="text-sm font-medium text-gray-700">
              {kind === 'notion' ? 'Notion page UUID' : 'Provider channel ID'}
              <input type="password" autoComplete="off" value={spaceId} onChange={(event) => setSpaceId(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2" />
            </label>
            {kind !== 'notion' && <label className="text-sm font-medium text-gray-700">
              Space type
              <select value={spaceKind} onChange={(event) => setSpaceKind(event.target.value as ExternalSpaceKind)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2">
                {kind === 'microsoft_teams' ? (
                  <>
                    <option value="teams_channel">Team channel</option>
                    <option value="teams_meeting_chat">Selected meeting chat</option>
                  </>
                ) : (
                  <>
                    <option value="slack_public_channel">Public channel</option>
                    <option value="slack_private_channel">Private channel</option>
                  </>
                )}
              </select>
            </label>}
            <button type="button" disabled={!spaceId || !spaceLabel || status === 'working'} onClick={() => void addSelectedSpace().catch(() => setStatus('error'))} className="rounded-lg bg-amber-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
              {kind === 'notion' ? 'Add selected page' : 'Add selected space'}
            </button>
          </div>
        )}
        {microsoftAuthorization && (
          <div className="rounded-xl border border-sky-200 bg-sky-50 p-4 md:col-span-2">
            <p className="text-sm text-sky-950">
              Open Microsoft sign-in and enter this one-time code:
              <strong className="ml-2 font-mono tracking-wider">
                {microsoftAuthorization.user_code}
              </strong>
            </p>
            <a
              href={microsoftAuthorization.verification_uri}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-2 inline-block text-sm font-semibold text-sky-800 underline"
            >
              Open Microsoft sign-in
            </a>
            <p className="mt-2 text-xs text-sky-700" role="status">
              Waiting for authorization. This code expires automatically.
            </p>
          </div>
        )}
      </div>

      <div className="space-y-3">
        {connectors.map((connector) => (
          <article key={connector.connector_id} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3">
                {connector.kind === 'local_files'
                  ? <FileText className="mt-0.5 h-5 w-5 text-gray-500" aria-hidden="true" />
                  : <Database className="mt-0.5 h-5 w-5 text-gray-500" aria-hidden="true" />}
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-gray-900">{connector.display_name}</h3>
                  <p className="text-xs text-gray-500">
                    {connector.kind in KIND_LABELS
                      ? KIND_LABELS[connector.kind as DesktopConnectorKind]
                      : connector.kind.replaceAll('_', ' ')}
                  </p>
                  <p className="mt-1 text-xs font-medium text-gray-700">{connector.phase.replace('_', ' ')}</p>
                  {connector.scope_summary && (
                    <p className="mt-1 text-xs text-gray-500">{connector.scope_summary}</p>
                  )}
                  {connector.kind === 'local_files' && (
                    <p className="mt-1 text-xs text-gray-500">
                      {connector.indexed_documents} indexed documents
                    </p>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  title={connector.kind === 'local_files' ? 'Reindex local files' : 'Check connection'}
                  onClick={() => void syncContextConnector(connector.connector_id)
                    .then(refresh)
                    .catch(() => setStatus('error'))}
                  className="rounded-lg border border-gray-200 p-2 text-gray-600 hover:bg-gray-50"
                >
                  <RefreshCw className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  title="Delete connector and local cache"
                  onClick={() => void deleteContextConnector(connector.connector_id).then(refresh).catch(() => setStatus('error'))}
                  className="rounded-lg border border-rose-200 p-2 text-rose-700 hover:bg-rose-50"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            </div>
          </article>
        ))}
        {connectors.length > 0 && (
          <button
            type="button"
            onClick={() => void deleteAll()}
            disabled={status === 'working'}
            className="rounded-lg border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50"
          >
            Disconnect all and delete connected data
          </button>
        )}
      </div>
    </section>
  );
}
