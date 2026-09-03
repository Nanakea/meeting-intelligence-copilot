import { invoke } from '@tauri-apps/api/core';

import type { IntelligenceSidecarStatus } from './intelligenceLifecycle.ts';
import {
  buildIntelligenceHeaders,
  buildIntelligenceWebSocketProtocols,
  buildIntelligenceWebSocketUrl,
  loadMeetingIntelligenceTransport,
} from './intelligenceTransport.ts';

const SPOKEN_PREFLIGHT_OPEN_TIMEOUT_MS = 10_000;
const SPOKEN_PREFLIGHT_SNAPSHOT_TIMEOUT_MS = 15_000;
const SPOKEN_PREFLIGHT_CLEANUP_TIMEOUT_MS = 5_000;

export type MeetingRetention =
  | 'seven_days'
  | 'thirty_days'
  | 'ninety_days'
  | 'forever';

export interface PilotSettings {
  retention: MeetingRetention;
  semanticBetaEnabled: boolean;
  semanticModel: 'qwen3:8b';
}

export interface RecordingConsentConfirmation {
  confirmed: true;
  confirmedAtEpochSeconds: number;
  appVersion: string;
}

export type WhisperModelIntegrity =
  | 'verified'
  | 'missing'
  | 'unapproved'
  | 'invalid_size'
  | 'invalid_hash'
  | 'invalid_format'
  | 'manifest_invalid';

export interface WhisperModelReadiness {
  modelId: string;
  manifestRevision: string | null;
  integrity: WhisperModelIntegrity;
  supportedLanguage: boolean;
  expectedBytes: number | null;
  actualBytes: number | null;
}

export interface PilotPreflight {
  language: string | null;
  languageSupported: boolean;
  localSttReady: boolean;
  localSttModel: string;
  whisperModelReadiness: WhisperModelReadiness | null;
  availableMemoryBytes: number;
  localSpeechVoiceReady: boolean;
  microphoneConfigured: boolean;
  microphoneAvailable: boolean;
  microphoneDeviceName: string;
  systemAudioConfigured: boolean;
  systemAudioAvailable: boolean;
  systemAudioDeviceName: string;
  backend: IntelligenceSidecarStatus;
  availableDiskBytes: number | null;
  pilotDataAvailableDiskBytes: number | null;
  recordingDirectory: string;
  localDataDirectory: string;
  retention: MeetingRetention;
}

export interface MeetingDataDeletionResult {
  deletedMeetings: number;
  fileCleanupFailures: number;
  recoveryCleanupFailed: boolean;
}

export async function confirmRecordingConsent(): Promise<RecordingConsentConfirmation> {
  return invoke<RecordingConsentConfirmation>('confirm_meeting_recording_consent', {
    confirmed: true,
  });
}

interface SpokenPreflightBackendResult {
  sessionId: string;
  receivedSequenceId: number;
  stateVersion: number;
  transcriptCharacterCount: number;
  durationMs: number;
}

export interface SpokenPipelinePreflightResult {
  transcriptCharacterCount: number;
  durationMs: number;
}

export function createSpokenPreflightSessionId(
  randomValues: (target: Uint8Array) => Uint8Array = (target) =>
    crypto.getRandomValues(target),
): string {
  const bytes = randomValues(new Uint8Array(16));
  const suffix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `meeting-intel-${suffix}`;
}

function waitForSocketOpen(socket: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error('The local intelligence WebSocket did not open'));
    }, SPOKEN_PREFLIGHT_OPEN_TIMEOUT_MS);
    socket.addEventListener(
      'open',
      () => {
        window.clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
    socket.addEventListener(
      'error',
      () => {
        window.clearTimeout(timeout);
        reject(new Error('The local intelligence WebSocket is unavailable'));
      },
      { once: true },
    );
  });
}

async function waitForSnapshot(
  snapshotReceived: Promise<void>,
): Promise<void> {
  let timeout: number | undefined;
  try {
    await Promise.race([
      snapshotReceived,
      new Promise<never>((_, reject) => {
        timeout = window.setTimeout(
          () => reject(new Error('The spoken preflight snapshot did not reach the panel')),
          SPOKEN_PREFLIGHT_SNAPSHOT_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    if (timeout !== undefined) window.clearTimeout(timeout);
  }
}

async function removeSpokenPreflightSession(
  transport: Awaited<ReturnType<typeof loadMeetingIntelligenceTransport>>,
  sessionId: string,
): Promise<void> {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    SPOKEN_PREFLIGHT_CLEANUP_TIMEOUT_MS,
  );
  try {
    const response = await fetch(
      `${transport.baseUrl}/meeting/${encodeURIComponent(sessionId)}`,
      {
        method: 'DELETE',
        headers: buildIntelligenceHeaders(transport),
        signal: controller.signal,
      },
    );
    if (!response.ok) {
      throw new Error('The spoken preflight session could not be removed');
    }
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error('The spoken preflight session cleanup timed out');
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function validSpokenPreflightResult(
  candidate: unknown,
  sessionId: string,
): candidate is SpokenPreflightBackendResult {
  if (!candidate || typeof candidate !== 'object') return false;
  const result = candidate as Record<string, unknown>;
  return (
    !('text' in result) &&
    !('transcript' in result) &&
    !('token' in result) &&
    !('speaker' in result) &&
    result.sessionId === sessionId &&
    result.receivedSequenceId === 0 &&
    Number.isSafeInteger(result.stateVersion) &&
    (result.stateVersion as number) >= 1 &&
    Number.isSafeInteger(result.transcriptCharacterCount) &&
    (result.transcriptCharacterCount as number) > 0 &&
    Number.isSafeInteger(result.durationMs) &&
    (result.durationMs as number) >= 0
  );
}

export async function runSpokenPipelinePreflight(
  language: 'ja' | 'en' | 'ko',
): Promise<SpokenPipelinePreflightResult> {
  const transport = await loadMeetingIntelligenceTransport();
  const sessionId = createSpokenPreflightSessionId();
  const socketUrl = buildIntelligenceWebSocketUrl(transport, sessionId);
  const protocols = buildIntelligenceWebSocketProtocols(transport);
  const socket =
    protocols.length > 0
      ? new WebSocket(socketUrl, protocols)
      : new WebSocket(socketUrl);
  let maximumSnapshotVersion = -1;
  let requiredSnapshotVersion: number | null = null;
  let resolveSnapshot: () => void = () => undefined;
  let rejectSnapshot: (error: Error) => void = () => undefined;
  const snapshotReceived = new Promise<void>((resolve, reject) => {
    resolveSnapshot = resolve;
    rejectSnapshot = reject;
  });
  socket.addEventListener('message', (event) => {
    try {
      const snapshot = JSON.parse(String(event.data)) as Record<string, unknown>;
      if (
        snapshot.meeting_id === sessionId &&
        Number.isSafeInteger(snapshot.version) &&
        (snapshot.version as number) >= 0
      ) {
        maximumSnapshotVersion = Math.max(
          maximumSnapshotVersion,
          snapshot.version as number,
        );
        if (
          requiredSnapshotVersion !== null &&
          maximumSnapshotVersion >= requiredSnapshotVersion
        ) {
          resolveSnapshot();
        }
      }
    } catch {
      // Ignore malformed frames; the bounded timeout reports a safe failure.
    }
  });
  socket.addEventListener('close', () => {
    if (
      requiredSnapshotVersion !== null &&
      maximumSnapshotVersion < requiredSnapshotVersion
    ) {
      rejectSnapshot(new Error('The spoken preflight WebSocket closed early'));
    }
  });

  let result: SpokenPreflightBackendResult | null = null;
  let failure: unknown = null;
  try {
    await waitForSocketOpen(socket);
    const candidate = await invoke<unknown>(
      'run_meeting_intelligence_spoken_preflight',
      { sessionId, language },
    );
    if (!validSpokenPreflightResult(candidate, sessionId)) {
      throw new Error('The spoken preflight acknowledgement is invalid');
    }
    result = candidate;
    requiredSnapshotVersion = result.stateVersion;
    if (maximumSnapshotVersion >= requiredSnapshotVersion) {
      resolveSnapshot();
    }
    await waitForSnapshot(snapshotReceived);
  } catch (error) {
    failure = error;
  } finally {
    socket.close();
    try {
      await removeSpokenPreflightSession(transport, sessionId);
    } catch (error) {
      failure ??= error;
    }
  }
  if (failure) throw failure;
  if (!result) throw new Error('The spoken preflight did not complete');
  return {
    transcriptCharacterCount: result.transcriptCharacterCount,
    durationMs: result.durationMs,
  };
}

export async function getPilotSettings(): Promise<PilotSettings> {
  return invoke<PilotSettings>('get_meeting_intelligence_pilot_settings');
}

export async function savePilotSettings(settings: PilotSettings): Promise<PilotSettings> {
  return invoke<PilotSettings>('set_meeting_intelligence_pilot_settings', { settings });
}

export async function applyMeetingRetention(
  settings: PilotSettings,
): Promise<MeetingDataDeletionResult> {
  return invoke<MeetingDataDeletionResult>('apply_meeting_intelligence_retention', { settings });
}

export async function deleteAllStoredMeetings(): Promise<MeetingDataDeletionResult> {
  return invoke<MeetingDataDeletionResult>('delete_all_local_meeting_data');
}

export async function getPilotPreflight(
  language: string | null,
  microphoneDevice: string | null,
  systemAudioDevice: string | null,
): Promise<PilotPreflight> {
  return invoke<PilotPreflight>('get_meeting_intelligence_preflight', {
    language,
    microphoneDevice,
    systemAudioDevice,
  });
}

export async function exportAggregateDiagnostics(): Promise<string> {
  return invoke<string>('export_meeting_intelligence_diagnostics');
}
