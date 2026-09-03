import type {
  AssuranceFinding,
  AssuranceSeverity,
  AssuranceRun,
  AssuranceRulePack,
  AssuranceSchedule,
  ConnectorHealth,
  ConsistencyAttribution,
  ConsistencyDashboard,
  ConsistencyFinding,
  ConsistencyFindingStatus,
  ConsistencyMismatchKind,
  ConsistencyRun,
  DocumentClaim,
  AuthorityPolicy,
  DataQualityFinding,
  DataQualityRun,
  DataQualityRulePack,
  DocumentRoutingProposal,
  DocumentRoutingDestination,
  DocumentInbox,
  DocumentOperationPlan,
  DocumentScanStatus,
  ERPGovernanceDashboard,
  ERPGovernanceRun,
  ERPProcess,
  EvidenceBundle,
  ExternalActionDraft,
  ImprovementEvaluation,
  ImprovementPackExport,
  ImprovementProfile,
  ImprovementProposal,
  ImprovementSettings,
  ImprovementShadowRun,
  ManagedDocumentClassification,
  ManagedDocumentDisposition,
  NetSuiteMappingValidation,
  NotificationPolicy,
  SolutionLeadDashboard,
  SolutionThreadEdgeProposal,
  TrustedRuleSigningKey,
} from './intelligenceContracts';
import { invoke } from '@tauri-apps/api/core';
import {
  buildIntelligenceHeaders,
  loadMeetingIntelligenceTransport,
} from './intelligenceTransport';

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const transport = await loadMeetingIntelligenceTransport();
  return fetch(`${transport.baseUrl}${path}`, {
    ...init,
    headers: { ...buildIntelligenceHeaders(transport), ...init.headers },
  });
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`assurance_request_${response.status}`);
  return response.json() as Promise<T>;
}

export async function getSolutionLeadDashboard(): Promise<SolutionLeadDashboard> {
  return json(await request('/solution-lead/dashboard'));
}

export async function getAssuranceFindings(): Promise<AssuranceFinding[]> {
  return json(await request('/assurance/findings'));
}

export async function runConsistencyCheck(input: {
  document_connector_ids: string[];
  observed_connector_ids: string[];
  language: 'ja' | 'en' | 'ko';
  environment?: string;
}): Promise<ConsistencyRun> {
  return json(await request('/consistency/runs', {
    method: 'POST',
    body: JSON.stringify({ ...input, changed_only: true, detect_undocumented: true }),
  }));
}

export async function getConsistencyFindings(filters: {
  mismatchKind?: ConsistencyMismatchKind;
  severity?: AssuranceSeverity;
  status?: ConsistencyFindingStatus;
  environment?: string;
  system?: string;
  project?: string;
  repository?: string;
  document?: string;
  documentKind?: string;
  ownerRole?: string;
} = {}): Promise<ConsistencyFinding[]> {
  const query = new URLSearchParams();
  if (filters.mismatchKind) query.set('mismatch_kind', filters.mismatchKind);
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.status) query.set('status', filters.status);
  if (filters.environment) query.set('environment', filters.environment);
  if (filters.system) query.set('system', filters.system);
  if (filters.project) query.set('project', filters.project);
  if (filters.repository) query.set('repository', filters.repository);
  if (filters.document) query.set('document', filters.document);
  if (filters.documentKind) query.set('document_kind', filters.documentKind);
  if (filters.ownerRole) query.set('owner_role', filters.ownerRole);
  const suffix = query.size ? `?${query.toString()}` : '';
  return json(await request(`/consistency/findings${suffix}`));
}

export async function getConsistencyDashboard(): Promise<ConsistencyDashboard> {
  return json(await request('/consistency/dashboard'));
}

export async function reviewConsistencyFinding(
  finding: ConsistencyFinding,
  input: {
    action: 'confirm' | 'dismiss' | 'resolve' | 'reopen' | 'classify' | 'assign' | 'request_refresh' | 'mark_exception';
    attribution?: ConsistencyAttribution;
    owner_role?: string;
    note?: string;
    exception_reason?: string;
    exception_expires_at?: string;
  },
): Promise<ConsistencyFinding> {
  return json(await request(
    `/consistency/findings/${encodeURIComponent(finding.finding_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: finding.revision, ...input }),
    },
  ));
}

export async function reviewConsistencyClaim(
  claim: DocumentClaim,
  action: 'confirm' | 'dismiss',
): Promise<DocumentClaim> {
  return json(await request(`/consistency/claims/${encodeURIComponent(claim.claim_id)}/review`, {
    method: 'POST', body: JSON.stringify({ expected_revision: claim.revision, action }),
  }));
}

export async function getAuthorityPolicies(): Promise<AuthorityPolicy[]> {
  return json(await request('/consistency/authority-policies'));
}

export async function saveAuthorityPolicy(policy: AuthorityPolicy): Promise<AuthorityPolicy> {
  return json(await request('/consistency/authority-policies', {
    method: 'PUT', body: JSON.stringify(policy),
  }));
}

export async function exportConsistencyFinding(
  finding: ConsistencyFinding,
  format: 'markdown' | 'canonical_json' | 'jira_csv' | 'azure_boards_csv',
  draftKind: 'issue' | 'risk' | 'action' | 'adr' | 'mapping_change' = 'issue',
): Promise<void> {
  const exported = await json<{
    filename: string;
    media_type: string;
    payload_base64: string;
    direct_submission_enabled: false;
  }>(await request(`/consistency/findings/${encodeURIComponent(finding.finding_id)}/export`, {
    method: 'POST', body: JSON.stringify({ format, draft_kind: draftKind }),
  }));
  const bytes = Uint8Array.from(atob(exported.payload_base64), (value) => value.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: exported.media_type }));
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = exported.filename;
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function getSuggestedTraceabilityLinks(): Promise<SolutionThreadEdgeProposal[]> {
  return json(await request('/solution-thread/edges/suggested'));
}

export async function reviewTraceabilityLink(
  proposal: SolutionThreadEdgeProposal,
  action: 'confirm' | 'dismiss',
): Promise<void> {
  await json(await request(
    `/solution-thread/edges/${encodeURIComponent(proposal.edge_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: proposal.revision, action }),
    },
  ));
}

export async function runDocumentAssurance(connectorIds: string[]): Promise<AssuranceRun> {
  return json(await request('/assurance/runs', {
    method: 'POST',
    body: JSON.stringify({ connector_ids: connectorIds, changed_only: true }),
  }));
}

export async function reviewAssuranceFinding(
  finding: AssuranceFinding,
  action: 'confirm' | 'dismiss' | 'resolve' | 'reopen' | 'propose_governance',
): Promise<AssuranceFinding> {
  return json(await request(
    `/assurance/findings/${encodeURIComponent(finding.finding_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: finding.revision, action }),
    },
  ));
}

export async function queryEvidence(text: string, connectorIds: string[]): Promise<EvidenceBundle> {
  return json(await request('/evidence/query', {
    method: 'POST',
    body: JSON.stringify({
      text,
      principal_id: 'local-current-user',
      connector_ids: connectorIds,
      limit: 8,
    }),
  }));
}

export async function getAssuranceSchedules(): Promise<AssuranceSchedule[]> {
  return json(await request('/assurance/schedules'));
}

export async function trustAssuranceSigningKey(input: {
  key_id: string;
  label: string;
  public_key_base64: string;
}): Promise<TrustedRuleSigningKey> {
  return json(await request('/assurance/trusted-keys', {
    method: 'PUT',
    body: JSON.stringify(input),
  }));
}

export async function installAssuranceRulePack(packageBase64: string): Promise<AssuranceRulePack> {
  return json(await request('/assurance/rule-packs', {
    method: 'PUT',
    body: JSON.stringify({ package_base64: packageBase64 }),
  }));
}

export async function saveAssuranceSchedule(
  schedule: AssuranceSchedule,
): Promise<AssuranceSchedule> {
  const saved = await json<AssuranceSchedule>(await request('/assurance/schedules', {
    method: 'PUT',
    body: JSON.stringify(schedule),
  }));
  await invoke('configure_meeting_intelligence_assurance_schedule', {
    scheduleId: saved.schedule_id,
    cadence: saved.cadence,
    localTime: saved.local_time,
    enabled: saved.enabled,
    runIfMissed: saved.run_if_missed,
  });
  return saved;
}

export async function checkErpMetadata(connectorId: string): Promise<number> {
  const result = await json<{ findings: AssuranceFinding[] }>(await request(
    '/erp/metadata/check',
    { method: 'POST', body: JSON.stringify({ connector_id: connectorId, artifact_ids: [] }) },
  ));
  return result.findings.length;
}

export function assuranceCapableConnectorIds(connectors: ConnectorHealth[]): string[] {
  return connectors
    .filter((connector) => ['local_files', 'microsoft_graph'].includes(connector.kind))
    .map((connector) => connector.connector_id);
}

export async function runNetSuiteDataQuality(connectorId: string): Promise<DataQualityRun> {
  return json(await request('/data-quality/runs', {
    method: 'POST',
    body: JSON.stringify({ connector_id: connectorId, rule_pack_ids: [], changed_only: true }),
  }));
}

export async function installDataQualityRulePack(
  packageBase64: string,
): Promise<DataQualityRulePack> {
  return json(await request('/data-quality/rule-packs', {
    method: 'PUT', body: JSON.stringify({ package_base64: packageBase64 }),
  }));
}

export async function getDataQualityFindings(): Promise<DataQualityFinding[]> {
  return json(await request('/data-quality/findings'));
}

export async function reviewDataQualityFinding(
  finding: DataQualityFinding,
  action: 'confirm' | 'dismiss',
): Promise<DataQualityFinding> {
  return json(await request(
    `/data-quality/findings/${encodeURIComponent(finding.finding_id)}/review`,
    { method: 'POST', body: JSON.stringify({ expected_revision: finding.revision, action }) },
  ));
}

export async function getExternalActions(): Promise<ExternalActionDraft[]> {
  return json(await request('/external-actions'));
}

export async function createExternalAction(input: {
  connector_id: string;
  destination_ref: string;
  finding_id: string;
}): Promise<ExternalActionDraft> {
  return json(await request('/external-actions', {
    method: 'POST', body: JSON.stringify(input),
  }));
}

export async function decideExternalAction(
  action: ExternalActionDraft,
  decision: 'approve' | 'cancel',
): Promise<ExternalActionDraft> {
  return json(await request(
    `/external-actions/${encodeURIComponent(action.action_id)}/${decision}`,
    { method: 'POST', body: JSON.stringify({ expected_revision: action.revision }) },
  ));
}

export async function exportExternalAction(
  action: ExternalActionDraft,
  format: 'markdown' | 'canonical_json' | 'jira_csv' | 'azure_boards_csv'
    | 'github_issue_markdown' | 'gitlab_issue_markdown' | 'servicenow_csv'
    | 'netsuite_review_json' | 'combined_zip',
): Promise<void> {
  const exported = await json<{
    filename: string;
    media_type: string;
    payload_base64: string;
    direct_submission_enabled: false;
  }>(await request(`/external-actions/${encodeURIComponent(action.action_id)}/export`, {
    method: 'POST',
    body: JSON.stringify({ format, include_connected_excerpts: false }),
  }));
  const binary = atob(exported.payload_base64);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: exported.media_type }));
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = exported.filename;
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function getNotificationPolicies(): Promise<NotificationPolicy[]> {
  return json(await request('/notification-policies'));
}

export async function saveNotificationPolicy(
  policy: NotificationPolicy,
  expectedRevision: number | null,
): Promise<NotificationPolicy> {
  return json(await request('/notification-policies', {
    method: 'PUT',
    body: JSON.stringify({ policy, expected_revision: expectedRevision }),
  }));
}

export async function getDocumentRoutingProposals(): Promise<DocumentRoutingProposal[]> {
  return json(await request('/document-routing/proposals'));
}

export async function getDocumentRoutingDestinations(): Promise<DocumentRoutingDestination[]> {
  return json(await request('/document-routing/destinations'));
}

export async function saveDocumentRoutingDestination(
  destination: DocumentRoutingDestination,
  rootPath: string,
): Promise<DocumentRoutingDestination> {
  return json(await request('/document-routing/destinations', {
    method: 'PUT', body: JSON.stringify({ destination, root_path: rootPath }),
  }));
}

export async function reviewDocumentRoutingProposal(
  proposal: DocumentRoutingProposal,
  action: 'execute' | 'cancel',
  routingAction: 'copy' | 'move' = 'copy',
): Promise<DocumentRoutingProposal> {
  return json(await request(
    `/document-routing/proposals/${encodeURIComponent(proposal.proposal_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({
        expected_revision: proposal.revision,
        action,
        routing_action: routingAction,
      }),
    },
  ));
}

export async function getImprovementProposals(): Promise<ImprovementProposal[]> {
  return json(await request('/improvements/proposals'));
}

export async function evaluateImprovementProposal(
  proposal: ImprovementProposal,
): Promise<ImprovementEvaluation> {
  return json(await request(
    `/improvements/proposals/${encodeURIComponent(proposal.proposal_id)}/evaluate`,
    { method: 'POST' },
  ));
}

export async function runImprovementShadowCycle(
  proposal: ImprovementProposal,
): Promise<ImprovementShadowRun> {
  return json(await request(
    `/improvements/proposals/${encodeURIComponent(proposal.proposal_id)}/shadow-runs`,
    { method: 'POST' },
  ));
}

export async function reviewImprovementProposal(
  proposal: ImprovementProposal,
  action: 'approve' | 'reject',
): Promise<ImprovementProposal> {
  return json(await request(
    `/improvements/proposals/${encodeURIComponent(proposal.proposal_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: proposal.revision, action }),
    },
  ));
}

export async function getImprovementProfiles(): Promise<ImprovementProfile[]> {
  return json(await request('/improvements/profiles'));
}

export async function rollbackImprovementProfile(
  profile: ImprovementProfile,
): Promise<ImprovementProfile> {
  return json(await request(
    `/improvements/profiles/${encodeURIComponent(profile.profile_id)}/rollback`,
    { method: 'POST' },
  ));
}

export async function getImprovementSettings(): Promise<ImprovementSettings> {
  return json(await request('/improvements/settings'));
}

export async function saveImprovementSettings(
  settings: ImprovementSettings,
): Promise<ImprovementSettings> {
  return json(await request('/improvements/settings', {
    method: 'PUT',
    body: JSON.stringify({
      expected_revision: settings.revision,
      paused: settings.paused,
      excerpt_retention_days: settings.excerpt_retention_days,
    }),
  }));
}

export async function exportImprovementPack(): Promise<ImprovementPackExport> {
  return json(await request('/improvements/packs/export', { method: 'POST' }));
}

export async function importImprovementPack(contentBase64: string): Promise<void> {
  await json(await request('/improvements/packs/import', {
    method: 'POST', body: JSON.stringify({ content_base64: contentBase64 }),
  }));
}

export async function getDocumentInbox(): Promise<DocumentInbox> {
  return json(await request('/documents/inbox'));
}

export async function scanManagedDocuments(connectorIds: string[]): Promise<DocumentScanStatus> {
  return json(await request('/documents/scan', {
    method: 'POST',
    body: JSON.stringify({ connector_ids: connectorIds }),
  }));
}

export async function getDocumentScanStatus(): Promise<DocumentScanStatus> {
  return json(await request('/documents/scan/status'));
}

export async function reviewManagedDocumentClassification(
  classification: ManagedDocumentClassification,
  action: 'approve' | 'archive',
): Promise<ManagedDocumentClassification> {
  return json(await request(
    `/documents/classifications/${encodeURIComponent(classification.classification_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: classification.revision, action, labels: {} }),
    },
  ));
}

export async function reviewManagedDocumentDisposition(
  disposition: ManagedDocumentDisposition,
  action: 'approve' | 'cancel',
): Promise<ManagedDocumentDisposition> {
  return json(await request(
    `/documents/dispositions/${encodeURIComponent(disposition.disposition_id)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({
        expected_revision: disposition.revision,
        action,
        routing_action: 'copy',
      }),
    },
  ));
}

export async function executeManagedDocumentDisposition(
  disposition: ManagedDocumentDisposition,
  destinationRef: string,
  action: 'copy' | 'move' = 'copy',
): Promise<DocumentOperationPlan> {
  return json(await request(
    `/documents/dispositions/${encodeURIComponent(disposition.disposition_id)}/execute`,
    {
      method: 'POST',
      body: JSON.stringify({
        expected_revision: disposition.revision,
        destination_ref: destinationRef,
        routing_action: action,
      }),
    },
  ));
}

export async function runErpGovernance(
  connectorId: string,
  processes: ERPProcess[] = [],
): Promise<ERPGovernanceRun> {
  return json(await request('/erp/governance/runs', {
    method: 'POST',
    body: JSON.stringify({ connector_id: connectorId, processes, changed_only: true }),
  }));
}

export async function getErpGovernanceDashboard(): Promise<ERPGovernanceDashboard> {
  return json(await request('/erp/governance/dashboard'));
}

export async function validateNetSuiteMappings(
  connectorId: string,
): Promise<NetSuiteMappingValidation> {
  return json(await request('/erp/governance/mappings/validate', {
    method: 'POST',
    body: JSON.stringify({ connector_id: connectorId }),
  }));
}
