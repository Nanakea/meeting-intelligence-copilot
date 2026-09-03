import type {
  InformationGap,
  MeetingFact,
  LiveIntelligenceSnapshot,
  PainPoint,
  QuestionSuggestion,
} from '../../services/intelligenceContracts';
import type { IntelligenceSidecarStatus } from '../../services/intelligenceLifecycle';

export type PanelLanguage = 'ja' | 'en' | 'ko';

const SLOT_LABELS: Record<string, Record<'ja' | 'en', string>> = {
  source_of_truth: { ja: '在庫の基準', en: 'Source of truth' },
  business_impact: { ja: '業務影響', en: 'Business impact' },
  owner: { ja: '担当者', en: 'Owner' },
  affected_scope: { ja: '影響範囲', en: 'Affected scope' },
  frequency: { ja: '発生頻度', en: 'Frequency' },
  correction_process: { ja: '修正方法', en: 'Correction process' },
  volume: { ja: '処理量', en: 'Volume' },
  time_cost: { ja: '所要時間', en: 'Time cost' },
  reason_manual: { ja: '手作業の理由', en: 'Reason it is manual' },
  success_condition: { ja: '解消条件', en: 'Success condition' },
  failure_point: { ja: '失敗箇所', en: 'Failure point' },
  retry_behavior: { ja: '再試行の挙動', en: 'Retry behavior' },
  error_signal: { ja: 'エラーの有無', en: 'Error signal' },
  source_system: { ja: '連携元システム', en: 'Source system' },
  target_system: { ja: '連携先システム', en: 'Target system' },
  expected_behavior: { ja: '望ましい状態', en: 'Expected behavior' },
  acceptance_criteria: { ja: '受け入れ基準', en: 'Acceptance criteria' },
  current_problem: { ja: '現状の課題', en: 'Current problem' },
  priority_level: { ja: '優先度', en: 'Priority' },
  bottleneck: { ja: 'ボトルネック', en: 'Bottleneck' },
  current_lead_time: { ja: '現状の所要時間', en: 'Current lead time' },
  normal_lead_time: { ja: '本来の所要時間', en: 'Normal lead time' },
  process_step: { ja: '工程', en: 'Process step' },
  decision_process: { ja: '意思決定プロセス', en: 'Decision process' },
  stakeholders: { ja: '関係者', en: 'Stakeholders' },
  escalation_path: { ja: 'エスカレーション先', en: 'Escalation path' },
  current_status: { ja: '現状', en: 'Current status' },
  quality_attribute: { ja: '非機能特性', en: 'Quality attribute' },
  measurable_target: { ja: '数値目標', en: 'Measurable target' },
  scope_load: { ja: '範囲・負荷', en: 'Scope and load' },
  measurement_method: { ja: '測定方法', en: 'Measurement method' },
  constraint: { ja: '制約', en: 'Constraint' },
  rationale_source: { ja: '根拠', en: 'Rationale or source' },
  affected_systems: { ja: '影響システム', en: 'Affected systems' },
  decision_authority: { ja: '意思決定権限', en: 'Decision authority' },
  alternatives: { ja: '代替案', en: 'Alternatives' },
  exception_path: { ja: '例外手続き', en: 'Exception path' },
  affected_asset_data: { ja: '対象資産・データ', en: 'Affected asset or data' },
  threat_control_gap: { ja: '脅威・統制不足', en: 'Threat or control gap' },
  compliance_source: { ja: '規程・法令', en: 'Compliance source' },
  target_date: { ja: '目標日', en: 'Target date' },
  provider: { ja: '依存先', en: 'Provider' },
  consumer: { ja: '利用側', en: 'Consumer' },
  deliverable_interface: { ja: '成果物・IF', en: 'Deliverable or interface' },
  needed_by: { ja: '必要期限', en: 'Needed by' },
  fallback: { ja: '代替策', en: 'Fallback' },
};

const KO_SLOT_LABELS: Record<string, string> = {
  source_of_truth: '기준 정보',
  business_impact: '업무 영향',
  owner: '담당자',
  affected_scope: '영향 범위',
  frequency: '발생 빈도',
  correction_process: '수정 절차',
  volume: '처리량',
  time_cost: '소요 시간',
  reason_manual: '수작업 이유',
  success_condition: '완료 조건',
  failure_point: '실패 지점',
  retry_behavior: '재시도 동작',
  error_signal: '오류 신호',
  source_system: '원본 시스템',
  target_system: '대상 시스템',
  expected_behavior: '기대 동작',
  acceptance_criteria: '인수 기준',
  current_problem: '현재 문제',
  priority_level: '우선순위',
  bottleneck: '병목',
  current_lead_time: '현재 리드 타임',
  normal_lead_time: '정상 리드 타임',
  process_step: '프로세스 단계',
  decision_process: '의사결정 절차',
  stakeholders: '이해관계자',
  escalation_path: '에스컬레이션 경로',
  current_status: '현재 상태',
  quality_attribute: '품질 특성',
  measurable_target: '측정 목표',
  scope_load: '범위 및 부하',
  measurement_method: '측정 방법',
  constraint: '제약',
  rationale_source: '근거 또는 출처',
  affected_systems: '영향 시스템',
  decision_authority: '의사결정 권한',
  alternatives: '대안',
  exception_path: '예외 절차',
  affected_asset_data: '영향 자산 또는 데이터',
  threat_control_gap: '위협 또는 통제 공백',
  compliance_source: '규정 또는 정책',
  target_date: '목표 날짜',
  provider: '제공자',
  consumer: '소비자',
  deliverable_interface: '산출물 또는 인터페이스',
  needed_by: '필요 기한',
  fallback: '대안',
};

const BASE_PANEL_COPY = {
  ja: {
    copilot: '質問コパイロット',
    privatePrompt: '会話から、次に確認すべきことを整理します',
    localOnly: 'ローカルのみ',
    localOnlyDetail: '会議インテリジェンスはこの端末内のループバック接続で動作します',
    detectedTopic: '検出した課題',
    askNow: '今すぐ聞く',
    why: '理由',
    followUp: '必要なら次に',
    known: 'わかっていること',
    missing: 'まだ確認したいこと',
    connecting: 'ローカルのコパイロットを起動しています…',
    connectingDetail: '接続でき次第、会話から質問候補を整理します。',
    listening: '具体的な課題を聞き取っています…',
    listeningDetail: '重要な情報が不足しているときだけ、質問を1つ表示します。',
    unavailable: 'コパイロットに接続できません',
    unavailableDetail: '録音は継続します。ローカルのコパイロットを起動または確認してください。',
    restartAttempt: 'バックエンドを再起動しています',
    automaticRetriesStopped: '自動再試行は上限に達しました。手動で再試行できます。',
    retryBackend: '今すぐ再試行',
    missingExecutable: 'バックエンド実行ファイルがありません。アプリを再インストールするか、ローカル設定を確認してください。',
    incompatibleBackend: 'ローカルのバックエンドのバージョンに互換性がありません。アプリ一式を同じバージョンに更新してください。',
    startupFailed: 'ローカルのバックエンドを起動できませんでした。再試行しても解決しない場合はログを確認してください。',
    backendStopped: 'ローカルのバックエンドが停止したか、ヘルスチェックに失敗しました。',
    recovering: 'ローカルで再接続しています…',
    recoveringDetail: '録音は継続します。文脈を安全に復元するまで質問は表示しません。',
    contextReset: '会議の文脈を再構築しています',
    contextResetDetail: '履歴または新しい発言から復元できるまで、古い質問は表示しません。',
    recovered: '会議の文脈を復元しました',
    noOpenQuestion: '今すぐ確認することはありません',
    noOpenQuestionDetail: '回答済みの内容は再質問せず、新しい不足情報を待っています。',
    checkingSuggestion: '現在の質問候補を確認しています…',
    checkingSuggestionDetail: 'この候補がすでに非表示にされていないか、端末内の記録を確認しています。',
    paused: '録音は一時停止中です',
    pausedDetail: '再開するまで ASK NOW は更新されません。現在の内容は確認できます。',
    topics: '検出した課題',
    useful: '役に立つ',
    markedUseful: '役に立つと記録済み',
    dismiss: '今回は非表示',
    feedbackSaveFailed: 'フィードバックを保存できませんでした。もう一度お試しください。',
    copyQuestion: '質問をコピー',
    copiedQuestion: 'コピーしました',
    copyQuestionFailed: 'コピーできませんでした',
    dismissed: '質問を非表示にしました',
    dismissedDetail: '事実や不足情報は変更せず、この質問の現在の表示だけを抑制します。',
    inferred: '推測',
    stated: '発言あり',
    prepareIssue: '起票の下書きを作る',
    issueDraft: '起票の下書き',
    issueDraftDetail: '表示中の課題・確認済み事実・未確認事項だけをローカルで整形します。',
    copyIssueDraft: '下書きをコピー',
    copiedIssueDraft: 'コピーしました',
    copyIssueDraftFailed: 'コピーできませんでした',
    closeIssueDraft: '閉じる',
  },
  en: {
    copilot: 'Question copilot',
    privatePrompt: 'Tracks what to clarify next from the live conversation',
    localOnly: 'Local only',
    localOnlyDetail: 'Meeting intelligence stays on this device over a loopback connection',
    detectedTopic: 'Detected issue',
    askNow: 'ASK NOW',
    why: 'Why',
    followUp: 'If useful next',
    known: 'Known',
    missing: 'Still worth clarifying',
    connecting: 'Starting the local copilot…',
    connectingDetail: 'Questions will appear when the local connection is ready.',
    listening: 'Listening for a concrete issue…',
    listeningDetail: 'One question appears only when important information is missing.',
    unavailable: 'Copilot unavailable',
    unavailableDetail: 'Recording continues. Start or check the local copilot when convenient.',
    restartAttempt: 'Restarting the backend',
    automaticRetriesStopped: 'Automatic retries reached their limit. You can retry manually.',
    retryBackend: 'Retry now',
    missingExecutable: 'The backend executable is missing. Reinstall the app or check the local configuration.',
    incompatibleBackend: 'The local backend version is incompatible. Update the app and backend together.',
    startupFailed: 'The local backend could not start. Retry, then inspect local logs if it still fails.',
    backendStopped: 'The local backend stopped or failed its health checks.',
    recovering: 'Reconnecting locally…',
    recoveringDetail: 'Recording continues. ASK NOW stays hidden until context is safely restored.',
    contextReset: 'Rebuilding meeting context',
    contextResetDetail: 'Old questions stay hidden until history or new speech rebuilds the context.',
    recovered: 'Meeting context recovered',
    noOpenQuestion: 'Nothing to clarify right now',
    noOpenQuestionDetail: 'Answered information stays out of the queue while the copilot listens for a new gap.',
    checkingSuggestion: 'Checking the current suggestion…',
    checkingSuggestionDetail: 'The copilot is checking local feedback before showing this occurrence.',
    paused: 'Recording is paused',
    pausedDetail: 'ASK NOW will not update until recording resumes. Current context remains visible.',
    topics: 'Detected topics',
    useful: 'Useful',
    markedUseful: 'Marked useful',
    dismiss: 'Dismiss this',
    feedbackSaveFailed: 'Feedback could not be saved. Please try again.',
    copyQuestion: 'Copy question',
    copiedQuestion: 'Copied question',
    copyQuestionFailed: 'Could not copy',
    dismissed: 'Question dismissed',
    dismissedDetail: 'Facts and gaps are unchanged; only this occurrence is hidden.',
    inferred: 'inferred',
    stated: 'stated',
    prepareIssue: 'Prepare issue draft',
    issueDraft: 'Issue draft',
    issueDraftDetail: 'Formats only the focused issue, active facts, and open questions locally.',
    copyIssueDraft: 'Copy issue draft',
    copiedIssueDraft: 'Copied',
    copyIssueDraftFailed: 'Could not copy',
    closeIssueDraft: 'Close',
  },
} as const;

const KOREAN_PANEL_COPY = {
  copilot: '질문 코파일럿',
  privatePrompt: '실시간 대화에서 다음에 확인할 내용을 추적합니다',
  localOnly: '로컬 전용',
  localOnlyDetail: '회의 인텔리전스는 이 장치의 루프백 연결에서만 작동합니다',
  detectedTopic: '감지된 문제',
  askNow: '지금 질문',
  why: '이유',
  followUp: '다음 후속 질문',
  known: '확인된 정보',
  missing: '추가 확인 사항',
  connecting: '로컬 코파일럿을 시작하는 중입니다…',
  connectingDetail: '로컬 연결이 준비되면 질문이 표시됩니다.',
  listening: '구체적인 문제를 듣고 있습니다…',
  listeningDetail: '중요한 정보가 부족할 때만 질문 하나를 표시합니다.',
  unavailable: '코파일럿을 사용할 수 없습니다',
  unavailableDetail: '녹음은 계속됩니다. 편한 시간에 로컬 코파일럿을 확인하십시오.',
  restartAttempt: '백엔드를 다시 시작하는 중',
  automaticRetriesStopped: '자동 재시도 한도에 도달했습니다. 수동으로 다시 시도할 수 있습니다.',
  retryBackend: '다시 시도',
  missingExecutable: '백엔드 실행 파일이 없습니다. 앱을 다시 설치하거나 로컬 설정을 확인하십시오.',
  incompatibleBackend: '로컬 백엔드 버전이 호환되지 않습니다. 앱과 백엔드를 함께 업데이트하십시오.',
  startupFailed: '로컬 백엔드를 시작하지 못했습니다. 다시 시도한 후 로컬 로그를 확인하십시오.',
  backendStopped: '로컬 백엔드가 중지되었거나 상태 확인에 실패했습니다.',
  recovering: '로컬에서 다시 연결하는 중입니다…',
  recoveringDetail: '녹음은 계속됩니다. 문맥이 안전하게 복구될 때까지 질문을 숨깁니다.',
  contextReset: '회의 문맥을 다시 구성하는 중',
  contextResetDetail: '기록이나 새 발언으로 문맥이 복구될 때까지 이전 질문을 숨깁니다.',
  recovered: '회의 문맥이 복구되었습니다',
  noOpenQuestion: '지금 확인할 내용이 없습니다',
  noOpenQuestionDetail: '답변된 내용은 다시 묻지 않고 새로운 정보 공백을 기다립니다.',
  checkingSuggestion: '현재 질문 제안을 확인하는 중입니다…',
  checkingSuggestionDetail: '이 질문이 이미 숨김 처리되었는지 로컬 피드백을 확인합니다.',
  paused: '녹음이 일시 중지되었습니다',
  pausedDetail: '녹음을 재개할 때까지 지금 질문은 갱신되지 않습니다.',
  topics: '감지된 문제',
  useful: '유용함',
  markedUseful: '유용함으로 기록됨',
  dismiss: '이번 질문 숨기기',
  feedbackSaveFailed: '피드백을 저장하지 못했습니다. 다시 시도하십시오.',
  copyQuestion: '질문 복사',
  copiedQuestion: '질문을 복사했습니다',
  copyQuestionFailed: '복사하지 못했습니다',
  dismissed: '질문을 숨겼습니다',
  dismissedDetail: '사실과 정보 공백은 그대로이며 현재 질문만 숨깁니다.',
  inferred: '추론',
  stated: '발언 근거',
  prepareIssue: '이슈 초안 준비',
  issueDraft: '이슈 초안',
  issueDraftDetail: '선택한 문제, 확인된 사실 및 열린 질문만 로컬에서 정리합니다.',
  copyIssueDraft: '이슈 초안 복사',
  copiedIssueDraft: '복사했습니다',
  copyIssueDraftFailed: '복사하지 못했습니다',
  closeIssueDraft: '닫기',
} satisfies Record<keyof typeof BASE_PANEL_COPY.en, string>;

export const PANEL_COPY = {
  ...BASE_PANEL_COPY,
  ko: KOREAN_PANEL_COPY,
} as const;

export function sidecarUnavailableDetail(
  status: IntelligenceSidecarStatus | null,
  lang: PanelLanguage,
): string {
  const t = PANEL_COPY[lang];
  const reasonDetail =
    status?.reason === 'missing_executable'
      ? t.missingExecutable
      : status?.reason === 'incompatible_backend'
        ? t.incompatibleBackend
        : status?.reason === 'startup_failed'
          ? t.startupFailed
          : status?.reason === 'backend_stopped'
            ? t.backendStopped
            : t.unavailableDetail;

  if (status?.phase === 'unavailable') {
    return `${reasonDetail} ${t.automaticRetriesStopped}`;
  }
  if (status?.phase === 'restarting') {
    return `${reasonDetail} ${t.restartAttempt} ${status.retryCount}/${status.maxRetryCount}.`;
  }
  return reasonDetail;
}

export interface IntelligencePanelView {
  pains: PainPoint[];
  pain: PainPoint | null;
  askNow: QuestionSuggestion | null;
  askNowGap: InformationGap | null;
  followUps: Array<{ suggestion: QuestionSuggestion; topicTitle: string | null }>;
  facts: MeetingFact[];
  openGaps: InformationGap[];
}

export function slotLabel(slot: string, lang: PanelLanguage): string {
  if (lang === 'ko') return KO_SLOT_LABELS[slot] ?? '기타 정보';
  return SLOT_LABELS[slot]?.[lang] ?? (lang === 'ja' ? 'その他の情報' : 'Other detail');
}

export function buildIntelligencePanelView(
  state: LiveIntelligenceSnapshot,
  selectedPainId?: string | null,
): IntelligencePanelView {
  const activeSuggestions = state.suggestions.filter((suggestion) => suggestion.status === 'active');
  const askNow = activeSuggestions.find((suggestion) => suggestion.role === 'ask_now') ?? null;
  const askNowGap = askNow
    ? state.gaps.find((gap) => gap.gap_id === askNow.gap_id) ?? null
    : null;
  const defaultPainId =
    askNowGap?.pain_id ??
    state.pain_points.find((pain) => pain.status === 'open')?.pain_id ??
    state.pain_points[0]?.pain_id ??
    null;
  const selectedPainIsValid = state.pain_points.some(
    (pain) => pain.pain_id === selectedPainId,
  );
  // ASK NOW owns the visible context. Topic selection is available only when
  // there is no active question, preventing facts from one pain being shown
  // beside a question targeting another.
  const focusPainId =
    askNowGap?.pain_id ?? (selectedPainIsValid ? selectedPainId : defaultPainId);
  const pain = state.pain_points.find((candidate) => candidate.pain_id === focusPainId) ?? null;
  const titleByPainId = new Map(state.pain_points.map((candidate) => [candidate.pain_id, candidate.title]));

  return {
    pains: state.pain_points,
    pain,
    askNow,
    askNowGap,
    followUps: activeSuggestions
      .filter((suggestion) => suggestion.role === 'follow_up')
      .slice(0, 2)
      .map((suggestion) => {
        const gap = state.gaps.find((candidate) => candidate.gap_id === suggestion.gap_id);
        return {
          suggestion,
          topicTitle:
            gap && gap.pain_id !== focusPainId
              ? titleByPainId.get(gap.pain_id) ?? null
              : null,
        };
      }),
    facts: state.facts.filter(
      (fact) => fact.status === 'active' && fact.pain_id === focusPainId,
    ),
    openGaps: state.gaps.filter(
      (gap) => gap.status === 'open' && gap.pain_id === focusPainId,
    ),
  };
}
