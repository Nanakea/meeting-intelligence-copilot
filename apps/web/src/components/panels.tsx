// Slice 1 presentational panels — pure renderers of a MeetingState snapshot
// (docs/ARCHITECTURE.md §2). The frontend renders backend-owned state only; it
// never computes gap resolution or suggestion validity. Empty states are kept for
// meetings with no active pain point or suggestion. Facts render their `kind`
// (evidence vs inference) distinctly, per DOMAIN_MODEL §3 / product guarantee G2.

import type {
  InformationGap,
  MeetingFact,
  MeetingState,
  QuestionSuggestion,
} from "../types/contracts";

// Presentation-only slot labels per language (display never shows snake_case).
// Backend state stays generic; this is a pure UI mapping across all categories.
const SLOT_LABELS: Record<string, { ja: string; en: string }> = {
  source_of_truth: { ja: "在庫の基準", en: "Source of truth" },
  business_impact: { ja: "業務影響", en: "Business impact" },
  owner: { ja: "担当者", en: "Owner" },
  affected_scope: { ja: "影響範囲", en: "Affected scope" },
  frequency: { ja: "発生頻度", en: "Frequency" },
  correction_process: { ja: "修正方法", en: "Correction process" },
  volume: { ja: "処理量", en: "Volume" },
  time_cost: { ja: "所要時間", en: "Time cost" },
  reason_manual: { ja: "手作業の理由", en: "Reason it is manual" },
  success_condition: { ja: "解消条件", en: "Success condition" },
  failure_point: { ja: "失敗箇所", en: "Failure point" },
  retry_behavior: { ja: "再試行の挙動", en: "Retry behavior" },
  error_signal: { ja: "エラーの有無", en: "Error signal" },
  source_system: { ja: "連携元システム", en: "Source system" },
  target_system: { ja: "連携先システム", en: "Target system" },
  expected_behavior: { ja: "望ましい状態", en: "Expected behavior" },
  acceptance_criteria: { ja: "受け入れ基準", en: "Acceptance criteria" },
  current_problem: { ja: "現状の課題", en: "Current problem" },
  priority_level: { ja: "優先度", en: "Priority" },
  bottleneck: { ja: "ボトルネック", en: "Bottleneck" },
  current_lead_time: { ja: "現状の所要時間", en: "Current lead time" },
  normal_lead_time: { ja: "本来の所要時間", en: "Normal lead time" },
  process_step: { ja: "工程", en: "Process step" },
  decision_process: { ja: "意思決定プロセス", en: "Decision process" },
  stakeholders: { ja: "関係者", en: "Stakeholders" },
  escalation_path: { ja: "エスカレーション先", en: "Escalation path" },
  current_status: { ja: "現状", en: "Current status" },
  quality_attribute: { ja: "非機能特性", en: "Quality attribute" },
  measurable_target: { ja: "数値目標", en: "Measurable target" },
  scope_load: { ja: "範囲・負荷", en: "Scope and load" },
  measurement_method: { ja: "測定方法", en: "Measurement method" },
  constraint: { ja: "制約", en: "Constraint" },
  rationale_source: { ja: "根拠", en: "Rationale or source" },
  affected_systems: { ja: "影響システム", en: "Affected systems" },
  decision_authority: { ja: "意思決定権限", en: "Decision authority" },
  alternatives: { ja: "代替案", en: "Alternatives" },
  exception_path: { ja: "例外手続き", en: "Exception path" },
  affected_asset_data: { ja: "対象資産・データ", en: "Affected asset or data" },
  threat_control_gap: { ja: "脅威・統制不足", en: "Threat or control gap" },
  compliance_source: { ja: "規程・法令", en: "Compliance source" },
  target_date: { ja: "目標日", en: "Target date" },
  provider: { ja: "依存先", en: "Provider" },
  consumer: { ja: "利用側", en: "Consumer" },
  deliverable_interface: { ja: "成果物・IF", en: "Deliverable or interface" },
  needed_by: { ja: "必要期限", en: "Needed by" },
  fallback: { ja: "代替策", en: "Fallback" },
};

const KO_SLOT_LABELS: Record<string, string> = {
  source_of_truth: "기준 정보",
  business_impact: "업무 영향",
  owner: "담당자",
  affected_scope: "영향 범위",
  frequency: "발생 빈도",
  correction_process: "수정 절차",
  volume: "처리량",
  time_cost: "소요 시간",
  reason_manual: "수작업 이유",
  success_condition: "완료 조건",
  failure_point: "실패 지점",
  retry_behavior: "재시도 동작",
  error_signal: "오류 신호",
  source_system: "원본 시스템",
  target_system: "대상 시스템",
  expected_behavior: "기대 동작",
  acceptance_criteria: "인수 기준",
  current_problem: "현재 문제",
  priority_level: "우선순위",
  bottleneck: "병목",
  current_lead_time: "현재 리드 타임",
  normal_lead_time: "정상 리드 타임",
  process_step: "프로세스 단계",
  decision_process: "의사결정 절차",
  stakeholders: "이해관계자",
  escalation_path: "에스컬레이션 경로",
  current_status: "현재 상태",
  quality_attribute: "품질 특성",
  measurable_target: "측정 목표",
  scope_load: "범위 및 부하",
  measurement_method: "측정 방법",
  constraint: "제약",
  rationale_source: "근거 또는 출처",
  affected_systems: "영향 시스템",
  decision_authority: "의사결정 권한",
  alternatives: "대안",
  exception_path: "예외 절차",
  affected_asset_data: "영향 자산 또는 데이터",
  threat_control_gap: "위협 또는 통제 공백",
  compliance_source: "규정 또는 정책",
  target_date: "목표 날짜",
  provider: "제공자",
  consumer: "소비자",
  deliverable_interface: "산출물 또는 인터페이스",
  needed_by: "필요 기한",
  fallback: "대안",
};

type UiLang = "ja" | "en" | "ko";

const UI_COPY = {
  ja: {
    transcript: "Transcript", waiting: "Waiting for transcript…",
    pain: "Active pain point", noPain: "No active pain point",
    known: "Known", nothingKnown: "Nothing established yet",
    missing: "Missing", noGaps: "No open information gaps",
    askNow: "Ask now", noQuestion: "No question yet",
    followUp: "Follow up", noFollowUp: "No follow-up questions",
    inferred: "inferred", stated: "stated", asked: "Asked",
    notUseful: "Not useful", alreadyKnown: "Already known",
  },
  en: {
    transcript: "Transcript", waiting: "Waiting for transcript…",
    pain: "Active pain point", noPain: "No active pain point",
    known: "Known", nothingKnown: "Nothing established yet",
    missing: "Missing", noGaps: "No open information gaps",
    askNow: "Ask now", noQuestion: "No question yet",
    followUp: "Follow up", noFollowUp: "No follow-up questions",
    inferred: "inferred", stated: "stated", asked: "Asked",
    notUseful: "Not useful", alreadyKnown: "Already known",
  },
  ko: {
    transcript: "실시간 기록", waiting: "발언을 기다리는 중입니다…",
    pain: "현재 문제", noPain: "현재 감지된 문제가 없습니다",
    known: "확인된 정보", nothingKnown: "아직 확인된 정보가 없습니다",
    missing: "부족한 정보", noGaps: "열린 정보 공백이 없습니다",
    askNow: "지금 질문", noQuestion: "아직 제안할 질문이 없습니다",
    followUp: "후속 질문", noFollowUp: "후속 질문이 없습니다",
    inferred: "추론", stated: "발언 근거", asked: "질문함",
    notUseful: "유용하지 않음", alreadyKnown: "이미 알고 있음",
  },
} as const;

function meetingLang(state: MeetingState): UiLang {
  return state.transcript[0]?.lang ?? "en";
}

function slotLabel(slot: string, lang: UiLang): string {
  if (lang === "ko") return KO_SLOT_LABELS[slot] ?? SLOT_LABELS[slot]?.en ?? slot;
  return SLOT_LABELS[slot]?.[lang] ?? SLOT_LABELS[slot]?.en ?? slot;
}

function Empty({ children }: { children: string }) {
  return <p className="empty">{children}</p>;
}

/** Left column: the live transcript (speaker + text only, never internal IDs). */
export function TranscriptPanel({ state }: { state: MeetingState }) {
  const copy = UI_COPY[meetingLang(state)];
  return (
    <section className="panel transcript" aria-label={copy.transcript}>
      <h2>{copy.transcript}</h2>
      {state.transcript.length === 0 ? (
        <Empty>{copy.waiting}</Empty>
      ) : (
        <ol className="transcript-list">
          {state.transcript.map((ev) => (
            <li key={ev.event_id} className="line">
              <span className="speaker">{ev.speaker.display_name ?? ev.speaker.id}</span>
              <span className="utterance">{ev.text}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function ActivePainPoint({ state }: { state: MeetingState }) {
  const pain = state.pain_points[0] ?? null;
  const copy = UI_COPY[meetingLang(state)];
  return (
    <section className="panel pain" aria-label={copy.pain}>
      <h2>{copy.pain}</h2>
      {pain === null ? (
        <Empty>{copy.noPain}</Empty>
      ) : (
        <p className="pain-title">{pain.title}</p>
      )}
    </section>
  );
}

export function KnownInfo({ state }: { state: MeetingState }) {
  const facts = state.facts.filter((f: MeetingFact) => f.status === "active");
  const copy = UI_COPY[meetingLang(state)];
  return (
    <section className="panel known" aria-label={copy.known}>
      <h2>{copy.known}</h2>
      {facts.length === 0 ? (
        <Empty>{copy.nothingKnown}</Empty>
      ) : (
        <ul>
          {facts.map((f) => (
            <li key={f.fact_id}>
              <span className="slot">{slotLabel(f.slot, meetingLang(state))}</span>
              <span className="value">{f.value}</span>
              <span className={`kind kind-${f.kind}`}>
                {f.kind === "inference" ? copy.inferred : copy.stated}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function MissingInfo({ state }: { state: MeetingState }) {
  const open = state.gaps.filter((g: InformationGap) => g.status === "open");
  const copy = UI_COPY[meetingLang(state)];
  return (
    <section className="panel missing" aria-label={copy.missing}>
      <h2>{copy.missing}</h2>
      {open.length === 0 ? (
        <Empty>{copy.noGaps}</Empty>
      ) : (
        <ul className="chips">
          {open.map((g) => (
            <li key={g.gap_id} className="chip">
              {slotLabel(g.slot, meetingLang(state))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function activeByRole(state: MeetingState, role: QuestionSuggestion["role"]) {
  return state.suggestions.filter((s) => s.status === "active" && s.role === role);
}

// Feedback controls. Slice 1: rendered for the live-meeting flow but interaction
// wiring is DEFERRED (no backend inbound-message contract exists yet). See docs.
function FeedbackButtons({ lang }: { lang: UiLang }) {
  const copy = UI_COPY[lang];
  return (
    <div className="feedback" aria-label="Question feedback">
      <button type="button" disabled>{copy.asked}</button>
      <button type="button" disabled>{copy.notUseful}</button>
      <button type="button" disabled>{copy.alreadyKnown}</button>
    </div>
  );
}

export function AskNowCard({ state }: { state: MeetingState }) {
  const askNow = activeByRole(state, "ask_now")[0] ?? null;
  const lang = meetingLang(state);
  const copy = UI_COPY[lang];
  return (
    <section className="panel ask-now" aria-label={copy.askNow}>
      <h2>{copy.askNow}</h2>
      {askNow === null ? (
        <Empty>{copy.noQuestion}</Empty>
      ) : (
        <article className="ask-now-card">
          <p className="question">{askNow.text}</p>
          <p className="reason">{askNow.reason}</p>
          <FeedbackButtons lang={lang} />
        </article>
      )}
    </section>
  );
}

export function FollowUpList({ state }: { state: MeetingState }) {
  const followUps = activeByRole(state, "follow_up").slice(0, 2);
  const copy = UI_COPY[meetingLang(state)];
  return (
    <section className="panel follow-up" aria-label={copy.followUp}>
      <h2>{copy.followUp}</h2>
      {followUps.length === 0 ? (
        <Empty>{copy.noFollowUp}</Empty>
      ) : (
        <ul>
          {followUps.map((s) => (
            <li key={s.suggestion_id}>{s.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
