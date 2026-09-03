import type { PreparedIssueDraft } from './intelligenceIssueDraft.ts';

const liveDrafts = new Map<string, PreparedIssueDraft>();
const MAX_CACHED_LIVE_DRAFTS = 8;

export function cacheLiveIssueDraft(
  sessionId: string,
  draft: PreparedIssueDraft,
): void {
  liveDrafts.delete(sessionId);
  liveDrafts.set(sessionId, draft);
  while (liveDrafts.size > MAX_CACHED_LIVE_DRAFTS) {
    const oldestSessionId = liveDrafts.keys().next().value;
    if (oldestSessionId === undefined) break;
    liveDrafts.delete(oldestSessionId);
  }
}

export function getCachedLiveIssueDraft(
  sessionId: string,
): PreparedIssueDraft | null {
  return liveDrafts.get(sessionId) ?? null;
}

export function clearCachedLiveIssueDraft(sessionId: string): void {
  liveDrafts.delete(sessionId);
}
