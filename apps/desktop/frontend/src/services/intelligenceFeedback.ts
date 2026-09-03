export type IntelligenceFeedbackAction = 'useful' | 'dismissed';

import {
  buildIntelligenceHeaders,
  loadMeetingIntelligenceTransport,
} from './intelligenceTransport';

export interface IntelligenceFeedback {
  sessionId: string;
  suggestionId: string;
  painTemplate: string;
  slot: string;
  action: IntelligenceFeedbackAction;
  stateVersion: number;
  reopenCount: number;
}

function tauriAvailable(): boolean {
  return typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__);
}

export async function recordIntelligenceFeedback(
  feedback: IntelligenceFeedback,
): Promise<boolean> {
  if (!tauriAvailable()) return false;
  const { invoke } = await import('@tauri-apps/api/core');
  const saved = await invoke<boolean>('record_meeting_intelligence_feedback', { feedback });
  if (saved) {
    try {
      const transport = await loadMeetingIntelligenceTransport();
      await fetch(`${transport.baseUrl}/improvements/ask-now-feedback`, {
        method: 'POST',
        headers: buildIntelligenceHeaders(transport),
        body: JSON.stringify({
          session_id: feedback.sessionId,
          suggestion_id: feedback.suggestionId,
          state_version: feedback.stateVersion,
          reopen_count: feedback.reopenCount,
          action: feedback.action,
        }),
      });
    } catch {
      // The local feedback record is authoritative; learning is best-effort.
    }
  }
  return saved;
}

export async function isIntelligenceSuggestionDismissed(
  sessionId: string,
  suggestionId: string,
  reopenCount: number,
): Promise<boolean> {
  if (!tauriAvailable()) return false;
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<boolean>('is_meeting_intelligence_suggestion_dismissed', {
    occurrence: { sessionId, suggestionId, reopenCount },
  });
}
