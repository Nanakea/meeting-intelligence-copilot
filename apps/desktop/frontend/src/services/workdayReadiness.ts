import type { PilotPreflight } from './intelligencePilot';

export const MINIMUM_WORKDAY_DISK_BYTES = 2 * 1024 * 1024 * 1024;

export type WorkdayReadinessCheckId =
  | 'language'
  | 'local_stt'
  | 'microphone'
  | 'system_audio'
  | 'backend'
  | 'storage'
  | 'retention';

export type WorkdayReadinessCheckState = 'pass' | 'warning' | 'action_required';

export interface WorkdayReadinessCheck {
  id: WorkdayReadinessCheckId;
  state: WorkdayReadinessCheckState;
}

export interface WorkdayReadinessSummary {
  checks: WorkdayReadinessCheck[];
  localRecordingReady: boolean;
  liveCopilotReady: boolean;
  hasWarnings: boolean;
}

function storageState(preflight: PilotPreflight): WorkdayReadinessCheckState {
  const values = [
    preflight.availableDiskBytes,
    preflight.pilotDataAvailableDiskBytes,
  ];
  if (values.some((value) => value !== null && value < MINIMUM_WORKDAY_DISK_BYTES)) {
    return 'action_required';
  }
  return values.every((value) => value !== null) ? 'pass' : 'warning';
}

export function evaluateWorkdayReadiness(
  preflight: PilotPreflight,
): WorkdayReadinessSummary {
  const checks: WorkdayReadinessCheck[] = [
    {
      id: 'language',
      state: preflight.languageSupported ? 'pass' : 'action_required',
    },
    {
      id: 'local_stt',
      state: preflight.localSttReady ? 'pass' : 'action_required',
    },
    {
      id: 'microphone',
      state: preflight.microphoneAvailable ? 'pass' : 'action_required',
    },
    {
      id: 'system_audio',
      state: preflight.systemAudioAvailable ? 'pass' : 'action_required',
    },
    {
      id: 'backend',
      state: preflight.backend.phase === 'ready' ? 'pass' : 'warning',
    },
    { id: 'storage', state: storageState(preflight) },
    { id: 'retention', state: 'pass' },
  ];
  const localRecordingReady = checks
    .filter((check) => check.id !== 'backend' && check.id !== 'retention')
    .every((check) => check.state !== 'action_required');
  const liveCopilotReady =
    localRecordingReady &&
    checks.find((check) => check.id === 'backend')?.state === 'pass';

  return {
    checks,
    localRecordingReady,
    liveCopilotReady,
    hasWarnings: checks.some((check) => check.state === 'warning'),
  };
}
