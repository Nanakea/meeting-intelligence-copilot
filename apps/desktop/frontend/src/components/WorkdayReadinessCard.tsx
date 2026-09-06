'use client';

import { useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  Radio,
} from 'lucide-react';

import {
  getPilotPreflight,
  runSpokenPipelinePreflight,
  type PilotPreflight,
} from '@/services/intelligencePilot';
import {
  evaluateWorkdayReadiness,
  type WorkdayReadinessCheckId,
  type WorkdayReadinessCheckState,
} from '@/services/workdayReadiness';

interface WorkdayReadinessCardProps {
  microphoneDevice: string | null;
  systemAudioDevice: string | null;
}

type PilotLanguage = 'ja' | 'en' | 'ko';
type ActivityState = 'idle' | 'running' | 'passed' | 'failed';

const COPY = {
  en: {
    title: 'First workday readiness',
    intro: 'Run this before your first real meeting. It checks the local recording and question-copilot path without needing a company account.',
    language: 'Test language',
    check: 'Check local setup',
    checking: 'Checking...',
    spoken: 'Test transcript to panel',
    spokenRunning: 'Testing...',
    spokenPassed: 'The local spoken test reached the question panel.',
    spokenFailed: 'The spoken test did not complete. Recording is still available; check the local model and copilot, then retry.',
    checkFailed: 'The local readiness check could not complete. Restart Meeting Intelligence Copilot and retry before a real meeting.',
    ready: 'Ready for a local meeting',
    copilotReady: 'Recording and live ASK NOW are ready.',
    recordingOnly: 'Local recording is ready. The copilot needs attention, but it will not block recording.',
    action: 'Action required before a real meeting',
    actionDetail: 'Fix the marked local recording items, then run the check again.',
    companyTitle: 'Complete with company IT later',
    companyDetail: 'Confirm recording and retention policy with IT before company use. Request only selected-source, read-only access to NetSuite, Notion, or OneDrive/SharePoint. Connectors are optional; meeting-only ASK NOW remains available.',
    statuses: { pass: 'Ready', warning: 'Warning', action_required: 'Action required' },
    labels: {
      language: 'Japanese, English, or Korean selected',
      local_stt: 'Local transcription model',
      microphone: 'Microphone capture',
      system_audio: 'Meeting-output capture',
      backend: 'Local question copilot',
      storage: 'Available local storage',
      retention: 'Local retention policy',
    },
  },
  ja: {
    title: '初日の準備確認',
    intro: '実際の会議の前に実行してください。会社アカウントを使わず、ローカル録音と質問コパイロットの経路を確認します。',
    language: '確認する言語',
    check: 'ローカル環境を確認',
    checking: '確認中...',
    spoken: '文字起こしからパネルまで確認',
    spokenRunning: 'テスト中...',
    spokenPassed: 'ローカル音声テストが質問パネルまで到達しました。',
    spokenFailed: '音声テストが完了しませんでした。録音は利用できます。ローカルモデルとコパイロットを確認して再試行してください。',
    checkFailed: '準備確認を完了できませんでした。Meeting Intelligence Copilotを再起動し、実際の会議の前に再試行してください。',
    ready: 'ローカル会議の準備完了',
    copilotReady: '録音とライブ ASK NOW を利用できます。',
    recordingOnly: 'ローカル録音は利用できます。コパイロットの確認が必要ですが、録音は停止しません。',
    action: '実際の会議の前に対応が必要です',
    actionDetail: '対応が必要なローカル録音項目を修正し、もう一度確認してください。',
    companyTitle: '入社後に社内ITと完了する項目',
    companyDetail: '会社で使用する前に録音と保存期間の方針を社内ITに確認してください。NetSuite、Notion、OneDrive/SharePointは選択したソースへの読み取り専用アクセスのみ申請します。接続なしでも会議のASK NOWは利用できます。',
    statuses: { pass: '準備完了', warning: '要確認', action_required: '対応が必要' },
    labels: {
      language: '日本語・英語・韓国語の選択',
      local_stt: 'ローカル文字起こしモデル',
      microphone: 'マイク入力',
      system_audio: '会議出力の取り込み',
      backend: 'ローカル質問コパイロット',
      storage: 'ローカル空き容量',
      retention: 'ローカル保存期間',
    },
  },
  ko: {
    title: '첫 업무일 준비 상태',
    intro: '첫 실제 회의 전에 실행하십시오. 회사 계정 없이 로컬 녹음과 질문 코파일럿 경로를 확인합니다.',
    language: '테스트 언어',
    check: '로컬 설정 확인',
    checking: '확인 중...',
    spoken: '전사부터 패널까지 테스트',
    spokenRunning: '테스트 중...',
    spokenPassed: '로컬 음성 테스트가 질문 패널까지 도달했습니다.',
    spokenFailed: '음성 테스트가 완료되지 않았습니다. 녹음은 계속 사용할 수 있습니다. 로컬 모델과 코파일럿을 확인하십시오.',
    checkFailed: '준비 상태 확인을 완료하지 못했습니다. 실제 회의 전에 Meeting Intelligence Copilot을 다시 시작하고 재시도하십시오.',
    ready: '로컬 회의 준비 완료',
    copilotReady: '녹음과 실시간 지금 질문을 사용할 수 있습니다.',
    recordingOnly: '로컬 녹음은 준비되었습니다. 코파일럿은 점검이 필요하지만 녹음을 중단하지 않습니다.',
    action: '실제 회의 전에 조치 필요',
    actionDetail: '표시된 로컬 녹음 항목을 수정한 후 다시 확인하십시오.',
    companyTitle: '입사 후 회사 IT와 완료할 항목',
    companyDetail: '녹음 정책과 조직 서명 빌드를 확인한 후 승인된 읽기 전용 연결을 설정하십시오. 연결 없이도 회의 질문 기능은 사용할 수 있습니다.',
    statuses: { pass: '준비 완료', warning: '확인 필요', action_required: '조치 필요' },
    labels: {
      language: '한국어, 일본어 또는 영어 선택',
      local_stt: '로컬 전사 모델',
      microphone: '마이크 입력',
      system_audio: '회의 출력 캡처',
      backend: '로컬 질문 코파일럿',
      storage: '로컬 여유 공간',
      retention: '로컬 보존 정책',
    },
  },
} as const;

function StatusIcon({ state }: { state: WorkdayReadinessCheckState }) {
  if (state === 'pass') return <CheckCircle2 className="h-4 w-4 text-emerald-700" />;
  if (state === 'warning') return <AlertTriangle className="h-4 w-4 text-amber-700" />;
  return <CircleAlert className="h-4 w-4 text-rose-700" />;
}

export function WorkdayReadinessCard({
  microphoneDevice,
  systemAudioDevice,
}: WorkdayReadinessCardProps) {
  const [language, setLanguage] = useState<PilotLanguage>('en');
  const [preflight, setPreflight] = useState<PilotPreflight | null>(null);
  const [checkState, setCheckState] = useState<ActivityState>('idle');
  const [spokenState, setSpokenState] = useState<ActivityState>('idle');
  const requestGeneration = useRef(0);
  useEffect(() => {
    requestGeneration.current += 1;
    setPreflight(null);
    setCheckState('idle');
    setSpokenState('idle');
    return () => { requestGeneration.current += 1; };
  }, [language, microphoneDevice, systemAudioDevice]);
  const t = COPY[language];
  const summary = preflight ? evaluateWorkdayReadiness(preflight) : null;

  const runCheck = async () => {
    const generation = ++requestGeneration.current;
    setPreflight(null);
    setCheckState('running');
    setSpokenState('idle');
    try {
      const result = await getPilotPreflight(
        language,
        microphoneDevice,
        systemAudioDevice,
      );
      if (generation !== requestGeneration.current) return;
      setPreflight(result);
      setCheckState('passed');
    } catch {
      if (generation !== requestGeneration.current) return;
      setPreflight(null);
      setCheckState('failed');
    }
  };

  const runSpokenTest = async () => {
    const generation = requestGeneration.current;
    setSpokenState('running');
    try {
      await runSpokenPipelinePreflight(language);
      if (generation !== requestGeneration.current) return;
      setSpokenState('passed');
    } catch {
      if (generation !== requestGeneration.current) return;
      setSpokenState('failed');
    }
  };

  const summaryTitle = summary?.localRecordingReady ? t.ready : t.action;
  const summaryDetail = summary?.localRecordingReady
    ? summary.liveCopilotReady ? t.copilotReady : t.recordingOnly
    : t.actionDetail;

  return (
    <section className="overflow-hidden rounded-2xl border border-sky-200 bg-gradient-to-br from-sky-50 via-white to-emerald-50 shadow-sm" aria-labelledby="workday-readiness-title">
      <div className="border-b border-sky-100 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-sky-900 p-2 text-white"><Radio className="h-5 w-5" /></div>
          <div>
            <h4 id="workday-readiness-title" className="font-semibold text-slate-950">{t.title}</h4>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-600">{t.intro}</p>
          </div>
        </div>
      </div>

      <div className="space-y-4 p-5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-44 text-sm font-medium text-slate-800">
            <span className="mb-1 block text-xs uppercase tracking-wide text-slate-500">{t.language}</span>
            <select
              value={language}
              onChange={(event) => {
                requestGeneration.current += 1;
                setLanguage(event.target.value as PilotLanguage);
                setPreflight(null);
                setCheckState('idle');
                setSpokenState('idle');
              }}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-700 focus-visible:ring-offset-2"
            >
              <option value="en">English</option>
              <option value="ja">日本語</option>
              <option value="ko">한국어</option>
            </select>
          </label>
          <button
            type="button"
            onClick={() => void runCheck()}
            disabled={checkState === 'running' || spokenState === 'running'}
            className="inline-flex items-center gap-2 rounded-lg bg-sky-900 px-4 py-2 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-700 focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60"
          >
            {checkState === 'running' && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {checkState === 'running' ? t.checking : t.check}
          </button>
          <button
            type="button"
            onClick={() => void runSpokenTest()}
            disabled={!summary?.liveCopilotReady || spokenState === 'running' || checkState === 'running'}
            className="inline-flex items-center gap-2 rounded-lg border border-sky-800 bg-white px-4 py-2 text-sm font-semibold text-sky-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:border-slate-300 disabled:text-slate-400"
          >
            {spokenState === 'running' && <LoaderCircle className="h-4 w-4 animate-spin" />}
            {spokenState === 'running' ? t.spokenRunning : t.spoken}
          </button>
        </div>

        <div aria-live="polite" aria-atomic="true">
          {checkState === 'failed' && <p className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{t.checkFailed}</p>}
          {summary && (
            <div className="space-y-3">
              <div className={`rounded-lg border p-3 ${summary.localRecordingReady ? 'border-emerald-200 bg-emerald-50 text-emerald-950' : 'border-rose-200 bg-rose-50 text-rose-950'}`}>
                <p className="font-semibold">{summaryTitle}</p>
                <p className="mt-1 text-sm leading-relaxed">{summaryDetail}</p>
              </div>
              <ul className="grid gap-2 sm:grid-cols-2">
                {summary.checks.map((check) => (
                  <li key={check.id} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
                    <StatusIcon state={check.state} />
                    <span className="min-w-0 flex-1 text-slate-700">{t.labels[check.id as WorkdayReadinessCheckId]}</span>
                    <span className="text-xs font-semibold text-slate-500">{t.statuses[check.state]}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {spokenState === 'passed' && <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">{t.spokenPassed}</p>}
          {spokenState === 'failed' && <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{t.spokenFailed}</p>}
        </div>

        <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-950 px-4 py-3 text-slate-100">
          <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-sky-300" />
          <div>
            <p className="text-sm font-semibold">{t.companyTitle}</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-300">{t.companyDetail}</p>
          </div>
        </div>
      </div>
    </section>
  );
}
