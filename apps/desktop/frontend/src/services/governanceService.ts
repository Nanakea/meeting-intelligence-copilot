import type {
  GovernanceCandidate,
  GovernanceRecord,
  SolutionPortfolio,
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
  if (!response.ok) throw new Error(`governance_request_${response.status}`);
  return response.json() as Promise<T>;
}

export async function reviewGovernanceCandidate(
  candidateId: string,
  values: {
    session_id: string;
    state_version: number;
    action: 'confirm' | 'dismiss';
    edits?: Record<string, unknown>;
  },
): Promise<GovernanceRecord | GovernanceCandidate> {
  return json(await request(
    `/governance/candidates/${encodeURIComponent(candidateId)}/review`,
    { method: 'POST', body: JSON.stringify({ edits: {}, ...values }) },
  ));
}

export async function getGovernancePortfolio(): Promise<SolutionPortfolio> {
  return json(await request('/governance/records'));
}

export async function getGovernanceCandidates(sessionId: string): Promise<GovernanceCandidate[]> {
  return json(await request(
    `/governance/sessions/${encodeURIComponent(sessionId)}/candidates`,
  ));
}

export async function deleteGovernanceMeeting(sessionId: string): Promise<void> {
  await json<{ deleted: boolean }>(await request(
    `/governance/meetings/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  ));
}

export async function changeGovernanceRecord(
  recordId: string,
  expectedRevision: number,
  action: 'update' | 'status' | 'supersede' | 'link' | 'unlink',
  changes: Record<string, unknown>,
): Promise<GovernanceRecord> {
  return json(await request(
    `/governance/records/${encodeURIComponent(recordId)}/events`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, action, changes }),
    },
  ));
}

export type GovernanceExportFormat = 'json' | 'raid_csv' | 'dependency_csv' | 'zip';

export async function exportGovernanceReviewPack(
  records: GovernanceRecord[],
  format: GovernanceExportFormat,
): Promise<boolean> {
  return invoke<boolean>('export_governance_review_pack', { records, format });
}
