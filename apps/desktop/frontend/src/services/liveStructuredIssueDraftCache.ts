import type { StructuredIssueDraft } from './intelligenceContracts';

const liveDrafts = new Map<string, StructuredIssueDraft[]>();

export function cacheLiveStructuredIssueDrafts(
  sessionId: string,
  drafts: StructuredIssueDraft[],
): void {
  liveDrafts.set(sessionId, drafts);
}

export function getCachedLiveStructuredIssueDrafts(
  sessionId: string,
): StructuredIssueDraft[] {
  return liveDrafts.get(sessionId) ?? [];
}

export function clearCachedLiveStructuredIssueDrafts(sessionId: string): void {
  liveDrafts.delete(sessionId);
}
