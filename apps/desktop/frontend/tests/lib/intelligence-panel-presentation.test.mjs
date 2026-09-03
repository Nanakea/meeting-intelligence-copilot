import assert from 'node:assert/strict';
import test from 'node:test';

const presentation = import('../../src/components/IntelligencePanel/presentation.ts');

function meetingState(overrides = {}) {
  return {
    meeting_id: 'meeting-1',
    version: 4,
    transcript: [],
    pain_points: [],
    facts: [],
    gaps: [],
    suggestions: [],
    last_event_seq: 3,
    ...overrides,
  };
}

function suggestion(suggestionId, role, status = 'active') {
  return {
    suggestion_id: suggestionId,
    gap_id: `gap-${suggestionId}`,
    role,
    text: `Question ${suggestionId}`,
    reason: `Reason ${suggestionId}`,
    status,
    priority: 1,
  };
}

test('renders at most one active ASK NOW and two active follow-ups', async () => {
  const { buildIntelligencePanelView } = await presentation;
  const view = buildIntelligencePanelView(
    meetingState({
      suggestions: [
        suggestion('ask-1', 'ask_now'),
        suggestion('ask-2', 'ask_now'),
        suggestion('ask-old', 'ask_now', 'retracted'),
        suggestion('follow-1', 'follow_up'),
        suggestion('follow-2', 'follow_up'),
        suggestion('follow-3', 'follow_up'),
        suggestion('follow-old', 'follow_up', 'retracted'),
      ],
    }),
  );

  assert.equal(view.askNow?.suggestion_id, 'ask-1');
  assert.deepEqual(
    view.followUps.map((item) => item.suggestion.suggestion_id),
    ['follow-1', 'follow-2'],
  );
});

test('retracted questions and inactive facts never reappear in the panel view', async () => {
  const { buildIntelligencePanelView } = await presentation;
  const view = buildIntelligencePanelView(
    meetingState({
      suggestions: [suggestion('answered-owner', 'ask_now', 'retracted')],
      pain_points: [{ pain_id: 'pain-1', title: 'Issue', status: 'open' }],
      facts: [
        { fact_id: 'active', pain_id: 'pain-1', status: 'active' },
        { fact_id: 'old', pain_id: 'pain-1', status: 'superseded' },
        { fact_id: 'wrong', pain_id: 'pain-1', status: 'contradicted' },
      ],
      gaps: [
        { gap_id: 'open', pain_id: 'pain-1', status: 'open' },
        { gap_id: 'answered', pain_id: 'pain-1', status: 'answered' },
      ],
    }),
  );

  assert.equal(view.askNow, null);
  assert.deepEqual(view.facts.map((fact) => fact.fact_id), ['active']);
  assert.deepEqual(view.openGaps.map((gap) => gap.gap_id), ['open']);
});

test('prefers the current open pain and keeps multilingual user-facing copy', async () => {
  const { buildIntelligencePanelView, PANEL_COPY, sidecarUnavailableDetail, slotLabel } = await presentation;
  const view = buildIntelligencePanelView(
    meetingState({
      pain_points: [
        { pain_id: 'mitigated', status: 'mitigated' },
        { pain_id: 'open', status: 'open' },
      ],
    }),
  );

  assert.equal(view.pain?.pain_id, 'open');
  assert.equal(slotLabel('source_of_truth', 'ja'), '在庫の基準');
  assert.equal(slotLabel('owner', 'en'), 'Owner');
  assert.equal(slotLabel('source_of_truth', 'ko'), '기준 정보');
  assert.equal(slotLabel('future_internal_slot', 'ja'), 'その他の情報');
  assert.equal(slotLabel('future_internal_slot', 'en'), 'Other detail');
  assert.equal(slotLabel('future_internal_slot', 'ko'), '기타 정보');
  assert.equal(PANEL_COPY.ja.askNow, '今すぐ聞く');
  assert.match(PANEL_COPY.ja.contextResetDetail, /古い質問は表示しません/);
  assert.match(PANEL_COPY.en.recoveringDetail, /ASK NOW stays hidden/);
  assert.equal(PANEL_COPY.en.retryBackend, 'Retry now');
  assert.match(PANEL_COPY.en.checkingSuggestionDetail, /local feedback/);
  assert.match(PANEL_COPY.ja.automaticRetriesStopped, /手動で再試行/);
  assert.equal(PANEL_COPY.en.localOnly, 'Local only');
  assert.equal(PANEL_COPY.ko.askNow, '지금 질문');
  assert.match(PANEL_COPY.ko.recoveringDetail, /문맥이 안전하게 복구/);
  assert.match(
    sidecarUnavailableDetail(
      {
        phase: 'unavailable',
        reason: 'missing_executable',
        retryCount: 3,
        maxRetryCount: 3,
        retryAfterMs: null,
        authEnabled: true,
        managed: true,
        backendVersion: null,
      },
      'en',
    ),
    /executable is missing.*Automatic retries reached their limit/,
  );
  assert.match(
    sidecarUnavailableDetail(
      {
        phase: 'restarting',
        reason: 'incompatible_backend',
        retryCount: 2,
        maxRetryCount: 3,
        retryAfterMs: 2000,
        authEnabled: true,
        managed: true,
        backendVersion: null,
      },
      'ja',
    ),
    /互換性.*2\/3/,
  );
});

test('anchors known and missing information to ASK NOW while labelling cross-topic follow-ups', async () => {
  const { buildIntelligencePanelView } = await presentation;
  const ask = suggestion('ask-a', 'ask_now');
  ask.gap_id = 'gap-a';
  const follow = suggestion('follow-b', 'follow_up');
  follow.gap_id = 'gap-b';
  const view = buildIntelligencePanelView(meetingState({
    pain_points: [
      { pain_id: 'pain-a', title: 'Inventory mismatch', status: 'open' },
      { pain_id: 'pain-b', title: 'API failure', status: 'open' },
    ],
    suggestions: [ask, follow],
    gaps: [
      { gap_id: 'gap-a', pain_id: 'pain-a', status: 'open' },
      { gap_id: 'gap-b', pain_id: 'pain-b', status: 'open' },
    ],
    facts: [
      { fact_id: 'fact-a', pain_id: 'pain-a', status: 'active' },
      { fact_id: 'fact-b', pain_id: 'pain-b', status: 'active' },
    ],
  }));

  assert.equal(view.pain?.pain_id, 'pain-a');
  assert.deepEqual(view.facts.map((fact) => fact.fact_id), ['fact-a']);
  assert.deepEqual(view.openGaps.map((gap) => gap.gap_id), ['gap-a']);
  assert.equal(view.followUps[0].topicTitle, 'API failure');

  const attemptedOverride = buildIntelligencePanelView(meetingState({
    pain_points: [
      { pain_id: 'pain-a', title: 'Inventory mismatch', status: 'open' },
      { pain_id: 'pain-b', title: 'API failure', status: 'open' },
    ],
    suggestions: [ask],
    gaps: [{ gap_id: 'gap-a', pain_id: 'pain-a', status: 'open' }],
    facts: [
      { fact_id: 'fact-a', pain_id: 'pain-a', status: 'active' },
      { fact_id: 'fact-b', pain_id: 'pain-b', status: 'active' },
    ],
  }), 'pain-b');
  assert.equal(attemptedOverride.pain?.pain_id, 'pain-a');
  assert.deepEqual(attemptedOverride.facts.map((fact) => fact.fact_id), ['fact-a']);
});
