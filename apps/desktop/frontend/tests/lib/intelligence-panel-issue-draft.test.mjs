import assert from 'node:assert/strict';
import test from 'node:test';

const issueDraftModule = import('../../src/components/IntelligencePanel/issueDraft.ts');

test('renders Korean issue draft labels without falling back to English', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(
    {
      pain: { title: '시스템 간 데이터 불일치' },
      askNow: null,
      followUps: [],
      facts: [
        { fact_id: 'fact-1', slot: 'source_of_truth', value: 'WMS', kind: 'evidence' },
      ],
      openGaps: [{ gap_id: 'gap-1', slot: 'owner' }],
    },
    'ko',
  );

  assert.match(draft.markdown, /현재 확인된 내용/);
  assert.match(draft.markdown, /기준 정보/);
  assert.match(draft.markdown, /담당자 확인/);
});

function panelView(overrides = {}) {
  return {
    pains: [],
    pain: { pain_id: 'pain-internal', title: 'Inventory mismatch', status: 'open' },
    askNow: {
      suggestion_id: 'suggestion-internal',
      gap_id: 'gap-owner',
      role: 'ask_now',
      text: 'Who owns resolving this mismatch?',
      reason: 'The owner is unclear.',
      status: 'active',
      priority: 0,
    },
    askNowGap: { gap_id: 'gap-owner', pain_id: 'pain-internal', slot: 'owner', status: 'open' },
    followUps: [],
    facts: [
      {
        fact_id: 'fact-internal',
        pain_id: 'pain-internal',
        slot: 'business_impact',
        value: 'Orders\nship late',
        kind: 'evidence',
        status: 'active',
        evidence_event_ids: ['event-internal'],
      },
      {
        fact_id: 'inference-internal',
        pain_id: 'pain-internal',
        slot: 'frequency',
        value: 'Likely daily',
        kind: 'inference',
        status: 'active',
        evidence_event_ids: ['event-other'],
      },
    ],
    openGaps: [
      { gap_id: 'gap-owner', pain_id: 'pain-internal', slot: 'owner', status: 'open' },
      { gap_id: 'gap-scope', pain_id: 'pain-internal', slot: 'affected_scope', status: 'open' },
    ],
    ...overrides,
  };
}

test('builds a concise issue draft from visible active context without internal identifiers', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(panelView(), 'en');

  assert.equal(draft?.title, 'Inventory mismatch');
  assert.equal(draft?.unresolvedCount, 2);
  assert.equal(draft?.ready, false);
  assert.match(draft?.markdown ?? '', /Business impact.*Orders ship late.*stated/);
  assert.match(draft?.markdown ?? '', /Frequency.*Likely daily.*inferred/);
  assert.match(draft?.markdown ?? '', /Who owns resolving this mismatch\?/);
  assert.match(draft?.markdown ?? '', /Clarify affected scope/);
  assert.doesNotMatch(
    draft?.markdown ?? '',
    /pain-internal|fact-internal|event-internal|suggestion-internal|gap-owner/,
  );
});

test('excludes cross-topic follow-ups and reports a complete resolved draft', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(panelView({
    askNow: null,
    askNowGap: null,
    openGaps: [],
    followUps: [{
      suggestion: {
        suggestion_id: 'other-suggestion',
        gap_id: 'other-gap',
        role: 'follow_up',
        text: 'Which API is failing?',
        reason: 'Another topic.',
        status: 'active',
        priority: 1,
      },
      topicTitle: 'API failure',
    }],
  }), 'en');

  assert.equal(draft?.ready, true);
  assert.equal(draft?.unresolvedCount, 0);
  assert.match(draft?.markdown ?? '', /No open questions remain/);
  assert.doesNotMatch(draft?.markdown ?? '', /Which API is failing/);
});

test('a hidden ASK NOW occurrence falls back to its generic open gap wording', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(panelView({ askNow: null, askNowGap: null }), 'en');

  assert.doesNotMatch(draft?.markdown ?? '', /Who owns resolving this mismatch/);
  assert.match(draft?.markdown ?? '', /Clarify owner/);
  assert.match(draft?.markdown ?? '', /Clarify affected scope/);
});

test('dismissal does not mutate the authoritative issue draft', async () => {
  const source = await import('node:fs/promises').then(({ readFile }) => readFile(
    new URL('../../src/components/IntelligencePanel/IntelligencePanel.tsx', import.meta.url),
    'utf8',
  ));

  assert.match(source, /resolveIssueDraftBatch/);
  assert.match(source, /cacheLiveStructuredIssueDrafts\(sessionId, batch\.drafts\)/);
  assert.doesNotMatch(source, /buildIssueDraft\(\{ \.\.\.view, askNow: null/);
});

test('localizes Japanese issue structure and returns null without a focused pain', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(panelView({
    pain: { pain_id: 'pain-ja', title: 'プロセスの遅延', status: 'open' },
    askNow: null,
    askNowGap: null,
    openGaps: [],
  }), 'ja');

  assert.match(draft?.markdown ?? '', /現時点でわかっていること/);
  assert.match(draft?.markdown ?? '', /起票に必要な主要情報がそろっています/);
  assert.equal(buildIssueDraft(panelView({ pain: null }), 'ja'), null);
});

test('keeps cited external context separate from meeting facts in issue drafts', async () => {
  const { buildIssueDraft } = await issueDraftModule;
  const draft = buildIssueDraft(panelView(), 'en', [{
    citation_id: 'citation-private',
    connector_id: 'sap-private',
    source_kind: 'sap',
    source_label: 'SAP delivery policy',
    record_id: 'record-private',
    entity_type: 'policy',
    excerpt: 'Configured lead time is fourteen days.',
    retrieved_at: '2026-08-22T00:00:00Z',
    stale: true,
  }]);

  assert.match(draft?.markdown ?? '', /External context \(not stated in the meeting\)/);
  assert.match(draft?.markdown ?? '', /SAP delivery policy.*fourteen days.*may be stale/);
  assert.doesNotMatch(draft?.markdown ?? '', /citation-private|sap-private|record-private/);
});
