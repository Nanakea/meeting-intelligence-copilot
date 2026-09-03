export type IntelligenceSidecarPhase =
  | "starting"
  | "ready"
  | "degraded"
  | "restarting"
  | "unavailable"
  | "stopped";

export type IntelligenceSidecarFailureReason =
  | "missing_executable"
  | "incompatible_backend"
  | "startup_failed"
  | "backend_stopped";

export interface IntelligenceSidecarStatus {
  phase: IntelligenceSidecarPhase;
  reason: IntelligenceSidecarFailureReason | null;
  retryCount: number;
  maxRetryCount: number;
  retryAfterMs: number | null;
  authEnabled: boolean;
  managed: boolean;
  backendVersion: string | null;
}

export interface IntelligenceDispatcherRepairResult {
  completed: boolean;
  latestSequenceId: number | null;
}

export const INTELLIGENCE_BACKEND_LIFECYCLE_EVENT =
  "meeting-intelligence-backend-lifecycle";

const PHASES = new Set<IntelligenceSidecarPhase>([
  "starting",
  "ready",
  "degraded",
  "restarting",
  "unavailable",
  "stopped",
]);

const FAILURE_REASONS = new Set<IntelligenceSidecarFailureReason>([
  "missing_executable",
  "incompatible_backend",
  "startup_failed",
  "backend_stopped",
]);

export function validateIntelligenceSidecarStatus(
  candidate: unknown,
): IntelligenceSidecarStatus {
  if (!candidate || typeof candidate !== "object") {
    throw new Error("Meeting Intelligence lifecycle status is unavailable");
  }
  const record = candidate as Record<string, unknown>;
  if (
    typeof record.phase !== "string" ||
    !PHASES.has(record.phase as IntelligenceSidecarPhase) ||
    (record.reason !== null &&
      (typeof record.reason !== "string" ||
        !FAILURE_REASONS.has(record.reason as IntelligenceSidecarFailureReason))) ||
    !Number.isSafeInteger(record.retryCount) ||
    (record.retryCount as number) < 0 ||
    !Number.isSafeInteger(record.maxRetryCount) ||
    (record.maxRetryCount as number) < 1 ||
    (record.retryCount as number) > (record.maxRetryCount as number) ||
    (record.retryAfterMs !== null &&
      (!Number.isSafeInteger(record.retryAfterMs) || (record.retryAfterMs as number) < 1)) ||
    typeof record.authEnabled !== "boolean" ||
    typeof record.managed !== "boolean" ||
    (record.backendVersion !== null && typeof record.backendVersion !== "string")
  ) {
    throw new Error("Meeting Intelligence lifecycle status is invalid");
  }
  return record as unknown as IntelligenceSidecarStatus;
}

export function validateIntelligenceDispatcherRepairResult(
  candidate: unknown,
): IntelligenceDispatcherRepairResult {
  if (!candidate || typeof candidate !== "object") {
    throw new Error("Meeting Intelligence repair status is unavailable");
  }
  const record = candidate as Record<string, unknown>;
  if (
    typeof record.completed !== "boolean" ||
    (record.latestSequenceId !== null &&
      (!Number.isSafeInteger(record.latestSequenceId) ||
        (record.latestSequenceId as number) < 0))
  ) {
    throw new Error("Meeting Intelligence repair status is invalid");
  }
  return record as unknown as IntelligenceDispatcherRepairResult;
}

export function mergeIntelligenceRecoverySequence(
  current: number | null,
  candidate: number | null,
): number | null {
  if (current === null) return candidate;
  if (candidate === null) return current;
  return Math.max(current, candidate);
}

export function isIntelligenceRecoverySequenceCaughtUp(
  connectionSequence: number,
  targetSequence: number | null,
): boolean {
  return targetSequence !== null && connectionSequence >= targetSequence;
}

export function shouldReplayCanonicalHistoryAfterRepair({
  dispatcherCompleted,
  connectionSequence,
  targetSequence,
}: {
  dispatcherCompleted: boolean;
  connectionSequence: number;
  targetSequence: number | null;
}): boolean {
  return (
    !dispatcherCompleted ||
    (targetSequence !== null &&
      !isIntelligenceRecoverySequenceCaughtUp(connectionSequence, targetSequence))
  );
}

export function shouldResumeIntelligenceSocketOnSidecarReady({
  phase,
  hasSocket,
  retryScheduled,
  retryCount,
  maxRetries,
  resumeUsed,
}: {
  phase: IntelligenceSidecarPhase;
  hasSocket: boolean;
  retryScheduled: boolean;
  retryCount: number;
  maxRetries: number;
  resumeUsed: boolean;
}): boolean {
  return (
    phase === "ready" &&
    !hasSocket &&
    !retryScheduled &&
    retryCount >= maxRetries &&
    !resumeUsed
  );
}

function tauriAvailable(): boolean {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);
}

export async function getIntelligenceSidecarStatus(): Promise<IntelligenceSidecarStatus | null> {
  if (!tauriAvailable()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  const status = await invoke<unknown>("get_meeting_intelligence_backend_status");
  return validateIntelligenceSidecarStatus(status);
}

export async function retryIntelligenceSidecar(): Promise<boolean> {
  if (!tauriAvailable()) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("retry_meeting_intelligence_backend");
}

export async function repairIntelligenceDispatcher(): Promise<IntelligenceDispatcherRepairResult> {
  if (!tauriAvailable()) {
    return { completed: false, latestSequenceId: null };
  }
  const { invoke } = await import("@tauri-apps/api/core");
  const result = await invoke<unknown>("repair_meeting_intelligence_dispatcher");
  return validateIntelligenceDispatcherRepairResult(result);
}

export async function onIntelligenceSidecarStatus(
  callback: (status: IntelligenceSidecarStatus) => void,
): Promise<() => void> {
  if (!tauriAvailable()) return () => {};

  const { listen } = await import("@tauri-apps/api/event");
  return listen<unknown>(INTELLIGENCE_BACKEND_LIFECYCLE_EVENT, (event) => {
    try {
      callback(validateIntelligenceSidecarStatus(event.payload));
    } catch {
      // Ignore malformed lifecycle payloads rather than surfacing raw data.
    }
  });
}
