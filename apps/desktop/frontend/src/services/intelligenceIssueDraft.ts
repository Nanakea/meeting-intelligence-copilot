import { invoke } from '@tauri-apps/api/core';
import type {
  IssueDraftReviewOverlay,
  IssueExportOptions,
  LiveIntelligenceSnapshot,
  StructuredIssueDraft,
} from './intelligenceContracts';
import type { Transcript } from '@/types';
import {
  buildIntelligenceHeaders,
  buildIntelligenceWebSocketProtocols,
  buildIntelligenceWebSocketUrl,
  loadMeetingIntelligenceTransport,
  validateLiveBatchAcknowledgement,
} from './intelligenceTransport';
import { decideProblemIdentity, resolveIssueDraftBatch } from './contextConnectorService';

export type IssueDraftLanguage = 'ja' | 'en' | 'ko';

export interface PreparedIssueDraft {
  title: string;
  markdown: string;
  unresolvedCount: number;
  ready: boolean;
  language: IssueDraftLanguage;
}

export interface StoredMeetingIssueDraft {
  meeting_id: string;
  draft_markdown: string;
  unresolved_count: number;
  language: IssueDraftLanguage;
  updated_at: string;
}

export interface StoredStructuredIssueDraft {
  meeting_id: string;
  pain_id: string;
  draft_json: string;
  readiness: StructuredIssueDraft['readiness'];
  language: IssueDraftLanguage;
  state_version: number;
  updated_at: string;
}

export interface StoredIssueDraftReviewOverlay {
  meeting_id: string;
  pain_id: string;
  base_state_version: number;
  status: IssueDraftReviewOverlay['status'];
  overlay_json: string;
  updated_at: string;
}

export interface IssueDraftRegenerationSession {
  sessionId: string;
  snapshot: LiveIntelligenceSnapshot;
  drafts: StructuredIssueDraft[];
}

export type IssueExportFormat = 'markdown' | 'json' | 'jira_csv' | 'azure_csv';

export type IssueExportSettings = Omit<IssueExportOptions, 'include_connected_excerpts'>;

export const DEFAULT_ISSUE_EXPORT_SETTINGS: IssueExportSettings = {
  jira_work_type: 'Task',
  azure_work_item_type: 'Task',
};

export async function saveMeetingIssueDraft(
  meetingId: string,
  draft: PreparedIssueDraft,
): Promise<boolean> {
  return invoke<boolean>('api_save_meeting_issue_draft', {
    meetingId,
    draftMarkdown: draft.markdown,
    unresolvedCount: draft.unresolvedCount,
    language: draft.language,
  });
}

export async function getMeetingIssueDraft(
  meetingId: string,
): Promise<StoredMeetingIssueDraft | null> {
  return invoke<StoredMeetingIssueDraft | null>('api_get_meeting_issue_draft', {
    meetingId,
  });
}

export async function saveMeetingIssueDrafts(
  meetingId: string,
  drafts: StructuredIssueDraft[],
): Promise<number> {
  return invoke<number>('api_save_meeting_issue_drafts', { meetingId, drafts });
}

export async function getMeetingIssueDrafts(
  meetingId: string,
): Promise<StoredStructuredIssueDraft[]> {
  return invoke<StoredStructuredIssueDraft[]>('api_get_meeting_issue_drafts', { meetingId });
}

export async function getIssueDraftReviewOverlays(
  meetingId: string,
): Promise<StoredIssueDraftReviewOverlay[]> {
  return invoke<StoredIssueDraftReviewOverlay[]>('api_get_issue_draft_review_overlays', {
    meetingId,
  });
}

export async function saveIssueDraftReviewOverlay(
  meetingId: string,
  painId: string,
  overlay: IssueDraftReviewOverlay,
): Promise<boolean> {
  return invoke<boolean>('api_save_issue_draft_review_overlay', {
    meetingId,
    painId,
    overlay,
  });
}

export async function exportIssueDraft(
  draft: StructuredIssueDraft,
  format: IssueExportFormat,
  options: IssueExportOptions,
): Promise<boolean> {
  return invoke<boolean>('export_issue_draft', {
    draft,
    format,
    options,
  });
}

export async function exportIssueDraftBatch(
  drafts: StructuredIssueDraft[],
  options: IssueExportOptions,
): Promise<boolean> {
  return invoke<boolean>('export_issue_draft_batch', {
    drafts,
    options,
  });
}

export async function getIssueExportSettings(): Promise<IssueExportSettings> {
  return invoke<IssueExportSettings>('get_issue_export_settings');
}

export async function setIssueExportSettings(
  settings: IssueExportSettings,
): Promise<IssueExportSettings> {
  return invoke<IssueExportSettings>('set_issue_export_settings', { settings });
}

function temporarySessionId(): string {
  return `meeting-intel-${crypto.randomUUID().replaceAll('-', '')}`;
}

async function readCurrentSnapshot(
  sessionId: string,
): Promise<LiveIntelligenceSnapshot> {
  const transport = await loadMeetingIntelligenceTransport();
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(
      buildIntelligenceWebSocketUrl(transport, sessionId),
      buildIntelligenceWebSocketProtocols(transport),
    );
    const timeout = window.setTimeout(() => {
      socket.close();
      reject(new Error('issue_draft_replay_snapshot_timeout'));
    }, 5_000);
    socket.onmessage = (event) => {
      try {
        const snapshot = JSON.parse(String(event.data)) as LiveIntelligenceSnapshot;
        window.clearTimeout(timeout);
        socket.close();
        resolve(snapshot);
      } catch {
        window.clearTimeout(timeout);
        socket.close();
        reject(new Error('issue_draft_replay_snapshot_invalid'));
      }
    };
    socket.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error('issue_draft_replay_socket_unavailable'));
    };
  });
}

async function draftsForSnapshot(
  sessionId: string,
  snapshot: LiveIntelligenceSnapshot,
): Promise<StructuredIssueDraft[]> {
  const painIds = snapshot.pain_points
    .filter((pain) => pain.status === 'open' && pain.merged_into_pain_id == null)
    .map((pain) => pain.pain_id);
  if (painIds.length === 0) return [];
  const batch = await resolveIssueDraftBatch({
    session_id: sessionId,
    pain_ids: painIds,
    state_version: snapshot.version,
    include_context: false,
  });
  return batch.drafts;
}

export async function beginIssueDraftRegeneration(
  transcripts: Transcript[],
  language: IssueDraftLanguage,
): Promise<IssueDraftRegenerationSession | null> {
  const usable = transcripts.filter((transcript) => transcript.text.trim().length > 0);
  if (usable.length === 0) return null;
  const sessionId = temporarySessionId();
  const transport = await loadMeetingIntelligenceTransport();
  const headers = buildIntelligenceHeaders(transport);
  try {
    for (let offset = 0; offset < usable.length; offset += 200) {
      const payloads = usable.slice(offset, offset + 200).map((transcript, index) => ({
        text: transcript.text,
        source: 'SavedMeetingReplay',
        sequence_id: offset + index,
        is_partial: false,
        confidence: transcript.confidence ?? 1,
        audio_start_time: transcript.audio_start_time ?? offset + index,
        audio_end_time: transcript.audio_end_time ?? offset + index + 1,
      }));
      const response = await fetch(
        `${transport.baseUrl}/ingest/live/${encodeURIComponent(sessionId)}/batch`,
        {
          method: 'POST',
          headers,
          body: JSON.stringify({ adapter: 'meetily', lang: language, payloads }),
        },
      );
      if (!response.ok) throw new Error(`issue_draft_replay_${response.status}`);
      const acknowledgement = validateLiveBatchAcknowledgement(await response.json());
      if (acknowledgement.rejected_sequence_ids.length > 0) {
        throw new Error('issue_draft_replay_rejected');
      }
    }
    const snapshot = await readCurrentSnapshot(sessionId);
    return {
      sessionId,
      snapshot,
      drafts: await draftsForSnapshot(sessionId, snapshot),
    };
  } catch (error) {
    await fetch(`${transport.baseUrl}/meeting/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers,
    }).catch(() => undefined);
    throw error;
  }
}

export async function decideRegeneratedProblemIdentity(
  regeneration: IssueDraftRegenerationSession,
  painIds: [string, string],
  action: 'merge' | 'keep_separate',
): Promise<IssueDraftRegenerationSession> {
  const snapshot = await decideProblemIdentity({
    session_id: regeneration.sessionId,
    state_version: regeneration.snapshot.version,
    action,
    pain_ids: painIds,
  });
  return {
    ...regeneration,
    snapshot,
    drafts: await draftsForSnapshot(regeneration.sessionId, snapshot),
  };
}

export async function closeIssueDraftRegeneration(
  regeneration: IssueDraftRegenerationSession,
): Promise<void> {
  const transport = await loadMeetingIntelligenceTransport();
  await fetch(
    `${transport.baseUrl}/meeting/${encodeURIComponent(regeneration.sessionId)}`,
    {
      method: 'DELETE',
      headers: buildIntelligenceHeaders(transport),
    },
  ).catch(() => undefined);
}

export async function regenerateMeetingIssueDrafts(
  transcripts: Transcript[],
  language: IssueDraftLanguage,
): Promise<StructuredIssueDraft[]> {
  const regeneration = await beginIssueDraftRegeneration(transcripts, language);
  if (regeneration === null) return [];
  try {
    return regeneration.drafts;
  } finally {
    await closeIssueDraftRegeneration(regeneration);
  }
}
