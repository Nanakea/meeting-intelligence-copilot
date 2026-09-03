import type { LiveIntelligenceSnapshot } from './intelligenceContracts';

function withoutEvidence<T extends { evidence_event_ids: string[] }>(item: T) {
  const visible: Partial<T> = { ...item };
  delete visible.evidence_event_ids;
  return visible;
}

/** Identity for panel-visible intelligence, excluding transport metadata and evidence churn. */
export function visibleIntelligenceFingerprint(state: LiveIntelligenceSnapshot): string {
  return JSON.stringify({
    pain_points: state.pain_points.map(withoutEvidence),
    facts: state.facts.map(withoutEvidence),
    gaps: state.gaps,
    suggestions: state.suggestions,
    semantic_hints: state.semantic_hints,
    identity_review_candidates: state.identity_review_candidates,
    governance_candidates: state.governance_candidates,
    governance_alerts: state.governance_alerts,
  });
}
