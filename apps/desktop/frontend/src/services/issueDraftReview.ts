import type {
  IssueDraftReviewOverlay,
  StructuredIssueDraft,
} from './intelligenceContracts';

export const REVIEWABLE_DRAFT_FIELDS = [
  'title',
  'problem',
  'impact',
  'affected_systems',
  'scope',
  'owner',
  'workaround',
  'acceptance_criteria',
] as const;

export function bindRegeneratedDraftToMeeting(
  draft: StructuredIssueDraft,
  replaySessionId: string,
  meetingId: string,
): StructuredIssueDraft {
  const replayPrefix = `${replaySessionId}::`;
  if (!draft.pain_id.startsWith(replayPrefix)) return draft;
  return {
    ...draft,
    session_id: meetingId,
    pain_id: `${meetingId}::${draft.pain_id.slice(replayPrefix.length)}`,
  };
}

export type ReviewableDraftField = typeof REVIEWABLE_DRAFT_FIELDS[number];
type ReviewValue = string | string[] | null;

function valueFor(draft: StructuredIssueDraft, field: ReviewableDraftField): ReviewValue {
  return draft[field] as ReviewValue;
}

export function reviewFieldHash(value: ReviewValue): string {
  const encoded = JSON.stringify(value);
  let hash = 0x811c9dc5;
  for (let index = 0; index < encoded.length; index += 1) {
    hash ^= encoded.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

export function createIssueDraftReviewOverlay(
  meetingId: string,
  draft: StructuredIssueDraft,
  now = new Date().toISOString(),
): IssueDraftReviewOverlay {
  return {
    meeting_id: meetingId,
    pain_id: draft.pain_id,
    base_state_version: draft.state_version,
    status: 'unreviewed',
    field_edits: {},
    accepted_semantic_suggestions: [],
    base_field_hashes: Object.fromEntries(
      REVIEWABLE_DRAFT_FIELDS.map((field) => [field, reviewFieldHash(valueFor(draft, field))]),
    ),
    rebase_conflicts: {},
    created_at: now,
    updated_at: now,
  };
}

export function rebaseIssueDraftReviewOverlay(
  overlay: IssueDraftReviewOverlay,
  nextDraft: StructuredIssueDraft,
  now = new Date().toISOString(),
): IssueDraftReviewOverlay {
  const conflicts: IssueDraftReviewOverlay['rebase_conflicts'] = {};
  const hashes = { ...overlay.base_field_hashes };
  for (const field of REVIEWABLE_DRAFT_FIELDS) {
    const generated = valueFor(nextDraft, field);
    const nextHash = reviewFieldHash(generated);
    if (field in overlay.field_edits && hashes[field] !== nextHash) {
      conflicts[field] = {
        generated_value: generated,
        reviewed_value: overlay.field_edits[field],
      };
    } else {
      hashes[field] = nextHash;
    }
  }
  return {
    ...overlay,
    base_state_version: nextDraft.state_version,
    status: Object.keys(conflicts).length > 0 ? 'needs_rebase' : overlay.status,
    base_field_hashes: hashes,
    rebase_conflicts: conflicts,
    updated_at: now,
  };
}

export function applyIssueDraftReviewOverlay(
  draft: StructuredIssueDraft,
  overlay: IssueDraftReviewOverlay | undefined,
): StructuredIssueDraft {
  if (!overlay || Object.keys(overlay.rebase_conflicts).length > 0) return draft;
  return {
    ...draft,
    ...overlay.field_edits,
    review_provenance: {
      user_authored_fields: Object.keys(overlay.field_edits).sort(),
      accepted_semantic_suggestions: overlay.accepted_semantic_suggestions,
    },
  } as StructuredIssueDraft;
}
