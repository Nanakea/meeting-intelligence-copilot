'use client';

import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import {
  AlertTriangle,
  Check,
  HardDrive,
  LoaderCircle,
  Mic,
  Radio,
  TestTube2,
  Volume2,
  X,
} from 'lucide-react';

import { AudioLevelMeter } from '@/components/AudioLevelMeter';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  confirmRecordingConsent,
  runSpokenPipelinePreflight,
  type PilotPreflight,
} from '@/services/intelligencePilot';
import {
  selectPreflightAudioLevels,
  type PreflightAudioLevel,
} from '@/services/preflightAudioLevels';

interface RecordingPreflightDialogProps {
  open: boolean;
  preflight: PilotPreflight | null;
  loading: boolean;
  onCancel: () => void;
  onContinue: () => void | Promise<void>;
}

interface AudioLevelUpdate {
  levels: PreflightAudioLevel[];
}

type SpokenTestStatus = 'idle' | 'running' | 'passed' | 'failed';

function formatBytes(value: number | null): string {
  if (value === null) return 'Unavailable';
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB free`;
}

function CheckRow({
  ok,
  icon: Icon,
  label,
  detail,
}: {
  ok: boolean;
  icon: typeof Mic;
  label: string;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5">
      <div className={`mt-0.5 rounded-full p-1 ${ok ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
        {ok ? <Check className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
      </div>
      <Icon className="mt-1 h-4 w-4 text-slate-500" />
      <div className="min-w-0">
        <div className="text-sm font-medium text-slate-900">{label}</div>
        <div className="truncate text-xs text-slate-500">{detail}</div>
      </div>
    </div>
  );
}

export function RecordingPreflightDialog({
  open,
  preflight,
  loading,
  onCancel,
  onContinue,
}: RecordingPreflightDialogProps) {
  const backendReady = preflight?.backend.phase === 'ready';
  const isJapanese = preflight?.language === 'ja';
  const isKorean = preflight?.language === 'ko';
  const minimumDiskBytes = 2 * 1024 * 1024 * 1024;
  const enoughDisk =
    (preflight?.availableDiskBytes ?? 0) >= minimumDiskBytes &&
    (preflight?.pilotDataAvailableDiskBytes ?? 0) >= minimumDiskBytes;
  const [microphoneLevel, setMicrophoneLevel] =
    useState<PreflightAudioLevel | null>(null);
  const [systemAudioLevel, setSystemAudioLevel] =
    useState<PreflightAudioLevel | null>(null);
  const [spokenTestStatus, setSpokenTestStatus] =
    useState<SpokenTestStatus>('idle');
  const [spokenTestDuration, setSpokenTestDuration] = useState<number | null>(
    null,
  );
  const [startingRecording, setStartingRecording] = useState(false);
  const [participantsInformed, setParticipantsInformed] = useState(false);
  const [consentSaveFailed, setConsentSaveFailed] = useState(false);

  useEffect(() => {
    setSpokenTestStatus('idle');
    setSpokenTestDuration(null);
    setStartingRecording(false);
    setParticipantsInformed(false);
    setConsentSaveFailed(false);
  }, [open, preflight?.language]);

  useEffect(() => {
    if (
      !open ||
      loading ||
      (!preflight?.microphoneAvailable && !preflight?.systemAudioAvailable)
    ) {
      setMicrophoneLevel(null);
      setSystemAudioLevel(null);
      return;
    }

    let disposed = false;
    let unlisten: (() => void) | undefined;
    const start = async () => {
      try {
        setMicrophoneLevel(null);
        setSystemAudioLevel(null);
        const registeredUnlisten = await listen<AudioLevelUpdate>('audio-levels', (event) => {
          if (!disposed) {
            const selected = selectPreflightAudioLevels(
              event.payload.levels,
              preflight.microphoneDeviceName,
              preflight.systemAudioDeviceName,
            );
            if (selected.microphone) {
              setMicrophoneLevel(selected.microphone);
            }
            if (selected.systemAudio) {
              setSystemAudioLevel(selected.systemAudio);
            }
          }
        });
        if (disposed) {
          registeredUnlisten();
          return;
        }
        unlisten = registeredUnlisten;
        const deviceNames = [
          ...(preflight.microphoneAvailable
            ? [preflight.microphoneDeviceName]
            : []),
          ...(preflight.systemAudioAvailable
            ? [preflight.systemAudioDeviceName]
            : []),
        ];
        await invoke('start_audio_level_monitoring', {
          deviceNames: [...new Set(deviceNames)],
        });
        if (disposed) {
          await invoke('stop_audio_level_monitoring').catch(() => undefined);
        }
      } catch {
        setMicrophoneLevel(null);
        setSystemAudioLevel(null);
      }
    };
    void start();

    return () => {
      disposed = true;
      unlisten?.();
      void invoke('stop_audio_level_monitoring').catch(() => undefined);
    };
  }, [
    loading,
    open,
    preflight?.microphoneAvailable,
    preflight?.microphoneDeviceName,
    preflight?.systemAudioAvailable,
    preflight?.systemAudioDeviceName,
  ]);

  const runSpokenTest = async () => {
    const language = preflight?.language;
    if (language !== 'ja' && language !== 'en' && language !== 'ko') return;
    setSpokenTestStatus('running');
    setSpokenTestDuration(null);
    try {
      const result = await runSpokenPipelinePreflight(language);
      setSpokenTestDuration(result.durationMs);
      setSpokenTestStatus('passed');
    } catch {
      setSpokenTestStatus('failed');
    }
  };

  const continueAfterMonitoringStops = async () => {
    if (!participantsInformed) return;
    setStartingRecording(true);
    setConsentSaveFailed(false);
    try {
      await invoke('stop_audio_level_monitoring').catch(() => undefined);
      try {
        await confirmRecordingConsent();
      } catch {
        setConsentSaveFailed(true);
        return;
      }
      await onContinue();
    } finally {
      setStartingRecording(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) =>
        !next && spokenTestStatus !== 'running' && onCancel()
      }
    >
      <DialogContent className="max-w-xl bg-slate-50">
        <DialogHeader>
          <DialogTitle>
            {isJapanese ? '録音前チェック' : isKorean ? '녹음 전 확인' : 'Recording preflight'}
          </DialogTitle>
          <DialogDescription>
            {isJapanese
              ? '質問支援が利用できない場合も録音できます。会議前に音声メーターが動くことを確認してください。'
              : isKorean
                ? '질문 지원을 사용할 수 없어도 녹음은 계속할 수 있습니다. 회의 전에 오디오 미터가 움직이는지 확인하십시오.'
              : 'Recording can continue when intelligence is unavailable. Confirm the audio meters move before joining the meeting.'}
          </DialogDescription>
        </DialogHeader>
        {loading || !preflight ? (
          <div className="py-8 text-center text-sm text-slate-500">Checking local recording setup...</div>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            <CheckRow
              ok={preflight.languageSupported}
              icon={Radio}
              label="Meeting language"
              detail={
                preflight.language === 'ja'
                  ? 'Japanese'
                  : preflight.language === 'en'
                    ? 'English'
                    : preflight.language === 'ko'
                      ? 'Korean'
                      : 'Select Japanese, English, or Korean'
              }
            />
            <CheckRow
              ok={preflight.localSttReady}
              icon={Radio}
              label="Local STT model"
              detail={`${preflight.localSttModel}: ${
                preflight.whisperModelReadiness
                  ? preflight.whisperModelReadiness.integrity === 'verified' &&
                    preflight.whisperModelReadiness.supportedLanguage
                    ? 'verified for this language'
                    : `not ready (${preflight.whisperModelReadiness.integrity.replaceAll('_', ' ')})`
                  : preflight.localSttReady
                    ? 'ready for this language'
                    : 'setup or language change required'
              }`}
            />
            <CheckRow
              ok={preflight.availableMemoryBytes >= 4 * 1024 * 1024 * 1024}
              icon={HardDrive}
              label="Available memory"
              detail={`${formatBytes(preflight.availableMemoryBytes)} available; 4 GB minimum recommended`}
            />
            {isKorean && (
              <CheckRow
                ok={preflight.localSpeechVoiceReady}
                icon={Radio}
                label="Korean synthetic test voice"
                detail={
                  preflight.localSpeechVoiceReady
                    ? 'A local ko-KR voice is available'
                    : 'Install a Windows ko-KR speech voice to run the spoken pipeline test'
                }
              />
            )}
            <CheckRow
              ok={preflight.microphoneAvailable}
              icon={Mic}
              label="Microphone"
              detail={
                preflight.microphoneAvailable
                  ? preflight.microphoneConfigured
                    ? 'Selected input is available'
                    : 'System default input is available'
                  : 'No usable input device'
              }
            />
            <CheckRow
              ok={preflight.systemAudioAvailable}
              icon={Volume2}
              label="Meeting output capture"
              detail={
                preflight.systemAudioAvailable
                  ? preflight.systemAudioConfigured
                    ? 'Selected output is available; verify its live meter'
                    : 'System default output is available; verify its live meter'
                  : preflight.systemAudioConfigured
                    ? 'Selected output device is no longer available'
                    : 'No usable default output device'
              }
            />
            <CheckRow
              ok={backendReady}
              icon={Radio}
              label="ASK NOW backend"
              detail={backendReady ? `Ready${preflight.backend.backendVersion ? ` v${preflight.backend.backendVersion}` : ''}` : 'Unavailable; recording will still work'}
            />
            <CheckRow
              ok={enoughDisk}
              icon={HardDrive}
              label="Local storage"
              detail={`Recordings ${formatBytes(preflight.availableDiskBytes)}; pilot data ${formatBytes(preflight.pilotDataAvailableDiskBytes)}; retention ${preflight.retention.replaceAll('_', ' ')}`}
            />
            <div className="grid gap-2 sm:col-span-2 sm:grid-cols-2">
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
                <div className="mb-2 text-xs font-medium text-slate-700">
                  Live microphone level
                </div>
                <AudioLevelMeter
                  rmsLevel={microphoneLevel?.rms_level ?? 0}
                  peakLevel={microphoneLevel?.peak_level ?? 0}
                  isActive={microphoneLevel?.is_active ?? false}
                  deviceName={microphoneLevel?.device_name ?? preflight.microphoneDeviceName}
                  size="small"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Speak briefly and confirm the bar moves.
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-white px-3 py-2.5">
                <div className="mb-2 text-xs font-medium text-slate-700">
                  Live meeting-output level
                </div>
                <AudioLevelMeter
                  rmsLevel={systemAudioLevel?.rms_level ?? 0}
                  peakLevel={systemAudioLevel?.peak_level ?? 0}
                  isActive={systemAudioLevel?.is_active ?? false}
                  deviceName={
                    systemAudioLevel?.device_name ??
                    (preflight.systemAudioDeviceName || 'Not configured')
                  }
                  size="small"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Play meeting audio and confirm this bar moves.
                </p>
              </div>
            </div>
            <div
              className="rounded-lg border border-slate-200 bg-white px-3 py-3 sm:col-span-2"
              aria-live="polite"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
                    <TestTube2 className="h-4 w-4 text-slate-500" />
                    Spoken intelligence pipeline
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Generates a fixed local synthetic voice and runs it through the selected STT
                    model, backend, and panel WebSocket. The temporary test
                    session is deleted immediately.
                  </p>
                  {spokenTestStatus === 'passed' && (
                    <p className="mt-1 text-xs font-medium text-emerald-700">
                      Passed in {((spokenTestDuration ?? 0) / 1000).toFixed(1)}s.
                    </p>
                  )}
                  {spokenTestStatus === 'failed' && (
                    <p className="mt-1 text-xs font-medium text-amber-700">
                      Test unavailable. Check the local voice, STT model, and
                      backend; recording is still available.
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => void runSpokenTest()}
                  disabled={
                    spokenTestStatus === 'running' ||
                    !backendReady ||
                    !preflight.localSttReady ||
                    !preflight.languageSupported
                  }
                  className="inline-flex shrink-0 items-center gap-2 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {spokenTestStatus === 'running' ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <TestTube2 className="h-3.5 w-3.5" />
                  )}
                  {spokenTestStatus === 'running' ? 'Testing...' : 'Run test'}
                </button>
              </div>
            </div>
          </div>
        )}
        {preflight && (
          <div className="space-y-1 text-xs text-slate-500">
            <p className="break-all">Recordings: {preflight.recordingDirectory}</p>
            <p className="break-all">Pilot and recovery data: {preflight.localDataDirectory}</p>
          </div>
        )}
        <div className="rounded-lg border border-slate-300 bg-white px-3 py-3">
          <label className="flex cursor-pointer items-start gap-3 text-sm text-slate-800">
            <input
              type="checkbox"
              checked={participantsInformed}
              onChange={(event) => {
                setParticipantsInformed(event.target.checked);
                setConsentSaveFailed(false);
              }}
              aria-describedby="recording-consent-detail"
              className="mt-0.5 h-4 w-4 rounded border-slate-400 text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2"
            />
            <span>
              <span className="font-semibold">
                {isJapanese
                  ? '参加者へ録音について案内しました。'
                  : isKorean
                    ? '참가자에게 녹음 사실을 알렸습니다.'
                    : 'I have informed participants.'}
              </span>
              <span id="recording-consent-detail" className="mt-1 block text-xs leading-relaxed text-slate-600">
                {isJapanese
                  ? 'この会議がローカルで録音・文字起こしされることを全員へ伝えました。この確認は法的助言ではありません。'
                  : isKorean
                    ? '이 회의가 로컬에서 녹음되고 전사된다는 사실을 모두에게 알렸습니다. 이 확인은 법률 자문이 아닙니다.'
                  : 'Everyone has been told that this meeting will be recorded and transcribed locally. This confirmation is not legal advice.'}
              </span>
            </span>
          </label>
          {consentSaveFailed && (
            <p className="mt-2 text-xs font-medium text-rose-700" role="alert">
              The local confirmation could not be saved. Check local storage and try again.
            </p>
          )}
        </div>
        <DialogFooter>
          <button
            type="button"
            onClick={onCancel}
            disabled={spokenTestStatus === 'running'}
            className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2"
          >
            <X className="h-4 w-4" />
            {isJapanese ? 'キャンセル' : isKorean ? '취소' : 'Cancel'}
          </button>
          <button
            type="button"
            onClick={() => void continueAfterMonitoringStops()}
            disabled={
              loading ||
              !preflight ||
              spokenTestStatus === 'running' ||
              startingRecording ||
              !participantsInformed
            }
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {startingRecording
              ? isJapanese ? '開始中...' : isKorean ? '시작 중...' : 'Starting...'
              : isJapanese ? '録音を開始' : isKorean ? '녹음 시작' : 'Start recording'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
