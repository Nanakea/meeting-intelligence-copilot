import type {
  ConnectorDefinition,
  ConnectorHealth,
  ConnectedQuestionBrief,
  ConnectedQuestionContextRequest,
  ConnectedProblemBrief,
  ConnectedProblemContextRequest,
  ContextSearchResult,
  MicrosoftAuthorizationStatus,
  MicrosoftDeviceCodeAuthorization,
  StructuredIssueDraft,
  StructuredIssueDraftBatch,
  EntityGlossary,
  ExternalSpaceSelection,
  AccessLease,
  ConnectorCatalogEntry,
  EnterpriseSearchRequest,
  EnterpriseSearchResult,
  LiveIntelligenceSnapshot,
  SourceSelection,
} from './intelligenceContracts';
import {
  buildIntelligenceHeaders,
  loadMeetingIntelligenceTransport,
} from './intelligenceTransport';

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const transport = await loadMeetingIntelligenceTransport();
  return fetch(`${transport.baseUrl}${path}`, {
    ...init,
    headers: {
      ...buildIntelligenceHeaders(transport),
      ...init.headers,
    },
  });
}

export async function saveConnectorCertificate(
  connectorId: string,
  privateKeyPem: string,
): Promise<ConnectorHealth> {
  return requireJson<ConnectorHealth>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/certificate`,
    { method: 'PUT', body: JSON.stringify({ private_key_pem: privateKeyPem }) },
  ));
}

export async function listExternalSpaces(
  connectorId: string,
): Promise<ExternalSpaceSelection[]> {
  return requireJson<ExternalSpaceSelection[]>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/spaces`,
  ));
}

export async function saveExternalSpace(
  connectorId: string,
  selection: ExternalSpaceSelection,
  externalId: string,
): Promise<ExternalSpaceSelection> {
  return requireJson<ExternalSpaceSelection>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/spaces`,
    { method: 'PUT', body: JSON.stringify({ selection, external_id: externalId }) },
  ));
}

export async function deleteExternalSpace(
  connectorId: string,
  selectionId: string,
): Promise<void> {
  await requireJson(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/spaces/${encodeURIComponent(selectionId)}`,
    { method: 'DELETE' },
  ));
}

export async function listEnterpriseConnectorCatalog(): Promise<ConnectorCatalogEntry[]> {
  return requireJson<ConnectorCatalogEntry[]>(await request('/connectors/catalog'));
}

export async function listEnterpriseSources(connectorId: string): Promise<SourceSelection[]> {
  return requireJson<SourceSelection[]>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/sources`,
  ));
}

export async function saveEnterpriseSource(
  connectorId: string,
  selection: SourceSelection,
  externalId: string,
): Promise<SourceSelection> {
  return requireJson<SourceSelection>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/sources`,
    { method: 'PUT', body: JSON.stringify({ selection, external_id: externalId }) },
  ));
}

export async function deleteEnterpriseSource(
  connectorId: string,
  selectionId: string,
): Promise<boolean> {
  const result = await requireJson<{ deleted: boolean }>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/sources/${encodeURIComponent(selectionId)}`,
    { method: 'DELETE' },
  ));
  return result.deleted;
}

export async function startEnterpriseAuthorization(connectorId: string): Promise<{
  authorization_url: string;
  state: string;
  expires_at: string;
}> {
  return requireJson(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/enterprise/authorize`,
    { method: 'POST' },
  ));
}

export async function completeEnterpriseAuthorization(
  connectorId: string,
  callbackUrl: string,
): Promise<ConnectorHealth> {
  const callback = new URL(callbackUrl);
  if (
    callback.protocol !== 'meeting-intelligence:'
    || callback.hostname !== 'oauth'
    || callback.pathname !== '/enterprise'
  ) {
    throw new Error('invalid_enterprise_callback');
  }
  const code = callback.searchParams.get('code');
  const state = callback.searchParams.get('state');
  if (!code || !state) throw new Error('invalid_enterprise_callback');
  return requireJson<ConnectorHealth>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/enterprise/callback`,
    { method: 'POST', body: JSON.stringify({ code, state }) },
  ));
}

export async function refreshEnterpriseLease(connectorId: string): Promise<AccessLease> {
  return requireJson<AccessLease>(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/lease/refresh`,
    { method: 'POST' },
  ));
}

export async function revokeEnterpriseAccess(connectorId: string): Promise<void> {
  await requireJson(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/revoke`,
    { method: 'POST' },
  ));
}

export async function searchEnterpriseEvidence(
  query: EnterpriseSearchRequest,
  signal?: AbortSignal,
): Promise<EnterpriseSearchResult> {
  return requireJson<EnterpriseSearchResult>(await request('/evidence/search', {
    method: 'POST',
    body: JSON.stringify(query),
    signal,
  }));
}

export async function startSlackAuthorization(connectorId: string): Promise<{
  authorization_url: string;
  state: string;
  expires_at: string;
}> {
  return requireJson(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/slack/authorize`,
    { method: 'POST', body: JSON.stringify({ redirect_uri: 'meeting-intelligence://oauth/slack' }) },
  ));
}

export async function completeSlackAuthorization(
  connectorId: string,
  callbackUrl: string,
): Promise<ConnectorHealth> {
  const callback = new URL(callbackUrl);
  if (callback.protocol !== 'meeting-intelligence:' || callback.hostname !== 'oauth' || callback.pathname !== '/slack') {
    throw new Error('invalid_slack_callback');
  }
  const code = callback.searchParams.get('code');
  const state = callback.searchParams.get('state');
  if (!code || !state) throw new Error('invalid_slack_callback');
  return requireJson(await request(
    `/context/connectors/${encodeURIComponent(connectorId)}/slack/callback`,
    {
      method: 'POST',
      body: JSON.stringify({ code, state, redirect_uri: 'meeting-intelligence://oauth/slack' }),
    },
  ));
}

async function requireJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`context_request_${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function listContextConnectors(): Promise<ConnectorHealth[]> {
  return requireJson<ConnectorHealth[]>(await request('/context/connectors'));
}

export async function saveContextConnector(
  definition: ConnectorDefinition,
  credential: string | null,
): Promise<ConnectorHealth> {
  return requireJson<ConnectorHealth>(
    await request('/context/connectors', {
      method: 'POST',
      body: JSON.stringify({ definition, credential: credential || null }),
    }),
  );
}

export async function syncContextConnector(connectorId: string): Promise<number> {
  const result = await requireJson<{ indexed_documents: number }>(
    await request(`/context/connectors/${encodeURIComponent(connectorId)}/sync`, {
      method: 'POST',
    }),
  );
  return result.indexed_documents;
}

export async function startMicrosoftDeviceCode(
  connectorId: string,
): Promise<MicrosoftDeviceCodeAuthorization> {
  return requireJson<MicrosoftDeviceCodeAuthorization>(
    await request(
      `/context/connectors/${encodeURIComponent(connectorId)}/microsoft-device-code`,
      { method: 'POST' },
    ),
  );
}

export async function pollMicrosoftDeviceCode(
  connectorId: string,
): Promise<MicrosoftAuthorizationStatus> {
  const result = await requireJson<{ status: MicrosoftAuthorizationStatus }>(
    await request(
      `/context/connectors/${encodeURIComponent(connectorId)}/microsoft-device-code/poll`,
      { method: 'POST' },
    ),
  );
  return result.status;
}

export async function deleteContextConnector(connectorId: string): Promise<boolean> {
  const result = await requireJson<{ deleted: boolean }>(
    await request(`/context/connectors/${encodeURIComponent(connectorId)}`, {
      method: 'DELETE',
    }),
  );
  return result.deleted;
}

export async function deleteAllContextConnectors(): Promise<void> {
  await requireJson<{ deleted: boolean }>(
    await request('/context', { method: 'DELETE' }),
  );
}

export async function searchContext(
  text: string,
  signal?: AbortSignal,
): Promise<ContextSearchResult> {
  return requireJson<ContextSearchResult>(
    await request('/context/search', {
      method: 'POST',
      signal,
      body: JSON.stringify({
        text,
        principal_id: 'current-windows-user',
        connector_ids: [],
        entity_types: [],
        limit: 4,
      }),
    }),
  );
}

export async function searchQuestionContext(
  query: ConnectedQuestionContextRequest,
  signal?: AbortSignal,
): Promise<ConnectedQuestionBrief> {
  const response = await request('/context/question-context', {
    method: 'POST',
    signal,
    body: JSON.stringify(query),
  });
  if (response.status === 409) {
    return response.json() as Promise<ConnectedQuestionBrief>;
  }
  return requireJson<ConnectedQuestionBrief>(response);
}

export async function searchProblemContext(
  query: ConnectedProblemContextRequest,
  signal?: AbortSignal,
): Promise<ConnectedProblemBrief> {
  const response = await request('/context/problem-context', {
    method: 'POST',
    signal,
    body: JSON.stringify(query),
  });
  if (response.status === 404 || response.status === 409) {
    return response.json() as Promise<ConnectedProblemBrief>;
  }
  return requireJson<ConnectedProblemBrief>(response);
}

export async function getEntityGlossary(): Promise<EntityGlossary> {
  return requireJson<EntityGlossary>(await request('/configuration/entity-glossary'));
}

export async function saveEntityGlossary(glossary: EntityGlossary): Promise<EntityGlossary> {
  return requireJson<EntityGlossary>(await request('/configuration/entity-glossary', {
    method: 'PUT',
    body: JSON.stringify(glossary),
  }));
}

export async function decideProblemIdentity(values: {
  session_id: string;
  state_version: number;
  action: 'merge' | 'keep_separate';
  pain_ids: string[];
  survivor_pain_id?: string | null;
}): Promise<LiveIntelligenceSnapshot> {
  return requireJson<LiveIntelligenceSnapshot>(await request('/problems/identity-decisions', {
    method: 'POST',
    body: JSON.stringify(values),
  }));
}

export async function decideSemanticHint(
  hintId: string,
  values: {
    session_id: string;
    state_version: number;
    action: 'confirm' | 'dismiss';
  },
): Promise<LiveIntelligenceSnapshot> {
  return requireJson<LiveIntelligenceSnapshot>(await request(
    `/problems/hints/${encodeURIComponent(hintId)}/decisions`,
    { method: 'POST', body: JSON.stringify(values) },
  ));
}

export async function recordContextCitationFeedback(values: {
  connector_kind: ConnectedQuestionBrief['citations'][number]['source_kind'];
  entity_type: string;
  rank: number;
  action: 'relevant' | 'not_relevant';
  state_version: number;
  response_ms: number;
}): Promise<void> {
  const response = await request('/context/citation-feedback', {
    method: 'POST',
    body: JSON.stringify(values),
  });
  if (!response.ok) throw new Error(`context_feedback_${response.status}`);
}

export async function resolveIssueDraft(values: {
  session_id: string;
  pain_id: string;
  state_version: number;
  connector_ids?: string[];
  include_context?: boolean;
}, signal?: AbortSignal): Promise<StructuredIssueDraft> {
  return requireJson<StructuredIssueDraft>(await request('/issues/drafts', {
    method: 'POST',
    signal,
    body: JSON.stringify({ connector_ids: [], include_context: true, ...values }),
  }));
}

export async function resolveIssueDraftBatch(values: {
  session_id: string;
  pain_ids: string[];
  state_version: number;
  connector_ids?: string[];
  include_context?: boolean;
}, signal?: AbortSignal): Promise<StructuredIssueDraftBatch> {
  return requireJson<StructuredIssueDraftBatch>(await request('/issues/drafts/batch', {
    method: 'POST',
    signal,
    body: JSON.stringify({ connector_ids: [], include_context: false, ...values }),
  }));
}
