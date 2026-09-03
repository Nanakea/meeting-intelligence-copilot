import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyIssueDraftReviewOverlay,
  bindRegeneratedDraftToMeeting,
  createIssueDraftReviewOverlay,
  rebaseIssueDraftReviewOverlay,
} from '../../src/services/issueDraftReview.ts';
import type { StructuredIssueDraft } from '../../src/services/intelligenceContracts.ts';

function draft(version: number, impact: string[] = ['Orders are delayed']): StructuredIssueDraft {
  return {
    draft_id: `draft-${version}`,
    session_id: 'meeting-intel-0123456789abcdef0123456789abcdef',
    pain_id: 'opaque-problem',
    state_version: version,
    template_id: 'integration_failure',
    language: 'en',
    title: 'Order integration failure',
    problem: ['Orders fail during transfer'],
    impact,
    affected_systems: ['SAP', 'Salesforce'],
    scope: [],
    owner: null,
    workaround: [],
    acceptance_criteria: [],
    facts: [],
    history_warnings: [],
    unresolved_questions: [],
    context_citations: [],
    context_status: 'not_requested',
    readiness: 'needs_clarification',
    missing_required_fields: ['owner'],
  };
}

test('review edits reapply only when the generated base field is unchanged', () => {
  const base = draft(1);
  const overlay = createIssueDraftReviewOverlay('meeting-1', base, '2026-01-01T00:00:00Z');
  overlay.field_edits.impact = ['Reviewer-approved impact'];
  overlay.status = 'in_review';

  const rebased = rebaseIssueDraftReviewOverlay(overlay, draft(2));
  const reviewed = applyIssueDraftReviewOverlay(draft(2), rebased) as StructuredIssueDraft & {
    review_provenance?: { user_authored_fields: string[] };
  };

  assert.equal(rebased.status, 'in_review');
  assert.deepEqual(rebased.rebase_conflicts, {});
  assert.deepEqual(reviewed.impact, ['Reviewer-approved impact']);
  assert.deepEqual(reviewed.review_provenance?.user_authored_fields, ['impact']);
});

test('changed generated fields require explicit rebase resolution before export', () => {
  const base = draft(1);
  const overlay = createIssueDraftReviewOverlay('meeting-1', base, '2026-01-01T00:00:00Z');
  overlay.field_edits.impact = ['Reviewer-approved impact'];
  overlay.status = 'reviewed';

  const changed = draft(2, ['A newly stated impact']);
  const rebased = rebaseIssueDraftReviewOverlay(overlay, changed);

  assert.equal(rebased.status, 'needs_rebase');
  assert.deepEqual(rebased.rebase_conflicts.impact, {
    generated_value: ['A newly stated impact'],
    reviewed_value: ['Reviewer-approved impact'],
  });
  assert.equal(applyIssueDraftReviewOverlay(changed, rebased), changed);
});

test('post-meeting replay keeps the saved problem identity stable', () => {
  const replaySession = 'meeting-intel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
  const savedMeeting = 'meeting-intel-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
  const replayDraft = {
    ...draft(3),
    session_id: replaySession,
    pain_id: `${replaySession}::integration_failure::pi-0123456789abcdef0123`,
  };

  const rebound = bindRegeneratedDraftToMeeting(
    replayDraft,
    replaySession,
    savedMeeting,
  );

  assert.equal(rebound.session_id, savedMeeting);
  assert.equal(
    rebound.pain_id,
    `${savedMeeting}::integration_failure::pi-0123456789abcdef0123`,
  );
});
