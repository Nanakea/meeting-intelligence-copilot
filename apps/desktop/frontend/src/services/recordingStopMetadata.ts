import { invoke } from '@tauri-apps/api/core';
import type { RecordingStoppedPayload } from './recordingService.ts';
import type { Transcript } from '../types/index.ts';

const MAX_BUFFERED_SESSIONS = 8;

const payloads = new Map<string, RecordingStoppedPayload>();
const claimedPostProcessingSessions = new Set<string>();
let anonymousPostProcessingClaimed = false;
const waiters = new Map<
  string,
  Set<(payload: RecordingStoppedPayload) => void>
>();

function boundedSet(sessionId: string, payload: RecordingStoppedPayload): void {
  payloads.delete(sessionId);
  payloads.set(sessionId, payload);
  while (payloads.size > MAX_BUFFERED_SESSIONS) {
    const oldest = payloads.keys().next().value;
    if (oldest === undefined) break;
    payloads.delete(oldest);
  }
}

export function publishRecordingStoppedMetadata(
  payload: RecordingStoppedPayload,
  fallbackSessionId: string | null,
): string | null {
  const sessionId = payload.recording_session_id ?? fallbackSessionId;
  if (!sessionId) return null;

  boundedSet(sessionId, payload);
  const pending = waiters.get(sessionId);
  if (pending) {
    waiters.delete(sessionId);
    pending.forEach((resolve) => resolve(payload));
  }
  return sessionId;
}

export async function waitForRecordingStoppedMetadata(
  sessionId: string,
  timeoutMilliseconds = 2_000,
): Promise<RecordingStoppedPayload | null> {
  const existing = payloads.get(sessionId);
  if (existing) return existing;

  return new Promise((resolve) => {
    const pending = waiters.get(sessionId) ?? new Set();
    const receive = (payload: RecordingStoppedPayload) => {
      clearTimeout(timeout);
      resolve(payload);
    };
    pending.add(receive);
    waiters.set(sessionId, pending);

    const timeout = setTimeout(() => {
      const current = waiters.get(sessionId);
      current?.delete(receive);
      if (current?.size === 0) waiters.delete(sessionId);
      resolve(null);
    }, timeoutMilliseconds);
  });
}

export function clearRecordingStoppedMetadata(sessionId: string): void {
  payloads.delete(sessionId);
}

export async function recoverNativeRecordingStoppedMetadata(
  sessionId: string,
): Promise<RecordingStoppedPayload | null> {
  try {
    const payload = await invoke<RecordingStoppedPayload | null>(
      'get_recording_stop_metadata',
      { sessionId },
    );
    if (!payload || payload.recording_session_id !== sessionId) return null;
    publishRecordingStoppedMetadata(payload, null);
    return payload;
  } catch {
    return null;
  }
}

export async function recoverLatestNativeRecordingStoppedMetadata(): Promise<RecordingStoppedPayload | null> {
  try {
    const payload = await invoke<RecordingStoppedPayload | null>(
      'get_latest_recording_stop_metadata',
    );
    const sessionId = payload?.recording_session_id;
    if (!payload || !sessionId) return null;
    publishRecordingStoppedMetadata(payload, null);
    return payload;
  } catch {
    return null;
  }
}

interface NativeFinalizedTranscript extends Omit<Transcript, 'id'> {
  source: string;
  sequence_id: number;
}

export async function recoverNativeRecordingStoppedTranscripts(
  sessionId: string,
): Promise<Transcript[]> {
  try {
    const updates = await invoke<NativeFinalizedTranscript[]>(
      'get_recording_stop_transcripts',
      { sessionId },
    );
    return updates.map((update) => ({
      ...update,
      id: `seg_${update.sequence_id}`,
    }));
  } catch {
    return [];
  }
}

export function mergeFinalizedTranscriptHistory(
  current: Transcript[],
  finalized: Transcript[],
): Transcript[] {
  const unsequenced = current.filter(
    (transcript) => !Number.isSafeInteger(transcript.sequence_id),
  );
  const bySequence = new Map<number, Transcript>();
  for (const transcript of [...current, ...finalized]) {
    if (Number.isSafeInteger(transcript.sequence_id)) {
      bySequence.set(transcript.sequence_id!, transcript);
    }
  }
  return [
    ...unsequenced,
    ...[...bySequence.values()].sort(
      (left, right) => left.sequence_id! - right.sequence_id!,
    ),
  ];
}

export async function acknowledgeNativeRecordingStoppedMetadata(
  sessionId: string,
): Promise<void> {
  try {
    await invoke('acknowledge_recording_stop_metadata', { sessionId });
  } catch {
    // The native cache is bounded; a failed acknowledgement is safe to retry.
  }
}

export function claimRecordingPostProcessing(sessionId: string | null): boolean {
  if (sessionId === null) {
    if (anonymousPostProcessingClaimed) return false;
    anonymousPostProcessingClaimed = true;
    return true;
  }
  if (claimedPostProcessingSessions.has(sessionId)) return false;
  claimedPostProcessingSessions.add(sessionId);
  while (claimedPostProcessingSessions.size > MAX_BUFFERED_SESSIONS) {
    const oldest = claimedPostProcessingSessions.values().next().value;
    if (oldest === undefined) break;
    claimedPostProcessingSessions.delete(oldest);
  }
  return true;
}

export function releaseRecordingPostProcessing(sessionId: string | null): void {
  if (sessionId === null) {
    anonymousPostProcessingClaimed = false;
  } else {
    claimedPostProcessingSessions.delete(sessionId);
  }
}
