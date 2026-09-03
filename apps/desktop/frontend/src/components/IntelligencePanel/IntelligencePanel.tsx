// Private live question copilot. This component renders backend-owned state
// and presentation-only view data; it never computes gap lifecycle or mutates
// MeetingState. ASK NOW remains the single dominant action.

import { useEffect, useState } from 'react';

import { useIntelligencePanel } from '@/hooks/useIntelligencePanel';
import { useProblemContext } from '@/hooks/useProblemContext';
import {
  decideProblemIdentity,
  decideSemanticHint,
  recordContextCitationFeedback,
  resolveIssueDraft,
  resolveIssueDraftBatch,
} from '@/services/contextConnectorService';
import { reviewGovernanceCandidate } from '@/services/governanceService';
import {
  isIntelligenceSuggestionDismissed,
  recordIntelligenceFeedback,
} from '@/services/intelligenceFeedback';
import type { IntelligenceIngressStatus } from '@/services/recordingService';
import { cacheLiveIssueDraft } from '@/services/liveIssueDraftCache';
import { cacheLiveStructuredIssueDrafts } from '@/services/liveStructuredIssueDraftCache';
import type { ConnectorKind, StructuredIssueDraft } from '@/services/intelligenceContracts';
import {
  DEFAULT_ISSUE_EXPORT_SETTINGS,
  exportIssueDraft,
  getIssueExportSettings,
  setIssueExportSettings,
  type IssueExportFormat,
  type IssueExportSettings,
} from '@/services/intelligenceIssueDraft';

import { renderStructuredIssueDraft } from './issueDraft';
import {
  buildIntelligencePanelView,
  PANEL_COPY,
  sidecarUnavailableDetail,
  slotLabel,
  type PanelLanguage,
} from './presentation';

const PANEL_CLASS =
  'h-full w-[min(22rem,42vw)] min-w-[16rem] shrink-0 overflow-y-auto border-l border-gray-200 bg-white px-4 py-4 custom-scrollbar max-[700px]:h-[45%] max-[700px]:w-full max-[700px]:min-w-0 max-[700px]:border-l-0 max-[700px]:border-t';

const CONTEXT_COPY = {
  ja: {
    heading: '外部システムの参照情報',
    disclaimer: '会議での発言ではなく、読み取り専用の参照情報です。',
    fresh: '最新',
    stale: '古い可能性あり',
    retrieved: '取得',
    unavailable: '一部の接続先を確認できませんでした。会議の質問表示は継続します。',
    reference: '参照',
    supporting: '一致',
    conflicting: '要確認',
    relevant: '関連あり',
    notRelevant: '関連なし',
    openSource: '元データを開く',
    confirmOpen: '外部システムを開きますか？',
  },
  en: {
    heading: 'External context',
    disclaimer: 'Read-only reference information, not a statement made in the meeting.',
    fresh: 'Fresh',
    stale: 'May be stale',
    retrieved: 'Retrieved',
    unavailable: 'Some connections could not be checked. Meeting questions remain available.',
    reference: 'Reference',
    supporting: 'Matches',
    conflicting: 'Check conflict',
    relevant: 'Relevant',
    notRelevant: 'Not relevant',
    openSource: 'Open source',
    confirmOpen: 'Open this external system?',
  },
  ko: {
    heading: '외부 시스템 참조 정보',
    disclaimer: '회의 발언이 아닌 읽기 전용 참조 정보입니다.',
    fresh: '최신',
    stale: '최신 정보가 아닐 수 있음',
    retrieved: '조회 시각',
    unavailable: '일부 연결을 확인하지 못했습니다. 회의 질문은 계속 사용할 수 있습니다.',
    reference: '참조',
    supporting: '일치',
    conflicting: '충돌 확인',
    relevant: '관련 있음',
    notRelevant: '관련 없음',
    openSource: '원본 열기',
    confirmOpen: '이 외부 시스템을 여시겠습니까?',
  },
} as const;

const CONTEXT_SOURCE_LABEL: Record<ConnectorKind, string> = {
  local_files: 'Local files',
  microsoft_graph: 'Microsoft 365',
  odata: 'OData',
  sap: 'SAP',
  dynamics365: 'Dynamics 365',
  netsuite: 'NetSuite',
  microsoft_teams: 'Microsoft Teams',
  slack: 'Slack',
  notion: 'Notion',
  wms: 'WMS',
  amazon_fba: 'Amazon FBA',
  github: 'GitHub',
  gitlab: 'GitLab',
  jira: 'Jira',
  confluence: 'Confluence',
  azure_devops: 'Azure DevOps',
  servicenow: 'ServiceNow',
  sql: 'Approved SQL views',
  sftp: 'SFTP',
  openapi: 'OpenAPI',
};

export interface IntelligencePanelProps {
  sessionId: string | null;
  lang: 'ja' | 'en' | 'ko' | null;
  enabled: boolean;
  ingressStatus?: IntelligenceIngressStatus;
  paused?: boolean;
}

interface PanelStateProps {
  title: string;
  detail: string;
  tone: 'neutral' | 'amber' | 'red';
  pulse?: boolean;
}

function PanelState({ title, detail, tone, pulse = false }: PanelStateProps) {
  const toneClasses = {
    neutral: 'border-gray-200 bg-gray-50 text-gray-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-900',
    red: 'border-rose-200 bg-rose-50 text-rose-900',
  } as const;
  const dotClasses = {
    neutral: 'bg-indigo-500',
    amber: 'bg-amber-500',
    red: 'bg-rose-500',
  } as const;

  return (
    <div
      className={`rounded-xl border p-4 ${toneClasses[tone]}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${dotClasses[tone]} ${pulse ? 'animate-pulse' : ''}`}
          aria-hidden="true"
        />
        <div className="min-w-0">
          <p className="text-sm font-semibold leading-snug">{title}</p>
          <p className="mt-1 text-xs leading-relaxed opacity-75">{detail}</p>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({ lang }: { lang: PanelLanguage }) {
  const t = PANEL_COPY[lang];

  return (
    <header className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="break-words text-sm font-semibold text-gray-900">{t.copilot}</h2>
        <p className="mt-0.5 break-words text-[11px] leading-relaxed text-gray-500">
          {t.privatePrompt}
        </p>
      </div>
      <span
        className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700"
        title={t.localOnlyDetail}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
        {t.localOnly}
      </span>
    </header>
  );
}

export function IntelligencePanel({
  sessionId,
  lang,
  enabled,
  ingressStatus = 'ready',
  paused = false,
}: IntelligencePanelProps) {
  const ingressReady = ingressStatus === 'ready';
  const {
    state,
    status,
    sidecarStatus,
    snapshotMarkerRef,
    latestStateVersionRef,
    retry,
  } = useIntelligencePanel(
    sessionId,
    lang,
    enabled && ingressReady,
  );
  const [selectedPainId, setSelectedPainId] = useState<string | null>(null);
  const [dismissedOccurrence, setDismissedOccurrence] = useState<string | null>(null);
  const [dismissalCheckedOccurrence, setDismissalCheckedOccurrence] = useState<string | null>(null);
  const [usefulOccurrence, setUsefulOccurrence] = useState<string | null>(null);
  const [feedbackSaveFailed, setFeedbackSaveFailed] = useState<string | null>(null);
  const [showIssueDraft, setShowIssueDraft] = useState(false);
  const [issueCopyStatus, setIssueCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [questionCopyStatus, setQuestionCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [citationFeedback, setCitationFeedback] = useState<Record<string, string>>({});
  const [authoritativeDrafts, setAuthoritativeDrafts] = useState<StructuredIssueDraft[]>([]);
  const [issueExportFormat, setIssueExportFormat] = useState<IssueExportFormat>('markdown');
  const [issueExportSettings, setIssueExportSettingsState] = useState<IssueExportSettings>(DEFAULT_ISSUE_EXPORT_SETTINGS);
  const [includeConnectedExcerpts, setIncludeConnectedExcerpts] = useState(false);
  const [reviewActionPending, setReviewActionPending] = useState(false);
  const [governanceReviewFailed, setGovernanceReviewFailed] = useState(false);
  const panelLang: PanelLanguage = lang === 'ja' ? 'ja' : lang === 'ko' ? 'ko' : 'en';
  const t = PANEL_COPY[panelLang];
  const exportCopy = panelLang === 'ja'
    ? { export: 'ファイルに書き出す', include: '外部参照の抜粋を含める', type: '作業項目タイプ' }
    : panelLang === 'ko'
      ? { export: '파일로 내보내기', include: '외부 참조 발췌문 포함', type: '작업 항목 유형' }
      : { export: 'Export file', include: 'Include connected excerpts', type: 'Work item type' };
  const view = buildIntelligencePanelView(state, selectedPainId);
  const unavailableDetail = sidecarUnavailableDetail(sidecarStatus, panelLang);
  const reopenCount = view.askNowGap?.reopen_count ?? 0;
  const askSuggestionId = view.askNow?.suggestion_id ?? null;
  const askOccurrence = askSuggestionId
    ? `${askSuggestionId}:${reopenCount}`
    : null;
  const dismissalLookupPending =
    askOccurrence !== null && dismissalCheckedOccurrence !== askOccurrence;
  const visibleAskNow =
    dismissalLookupPending || (askOccurrence && askOccurrence === dismissedOccurrence)
      ? null
      : view.askNow;
  const problemContext = useProblemContext(
    sessionId,
    view.pain?.pain_id ?? null,
    state.version,
    status === 'connected' || status === 'recovered',
  );
  const focusedStructuredDraft = authoritativeDrafts.find(
    (draft) => draft.pain_id === view.pain?.pain_id,
  ) ?? null;
  const issueDraft = focusedStructuredDraft
    ? renderStructuredIssueDraft(focusedStructuredDraft)
    : null;
  const issueDraftTitle = issueDraft?.title;
  const issueDraftMarkdown = issueDraft?.markdown;
  const issueDraftUnresolvedCount = issueDraft?.unresolvedCount;
  const issueDraftReady = issueDraft?.ready;
  const issueDraftLanguage = issueDraft?.language;

  useEffect(() => {
    void getIssueExportSettings().then(setIssueExportSettingsState).catch(() => undefined);
  }, []);

  useEffect(() => {
    setSelectedPainId(null);
    setDismissedOccurrence(null);
    setDismissalCheckedOccurrence(null);
    setUsefulOccurrence(null);
    setFeedbackSaveFailed(null);
    setShowIssueDraft(false);
    setIssueCopyStatus('idle');
    setQuestionCopyStatus('idle');
    setAuthoritativeDrafts([]);
    setIncludeConnectedExcerpts(false);
    setGovernanceReviewFailed(false);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || state.version === 0 || state.pain_points.length === 0) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void resolveIssueDraftBatch({
        session_id: sessionId,
        pain_ids: state.pain_points.map((pain) => pain.pain_id),
        state_version: state.version,
        include_context: false,
      }, controller.signal).then((batch) => {
        if (controller.signal.aborted) return;
        setAuthoritativeDrafts(batch.drafts);
        cacheLiveStructuredIssueDrafts(sessionId, batch.drafts);
      }).catch(() => {
        // Draft preparation is supplemental and must never affect ASK NOW.
      });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [sessionId, state.pain_points, state.version]);

  useEffect(() => {
    setShowIssueDraft(false);
    setIssueCopyStatus('idle');
  }, [view.pain?.pain_id]);

  useEffect(() => {
    setIssueCopyStatus('idle');
  }, [state.version]);

  useEffect(() => {
    setQuestionCopyStatus('idle');
    setCitationFeedback({});
    setFeedbackSaveFailed(null);
  }, [askOccurrence]);

  useEffect(() => {
    if (
      sessionId &&
      issueDraftTitle !== undefined &&
      issueDraftMarkdown !== undefined &&
      issueDraftUnresolvedCount !== undefined &&
      issueDraftReady !== undefined &&
      issueDraftLanguage !== undefined
    ) {
      cacheLiveIssueDraft(sessionId, {
        title: issueDraftTitle,
        markdown: issueDraftMarkdown,
        unresolvedCount: issueDraftUnresolvedCount,
        ready: issueDraftReady,
        language: issueDraftLanguage,
      });
    }
  }, [
    issueDraftLanguage,
    issueDraftMarkdown,
    issueDraftReady,
    issueDraftTitle,
    issueDraftUnresolvedCount,
    sessionId,
  ]);

  useEffect(() => {
    setDismissalCheckedOccurrence(null);
    if (!sessionId || !askSuggestionId || !askOccurrence) return;
    let cancelled = false;
    void isIntelligenceSuggestionDismissed(
      sessionId,
      askSuggestionId,
      reopenCount,
    ).then((dismissed) => {
      if (!cancelled && dismissed) {
        setDismissedOccurrence(askOccurrence);
      }
      if (!cancelled) setDismissalCheckedOccurrence(askOccurrence);
    }).catch(() => {
      // A lookup failure must not hide a valid backend-owned question forever.
      if (!cancelled) setDismissalCheckedOccurrence(askOccurrence);
    });
    return () => {
      cancelled = true;
    };
  }, [askOccurrence, askSuggestionId, reopenCount, sessionId]);

  const recordFeedback = (action: 'useful' | 'dismissed') => {
    if (!sessionId || !view.askNow || !view.askNowGap || !askOccurrence) return;
    const pain = state.pain_points.find(
      (candidate) => candidate.pain_id === view.askNowGap?.pain_id,
    );
    if (!pain) return;
    if (action === 'dismissed') setDismissedOccurrence(askOccurrence);
    if (action === 'useful') setUsefulOccurrence(askOccurrence);
    setFeedbackSaveFailed(null);
    void recordIntelligenceFeedback({
      sessionId,
      suggestionId: view.askNow.suggestion_id,
      painTemplate: pain.template_id,
      slot: view.askNowGap.slot,
      action,
      stateVersion: latestStateVersionRef.current,
      reopenCount,
    }).catch(() => {
      if (action === 'dismissed') {
        setDismissedOccurrence((current) => current === askOccurrence ? null : current);
      }
      if (action === 'useful') {
        setUsefulOccurrence((current) => current === askOccurrence ? null : current);
      }
      setFeedbackSaveFailed(askOccurrence);
    });
  };

  const copyIssueDraft = async () => {
    if (!issueDraft) return;
    try {
      await navigator.clipboard.writeText(issueDraft.markdown);
      setIssueCopyStatus('copied');
    } catch {
      setIssueCopyStatus('failed');
    }
  };

  const copyAskNow = async () => {
    if (!visibleAskNow) return;
    try {
      await navigator.clipboard.writeText(visibleAskNow.text);
      setQuestionCopyStatus('copied');
    } catch {
      setQuestionCopyStatus('failed');
    }
  };

  const prepareIssueDraft = async () => {
    if (!sessionId || !view.pain || !focusedStructuredDraft) return;
    setShowIssueDraft(true);
    try {
      const enriched = await resolveIssueDraft({
        session_id: sessionId,
        pain_id: view.pain.pain_id,
        state_version: state.version,
        include_context: true,
      });
      const next = authoritativeDrafts.map((draft) => (
        draft.pain_id === enriched.pain_id ? enriched : draft
      ));
      setAuthoritativeDrafts(next);
      cacheLiveStructuredIssueDrafts(sessionId, next);
    } catch {
      // The already resolved meeting-only draft remains usable.
    }
  };

  const exportPreparedIssue = async () => {
    if (!focusedStructuredDraft) return;
    try {
      const savedSettings = await setIssueExportSettings(issueExportSettings);
      setIssueExportSettingsState(savedSettings);
      await exportIssueDraft(
        focusedStructuredDraft,
        issueExportFormat,
        {
          ...savedSettings,
          include_connected_excerpts: includeConnectedExcerpts,
        },
      );
    } catch {
      setIssueCopyStatus('failed');
    }
  };

  const openCitation = async (uri: string) => {
    try {
      const hostname = new URL(uri).hostname;
      if (!window.confirm(`${CONTEXT_COPY[panelLang].confirmOpen}\n${hostname}`)) return;
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('open_external_url', { url: uri });
    } catch {
      // Source-opening failures never affect the live question panel.
    }
  };

  const saveCitationFeedback = (
    citation: typeof problemContext.result.citations[number],
    action: 'relevant' | 'not_relevant',
  ) => {
    const feedbackKey = `${view.pain?.pain_id ?? 'none'}:${citation.citation_id}`;
    setCitationFeedback((current) => ({ ...current, [feedbackKey]: action }));
    void recordContextCitationFeedback({
      connector_kind: citation.source_kind,
      entity_type: citation.entity_type,
      rank: citation.rank,
      action,
      state_version: state.version,
      response_ms: problemContext.responseMs,
    }).catch(() => {
      setCitationFeedback((current) => {
        const next = { ...current };
        delete next[feedbackKey];
        return next;
      });
    });
  };

  const reviewSemanticHint = async (hintId: string, action: 'confirm' | 'dismiss') => {
    if (!sessionId || reviewActionPending) return;
    setReviewActionPending(true);
    try {
      await decideSemanticHint(hintId, {
        session_id: sessionId,
        state_version: state.version,
        action,
      });
    } finally {
      setReviewActionPending(false);
    }
  };

  const reviewIdentity = async (
    painIds: string[],
    action: 'merge' | 'keep_separate',
  ) => {
    if (!sessionId || reviewActionPending) return;
    setReviewActionPending(true);
    try {
      await decideProblemIdentity({
        session_id: sessionId,
        state_version: state.version,
        action,
        pain_ids: painIds,
      });
    } finally {
      setReviewActionPending(false);
    }
  };

  const reviewGovernance = async (
    candidateId: string,
    action: 'confirm' | 'dismiss',
  ) => {
    if (!sessionId || reviewActionPending) return;
    setReviewActionPending(true);
    setGovernanceReviewFailed(false);
    try {
      await reviewGovernanceCandidate(candidateId, {
        session_id: sessionId,
        state_version: state.version,
        action,
      });
    } catch {
      setGovernanceReviewFailed(true);
    } finally {
      setReviewActionPending(false);
    }
  };

  if (!enabled) return null;

  return (
    <aside
      ref={snapshotMarkerRef}
      className={PANEL_CLASS}
      aria-label={t.copilot}
      data-intelligence-status={status}
      data-intelligence-version={state.version}
      data-intelligence-last-event-seq={state.last_event_seq}
    >
      <PanelHeader lang={panelLang} />

      {!ingressReady && (
        <PanelState
          title={t.unavailable}
          detail={
            panelLang === 'ja'
              ? '選択した音声認識モデルと言語の組み合わせは対応していません。録音は継続します。'
              : panelLang === 'ko'
                ? '선택한 음성 인식 모델과 회의 언어 조합은 지원되지 않습니다. 녹음은 계속됩니다.'
              : 'The selected speech model and meeting language are not compatible. Recording continues.'
          }
          tone="amber"
        />
      )}

      {ingressReady && status === 'unavailable' && (
        <div className="space-y-3">
          <PanelState title={t.unavailable} detail={unavailableDetail} tone="red" />
          <button
            type="button"
            onClick={retry}
            className="w-full rounded-lg border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition-colors hover:bg-rose-50 focus:outline-none focus:ring-2 focus:ring-rose-400 focus:ring-offset-2"
          >
            {t.retryBackend}
          </button>
        </div>
      )}

      {ingressReady && status === 'recovering' && (
        <PanelState title={t.recovering} detail={t.recoveringDetail} tone="amber" pulse />
      )}

      {ingressReady && status === 'reset' && (
        <PanelState title={t.contextReset} detail={t.contextResetDetail} tone="amber" pulse />
      )}

      {ingressReady && (status === 'idle' || status === 'connecting') && (
        <PanelState title={t.connecting} detail={t.connectingDetail} tone="neutral" pulse />
      )}

      {ingressReady && (status === 'connected' || status === 'recovered') && (
        <div>
          {paused && (
            <div className="mb-3">
              <PanelState title={t.paused} detail={t.pausedDetail} tone="amber" />
            </div>
          )}
          {status === 'recovered' && (
            <div
              className="mb-3 flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700"
              role="status"
              aria-live="polite"
            >
              <span className="h-2 w-2 rounded-full bg-emerald-500" aria-hidden="true" />
              {t.recovered}
            </div>
          )}

          {state.governance_candidates.map((candidate) => (
            <section
              key={candidate.candidate_id}
              className="mb-3 rounded-xl border border-teal-200 bg-teal-50 p-3"
              aria-label={panelLang === 'ja' ? 'ガバナンス候補' : panelLang === 'ko' ? '거버넌스 후보' : 'Governance candidate'}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-teal-800">
                {panelLang === 'ja' ? '確認して記録' : panelLang === 'ko' ? '검토 후 기록' : 'Review before recording'}
              </p>
              <p className="mt-1 break-words text-sm font-semibold text-teal-950">
                {candidate.title}
              </p>
              <p className="mt-1 text-[11px] text-teal-800">
                {panelLang === 'ja'
                  ? '会議の発言に基づく候補です。確認するまで正式記録にはなりません。'
                  : panelLang === 'ko'
                    ? '회의 발언을 근거로 한 후보입니다. 확인 전에는 공식 기록이 아닙니다.'
                  : 'Transcript-backed candidate. It is not a record until you confirm it.'}
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewGovernance(candidate.candidate_id, 'confirm')}
                  className="rounded-lg bg-teal-900 px-3 py-1.5 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '確認して記録' : panelLang === 'ko' ? '확인하고 기록' : 'Confirm record'}
                </button>
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewGovernance(candidate.candidate_id, 'dismiss')}
                  className="rounded-lg border border-teal-300 bg-white px-3 py-1.5 text-xs font-semibold text-teal-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '候補を閉じる' : panelLang === 'ko' ? '후보 닫기' : 'Dismiss'}
                </button>
              </div>
            </section>
          ))}

          {governanceReviewFailed && (
            <p className="mb-3 text-xs font-medium text-rose-700" role="status">
              {panelLang === 'ja'
                ? '記録を更新できませんでした。最新の会議状態で再試行してください。'
                : panelLang === 'ko'
                  ? '기록을 갱신하지 못했습니다. 최신 회의 상태에서 다시 시도하십시오.'
                : 'The record was not updated. Retry against the latest meeting state.'}
            </p>
          )}

          {state.governance_alerts.length > 0 && (
            <section className="mb-3 rounded-xl border border-orange-200 bg-orange-50 p-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-orange-800">
                {panelLang === 'ja' ? 'フォローが必要' : panelLang === 'ko' ? '후속 조치 필요' : 'Needs follow-up'}
              </p>
              <ul className="mt-2 space-y-1 text-xs text-orange-950">
                {state.governance_alerts.slice(0, 3).map((alert) => (
                  <li key={alert.alert_id} className="break-words">{alert.label}</li>
                ))}
              </ul>
            </section>
          )}

          {state.semantic_hints.map((hint) => (
            <section
              key={hint.hint_id}
              className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-3"
              aria-label={panelLang === 'ja' ? '問題候補' : panelLang === 'ko' ? '문제 후보' : 'Possible problem'}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-800">
                {panelLang === 'ja' ? '問題候補（確認が必要）' : panelLang === 'ko' ? '문제 후보(추가 전 확인 필요)' : 'Possible problem (confirm to add)'}
              </p>
              <p className="mt-1 break-words text-sm font-semibold text-amber-950">
                {hint.subject}
              </p>
              {hint.facts.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-amber-900">
                  {hint.facts.slice(0, 3).map((fact) => (
                    <li key={`${fact.slot}:${fact.value}`} className="break-words">
                      <span className="font-semibold">{slotLabel(fact.slot, panelLang)}:</span>{' '}
                      {fact.value}
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewSemanticHint(hint.hint_id, 'confirm')}
                  className="rounded-lg bg-amber-900 px-3 py-1.5 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '会議状態に追加' : panelLang === 'ko' ? '확인 후 회의 상태에 추가' : 'Confirm and add'}
                </button>
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewSemanticHint(hint.hint_id, 'dismiss')}
                  className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '非表示' : panelLang === 'ko' ? '숨기기' : 'Dismiss'}
                </button>
              </div>
            </section>
          ))}

          {state.identity_review_candidates.map((candidate) => (
            <section
              key={candidate.review_id}
              className="mb-3 rounded-xl border border-sky-200 bg-sky-50 p-3"
              aria-label={panelLang === 'ja' ? '問題の重複確認' : panelLang === 'ko' ? '중복 문제 검토' : 'Problem identity review'}
            >
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-sky-800">
                {panelLang === 'ja' ? '同じ問題ですか？' : panelLang === 'ko' ? '같은 문제입니까?' : 'Are these the same problem?'}
              </p>
              <p className="mt-1 break-words text-xs text-sky-950">
                {candidate.first_subject} / {candidate.second_subject}
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewIdentity(
                    [candidate.first_pain_id, candidate.second_pain_id],
                    'merge',
                  )}
                  className="rounded-lg bg-sky-900 px-3 py-1.5 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '同じ問題' : panelLang === 'ko' ? '같은 문제' : 'Same problem'}
                </button>
                <button
                  type="button"
                  disabled={reviewActionPending}
                  onClick={() => void reviewIdentity(
                    [candidate.first_pain_id, candidate.second_pain_id],
                    'keep_separate',
                  )}
                  className="rounded-lg border border-sky-300 bg-white px-3 py-1.5 text-xs font-semibold text-sky-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 disabled:opacity-50"
                >
                  {panelLang === 'ja' ? '別の問題' : panelLang === 'ko' ? '별도 문제로 유지' : 'Keep separate'}
                </button>
              </div>
            </section>
          ))}

          {view.pain === null ? (
            <PanelState title={t.listening} detail={t.listeningDetail} tone="neutral" pulse />
          ) : (
            <>
              <section className="mb-3" aria-labelledby="intelligence-topic-heading">
                <h3
                  id="intelligence-topic-heading"
                  className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400"
                >
                  {t.detectedTopic}
                </h3>
                <p className="mt-1 break-words text-sm font-semibold leading-snug text-gray-800">
                  {view.pain.title}
                </p>
                {view.pains.length > 1 && (
                  <div className="mt-2 flex flex-wrap gap-1" aria-label={t.topics}>
                    {view.pains.map((pain) => (
                      <button
                        key={pain.pain_id}
                        type="button"
                        onClick={() => setSelectedPainId(pain.pain_id)}
                        disabled={view.askNowGap !== null}
                        aria-pressed={pain.pain_id === view.pain?.pain_id}
                        title={pain.title}
                        className={`max-w-full break-words rounded-full border px-2 py-1 text-left text-[10px] leading-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${
                          pain.pain_id === view.pain?.pain_id
                            ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                            : 'border-gray-200 bg-white text-gray-500 disabled:cursor-not-allowed disabled:opacity-60'
                        }`}
                      >
                        {pain.title}
                      </button>
                    ))}
                  </div>
                )}
              </section>

              {visibleAskNow === null ? (
                <PanelState
                  title={
                    dismissalLookupPending
                      ? t.checkingSuggestion
                      : dismissedOccurrence === askOccurrence
                        ? t.dismissed
                        : t.noOpenQuestion
                  }
                  detail={
                    dismissalLookupPending
                      ? t.checkingSuggestionDetail
                      : dismissedOccurrence === askOccurrence
                        ? t.dismissedDetail
                        : t.noOpenQuestionDetail
                  }
                  tone="neutral"
                  pulse={dismissalLookupPending}
                />
              ) : (
                <section
                  className="mb-4 rounded-2xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-white p-4 shadow-sm"
                  aria-labelledby="ask-now-heading"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  <h3
                    id="ask-now-heading"
                    className="text-[11px] font-bold uppercase tracking-[0.16em] text-indigo-600"
                  >
                    {t.askNow}
                  </h3>
                  <p className="mt-2 break-words text-lg font-bold leading-snug text-gray-950">
                    {visibleAskNow.text}
                  </p>
                  <p className="mt-3 break-words border-t border-indigo-100 pt-2 text-xs leading-relaxed text-gray-600">
                    <span className="font-semibold text-gray-700">{t.why}:</span>{' '}
                    {visibleAskNow.reason}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void copyAskNow()}
                      className="rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                      aria-live="polite"
                    >
                      {questionCopyStatus === 'copied'
                        ? t.copiedQuestion
                        : questionCopyStatus === 'failed'
                          ? t.copyQuestionFailed
                          : t.copyQuestion}
                    </button>
                    <button
                      type="button"
                      onClick={() => recordFeedback('useful')}
                      disabled={usefulOccurrence === askOccurrence}
                      aria-pressed={usefulOccurrence === askOccurrence}
                      className="rounded-lg border border-emerald-200 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 focus:outline-none focus:ring-2 focus:ring-emerald-400 disabled:cursor-default disabled:bg-emerald-50 disabled:text-emerald-600"
                    >
                      {usefulOccurrence === askOccurrence ? t.markedUseful : t.useful}
                    </button>
                    <button
                      type="button"
                      onClick={() => recordFeedback('dismissed')}
                      className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-400"
                    >
                      {t.dismiss}
                    </button>
                  </div>
                  {feedbackSaveFailed === askOccurrence && (
                    <p className="mt-2 text-xs font-medium text-rose-700" role="status">
                      {t.feedbackSaveFailed}
                    </p>
                  )}
                </section>
              )}

              {(problemContext.result.citations.length > 0 ||
                problemContext.result.unavailable_connector_ids.length > 0 ||
                problemContext.status === 'unavailable') && (
                <section
                  className="mb-4 rounded-xl border border-cyan-200 bg-cyan-50/70 p-3"
                  aria-labelledby="connected-context-heading"
                >
                  <h3
                    id="connected-context-heading"
                    className="text-[10px] font-bold uppercase tracking-[0.14em] text-cyan-800"
                  >
                    {CONTEXT_COPY[panelLang].heading}
                  </h3>
                  <p className="mt-1 text-[10px] leading-relaxed text-cyan-800/75">
                    {CONTEXT_COPY[panelLang].disclaimer}
                  </p>
                  {problemContext.result.citations.length > 0 && (
                    <ul className="mt-2 space-y-2">
                      {problemContext.result.citations.map((citation) => (
                        <li key={citation.citation_id} className="rounded-lg bg-white/80 px-3 py-2">
                          <div className="flex items-start justify-between gap-2">
                            <span className="break-words text-[11px] font-semibold text-cyan-950">
                              {citation.source_label}
                            </span>
                            <span
                              className={`shrink-0 text-[9px] font-semibold ${
                                citation.stale ? 'text-amber-700' : 'text-emerald-700'
                              }`}
                            >
                              {citation.stale
                                ? CONTEXT_COPY[panelLang].stale
                                : CONTEXT_COPY[panelLang].fresh}
                            </span>
                          </div>
                          <p className="mt-0.5 break-words text-[10px] font-medium text-cyan-800">
                            {citation.source_reference}
                            {' · '}
                            {CONTEXT_COPY[panelLang][citation.relation]}
                          </p>
                          <p className="mt-1 break-words text-xs leading-relaxed text-gray-700">
                            {citation.excerpt}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {citation.uri && (
                              <button
                                type="button"
                                onClick={() => void openCitation(citation.uri as string)}
                                className="rounded border border-cyan-200 bg-white px-2 py-1 text-[10px] font-semibold text-cyan-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-700 focus-visible:ring-offset-2"
                              >
                                {CONTEXT_COPY[panelLang].openSource}
                              </button>
                            )}
                            {(['relevant', 'not_relevant'] as const).map((action) => (
                              <button
                                key={action}
                                type="button"
                                aria-pressed={
                                  citationFeedback[
                                    `${view.pain?.pain_id ?? 'none'}:${citation.citation_id}`
                                  ]
                                  === action
                                }
                                disabled={
                                  citationFeedback[
                                    `${view.pain?.pain_id ?? 'none'}:${citation.citation_id}`
                                  ]
                                  === action
                                }
                                onClick={() => saveCitationFeedback(citation, action)}
                                className="rounded border border-gray-200 bg-white px-2 py-1 text-[10px] text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-700 focus-visible:ring-offset-2 aria-pressed:bg-gray-100"
                              >
                                {action === 'relevant'
                                  ? CONTEXT_COPY[panelLang].relevant
                                  : CONTEXT_COPY[panelLang].notRelevant}
                              </button>
                            ))}
                          </div>
                          <p className="mt-1 text-[9px] text-gray-500">
                            {CONTEXT_SOURCE_LABEL[citation.source_kind]}
                            {' · '}
                            {CONTEXT_COPY[panelLang].retrieved}{' '}
                            {new Intl.DateTimeFormat(panelLang === 'ja' ? 'ja-JP' : panelLang === 'ko' ? 'ko-KR' : 'en-US', {
                              dateStyle: 'medium',
                              timeStyle: 'short',
                            }).format(new Date(citation.retrieved_at))}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                  {(problemContext.result.unavailable_connector_ids.length > 0 ||
                    problemContext.status === 'unavailable') && (
                    <p className="mt-2 text-[10px] leading-relaxed text-amber-800" role="status">
                      {CONTEXT_COPY[panelLang].unavailable}
                    </p>
                  )}
                </section>
              )}

              {view.followUps.length > 0 && (
                <section className="mb-4" aria-labelledby="follow-up-heading">
                  <h3
                    id="follow-up-heading"
                    className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400"
                  >
                    {t.followUp}
                  </h3>
                  <ol className="mt-2 space-y-2">
                    {view.followUps.map(({ suggestion, topicTitle }, index) => (
                      <li
                        key={suggestion.suggestion_id}
                        className="flex min-w-0 items-start gap-2 text-sm leading-snug text-gray-700"
                      >
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-100 text-[10px] font-semibold text-gray-500">
                          {index + 1}
                        </span>
                        <span className="min-w-0 break-words pt-0.5">
                          {topicTitle && (
                            <span className="mr-1 font-semibold text-gray-500">
                              {topicTitle}:
                            </span>
                          )}
                          {suggestion.text}
                        </span>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              {view.facts.length > 0 && (
                <section className="border-t border-gray-100 pt-3" aria-labelledby="known-heading">
                  <h3
                    id="known-heading"
                    className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400"
                  >
                    {t.known}
                  </h3>
                  <ul className="mt-2 space-y-2">
                    {view.facts.map((fact) => (
                      <li key={fact.fact_id} className="rounded-lg bg-gray-50 px-3 py-2">
                        <div className="flex min-w-0 items-center justify-between gap-2">
                          <span className="min-w-0 break-words text-[11px] font-semibold text-gray-600">
                            {slotLabel(fact.slot, panelLang)}
                          </span>
                          <span className="shrink-0 text-[9px] font-medium text-gray-400">
                            {fact.kind === 'inference' ? t.inferred : t.stated}
                          </span>
                        </div>
                        <p className="mt-0.5 break-words text-xs leading-relaxed text-gray-700">
                          {fact.value}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {view.openGaps.length > 0 && (
                <section className="mt-3 border-t border-gray-100 pt-3" aria-labelledby="missing-heading">
                  <h3
                    id="missing-heading"
                    className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400"
                  >
                    {t.missing}
                  </h3>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {view.openGaps.map((gap) => (
                      <span
                        key={gap.gap_id}
                        className="max-w-full break-words rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] leading-tight text-gray-600"
                      >
                        {slotLabel(gap.slot, panelLang)}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {issueDraft && (
                <section className="mt-4 border-t border-gray-100 pt-3" aria-label={t.issueDraft}>
                  {!showIssueDraft ? (
                    <button
                      type="button"
                      onClick={() => void prepareIssueDraft()}
                      className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700 transition-colors hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-400"
                    >
                      {t.prepareIssue}
                    </button>
                  ) : (
                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 id="issue-draft-heading" className="text-sm font-semibold text-slate-900">
                            {t.issueDraft}
                          </h3>
                          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                            {t.issueDraftDetail}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => setShowIssueDraft(false)}
                          className="shrink-0 text-xs font-medium text-slate-500 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-400"
                        >
                          {t.closeIssueDraft}
                        </button>
                      </div>
                      <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-3 font-sans text-xs leading-relaxed text-slate-700">
                        {issueDraft.markdown}
                      </pre>
                      <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        <select
                          value={issueExportFormat}
                          onChange={(event) => setIssueExportFormat(event.target.value as IssueExportFormat)}
                          className="rounded-md border border-slate-300 bg-white px-2 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-slate-500"
                          aria-label={exportCopy.export}
                        >
                          <option value="markdown">Markdown</option>
                          <option value="json">JSON</option>
                          <option value="jira_csv">Jira CSV</option>
                          <option value="azure_csv">Azure Boards CSV</option>
                        </select>
                        {(issueExportFormat === 'jira_csv' || issueExportFormat === 'azure_csv') && (
                          <input
                            value={issueExportFormat === 'jira_csv'
                              ? issueExportSettings.jira_work_type
                              : issueExportSettings.azure_work_item_type}
                            onChange={(event) => setIssueExportSettingsState((current) => ({
                              ...current,
                              [issueExportFormat === 'jira_csv' ? 'jira_work_type' : 'azure_work_item_type']:
                                event.target.value.slice(0, 80),
                            }))}
                            className="rounded-md border border-slate-300 bg-white px-2 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-slate-500"
                            aria-label={exportCopy.type}
                          />
                        )}
                      </div>
                      <label className="mt-2 flex items-center gap-2 text-[11px] text-slate-600">
                        <input
                          type="checkbox"
                          checked={includeConnectedExcerpts}
                          onChange={(event) => setIncludeConnectedExcerpts(event.target.checked)}
                        />
                        {exportCopy.include}
                      </label>
                      <button
                        type="button"
                        onClick={() => void exportPreparedIssue()}
                        className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-500"
                      >
                        {exportCopy.export}
                      </button>
                      <button
                        type="button"
                        onClick={() => void copyIssueDraft()}
                        className="mt-3 w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
                        aria-live="polite"
                      >
                        {issueCopyStatus === 'copied'
                          ? t.copiedIssueDraft
                          : issueCopyStatus === 'failed'
                            ? t.copyIssueDraftFailed
                            : t.copyIssueDraft}
                      </button>
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      )}
    </aside>
  );
}
