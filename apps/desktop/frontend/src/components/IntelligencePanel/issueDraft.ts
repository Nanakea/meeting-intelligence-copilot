import type { IntelligencePanelView, PanelLanguage } from './presentation.ts';
import { slotLabel } from './presentation.ts';
import type { PreparedIssueDraft } from '../../services/intelligenceIssueDraft.ts';
import type { ContextCitation } from '../../services/intelligenceContracts.ts';
import type { StructuredIssueDraft } from '../../services/intelligenceContracts.ts';

const COPY = {
  ja: {
    currentUnderstanding: '現時点でわかっていること',
    openQuestions: '起票前に確認したいこと',
    readiness: '起票準備状況',
    noFacts: '確認済みの詳細はまだありません。',
    noQuestions: '未確認事項はありません。',
    ready: '起票に必要な主要情報がそろっています。',
    needsClarification: (count: number) => `未確認事項が${count}件あります。`,
    stated: '発言あり',
    inferred: '推測',
    externalContext: '外部システムの参照情報（会議での発言ではありません）',
    stale: '古い可能性あり',
    clarify: (label: string) => `${label}を確認する`,
  },
  en: {
    currentUnderstanding: 'Current understanding',
    openQuestions: 'Questions before filing',
    readiness: 'Issue readiness',
    noFacts: 'No confirmed details yet.',
    noQuestions: 'No open questions remain.',
    ready: 'The main information needed to file is present.',
    needsClarification: (count: number) => `${count} open ${count === 1 ? 'question remains' : 'questions remain'}.`,
    stated: 'stated',
    inferred: 'inferred',
    externalContext: 'External context (not stated in the meeting)',
    stale: 'may be stale',
    clarify: (label: string) => `Clarify ${label.toLowerCase()}`,
  },
  ko: {
    currentUnderstanding: '현재 확인된 내용',
    openQuestions: '이슈 등록 전 확인할 질문',
    readiness: '이슈 준비 상태',
    noFacts: '아직 확인된 세부 정보가 없습니다.',
    noQuestions: '열린 질문이 없습니다.',
    ready: '이슈 검토에 필요한 주요 정보가 준비되었습니다.',
    needsClarification: (count: number) => `확인할 항목이 ${count}개 남아 있습니다.`,
    stated: '발언 근거',
    inferred: '추론',
    externalContext: '외부 시스템 참조 정보(회의에서 발언된 내용이 아님)',
    stale: '최신 정보가 아닐 수 있음',
    clarify: (label: string) => `${label} 확인`,
  },
} as const;

function markdownText(value: string): string {
  return value
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/([\\`*_[\]<>#])/g, '\\$1');
}

export function buildIssueDraft(
  view: IntelligencePanelView,
  lang: PanelLanguage,
  citations: ContextCitation[] = [],
): PreparedIssueDraft | null {
  if (!view.pain) return null;

  const t = COPY[lang];
  const suggestedGapIds = new Set<string>();
  const questions: string[] = [];
  if (view.askNow) {
    questions.push(view.askNow.text);
    suggestedGapIds.add(view.askNow.gap_id);
  }
  for (const { suggestion, topicTitle } of view.followUps) {
    if (topicTitle === null) {
      questions.push(suggestion.text);
      suggestedGapIds.add(suggestion.gap_id);
    }
  }
  for (const gap of view.openGaps) {
    if (!suggestedGapIds.has(gap.gap_id)) {
      questions.push(t.clarify(slotLabel(gap.slot, lang)));
    }
  }

  const factLines = view.facts.map((fact) => {
    const provenance = fact.kind === 'inference' ? t.inferred : t.stated;
    return `- **${markdownText(slotLabel(fact.slot, lang))}:** ${markdownText(fact.value)} _(${provenance})_`;
  });
  const questionLines = questions.map((question) => `- ${markdownText(question)}`);
  const citationLines = citations.map((citation) => {
    const freshness = citation.stale ? ` _(${t.stale})_` : '';
    return `- **${markdownText(citation.source_label)}:** ${markdownText(citation.excerpt)}${freshness}`;
  });
  const unresolvedCount = view.openGaps.length;
  const title = markdownText(view.pain.title);
  const markdown = [
    `# ${title}`,
    '',
    `## ${t.currentUnderstanding}`,
    factLines.length > 0 ? factLines.join('\n') : t.noFacts,
    '',
    `## ${t.openQuestions}`,
    questionLines.length > 0 ? questionLines.join('\n') : t.noQuestions,
    ...(citationLines.length > 0
      ? ['', `## ${t.externalContext}`, citationLines.join('\n')]
      : []),
    '',
    `## ${t.readiness}`,
    unresolvedCount === 0 ? t.ready : t.needsClarification(unresolvedCount),
  ].join('\n');

  return {
    title,
    markdown,
    unresolvedCount,
    ready: unresolvedCount === 0,
    language: lang,
  };
}

export function renderStructuredIssueDraft(
  draft: StructuredIssueDraft,
  includeConnectedExcerpts = false,
): PreparedIssueDraft {
  const t = COPY[draft.language];
  const factLines = draft.facts.map((fact) => {
    const provenance = fact.kind === 'inference' ? t.inferred : t.stated;
    return `- **${markdownText(slotLabel(fact.slot, draft.language))}:** ${markdownText(fact.value)} _(${provenance})_`;
  });
  const questionLines = draft.unresolved_questions.map(
    (question) => `- ${markdownText(question.question)}`,
  );
  const citationLines = draft.context_citations.map((citation) => {
    const relation = markdownText(citation.relation);
    const freshness = citation.stale ? `; ${t.stale}` : '';
    const excerpt = includeConnectedExcerpts ? ` — ${markdownText(citation.excerpt)}` : '';
    return `- **${markdownText(citation.source_label)} / ${markdownText(citation.source_reference)}** _(${relation}${freshness})_${excerpt}`;
  });
  const historyLines = draft.history_warnings.map(
    (fact) => `- **${markdownText(slotLabel(fact.slot, draft.language))}:** ${markdownText(fact.value)} _(${fact.status})_`,
  );
  const markdown = [
    `# ${markdownText(draft.title)}`,
    '',
    `## ${t.currentUnderstanding}`,
    factLines.length > 0 ? factLines.join('\n') : t.noFacts,
    '',
    `## ${t.openQuestions}`,
    questionLines.length > 0 ? questionLines.join('\n') : t.noQuestions,
    ...(historyLines.length > 0 ? ['', '## History', historyLines.join('\n')] : []),
    ...(citationLines.length > 0
      ? ['', `## ${t.externalContext}`, citationLines.join('\n')]
      : []),
    '',
    `## ${t.readiness}`,
    draft.readiness === 'ready_for_review'
      ? t.ready
      : t.needsClarification(draft.missing_required_fields.length),
  ].join('\n');
  return {
    title: draft.title,
    markdown,
    unresolvedCount: draft.unresolved_questions.length,
    ready: draft.readiness === 'ready_for_review',
    language: draft.language,
  };
}
