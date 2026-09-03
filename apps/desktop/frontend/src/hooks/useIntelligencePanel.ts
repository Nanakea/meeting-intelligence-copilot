// Connects to the Meeting Intelligence Copilot backend and exposes the latest
// MeetingState snapshot, for the real Meetily-integrated panel (Phase 8).
// Ported/adapted from the standalone dev UI's useMeetingSocket.ts:
// - adds the `lang` query param (session-level language, never inferred)
// - only connects when `enabled` (recording active + lang is ja/en/ko)
// - tracks connection status for the panel's required states (Phase 8)
// - bounded auto-reconnect so a transient backend restart recovers on its own
//   without ever blocking or breaking Meetily's own recording (Phase 9)

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import { transcriptService, type TranscriptHistorySegment } from "../services/transcriptService";
import {
  emptyLiveIntelligenceSnapshot,
  type LiveIntelligenceSnapshot,
} from "../services/intelligenceContracts";
import {
  getIntelligenceSidecarStatus,
  isIntelligenceRecoverySequenceCaughtUp,
  mergeIntelligenceRecoverySequence,
  onIntelligenceSidecarStatus,
  repairIntelligenceDispatcher,
  retryIntelligenceSidecar,
  shouldReplayCanonicalHistoryAfterRepair,
  shouldResumeIntelligenceSocketOnSidecarReady,
  type IntelligenceSidecarStatus,
} from "../services/intelligenceLifecycle";
import {
  buildIntelligenceHeaders,
  buildIntelligenceWebSocketProtocols,
  buildIntelligenceWebSocketUrl,
  loadMeetingIntelligenceTransport,
  validateLiveBatchAcknowledgement,
  type MeetingIntelligenceTransport,
} from "../services/intelligenceTransport";
import { visibleIntelligenceFingerprint } from "../services/intelligenceProjection";

export type IntelligenceConnectionStatus =
  | "idle" // not enabled yet (not recording, or language not configured)
  | "connecting"
  | "connected"
  | "recovering" // was connected, lost the socket, retrying
  | "reset" // the backend restarted and the prior in-memory context was lost
  | "recovered" // transcript history was replayed into a fresh backend state
  | "unavailable"; // exhausted retries -- backend not reachable

const MAX_RETRIES = 5;
const RETRY_BASE_MS = 1000;
const PERSISTED_SNAPSHOT_GRACE_MS = 500;
type IntelligenceLanguage = "ja" | "en" | "ko";

function isIntelligenceLanguage(value: string | null): value is IntelligenceLanguage {
  return value === "ja" || value === "en" || value === "ko";
}

// Module-level, not a per-call default parameter: a default parameter value
// like `(url) => new WebSocket(url)` is re-created on every call, which would
// make the effect below re-run (tearing down and reconnecting the socket) on
// every re-render since it's referenced in the dependency array.
export type IntelligenceSocketFactory = (url: string, protocols?: string[]) => WebSocket;
const DEFAULT_SOCKET_FACTORY: IntelligenceSocketFactory = (url, protocols = []) =>
  protocols.length > 0 ? new WebSocket(url, protocols) : new WebSocket(url);

function replayPayload(segment: TranscriptHistorySegment) {
  const start = segment.audio_start_time ?? 0;
  const end = segment.audio_end_time ?? start + (segment.duration ?? 0);

  return {
    text: segment.text,
    timestamp: segment.display_time,
    source: "Audio",
    sequence_id: segment.sequence_id,
    chunk_start_time: start,
    is_partial: false,
    confidence: segment.confidence,
    audio_start_time: start,
    audio_end_time: end,
    duration: segment.duration ?? Math.max(0, end - start),
  };
}

async function replayTranscriptHistory(
  transport: MeetingIntelligenceTransport,
  sessionId: string,
  lang: IntelligenceLanguage,
): Promise<{ eventCount: number; latestSequenceId: number | null }> {
  const history = await transcriptService.getTranscriptHistory();
  const ordered = [...history].sort((a, b) => a.sequence_id - b.sequence_id);

  for (let index = 0; index < ordered.length; index += 200) {
    const payloads = ordered.slice(index, index + 200).map(replayPayload);
    const response = await fetch(
      `${transport.baseUrl}/ingest/live/${encodeURIComponent(sessionId)}/batch`,
      {
        method: "POST",
        headers: buildIntelligenceHeaders(transport),
        body: JSON.stringify({ adapter: "meetily", lang, payloads }),
      },
    );
    if (!response.ok) {
      throw new Error(`Transcript replay failed with HTTP ${response.status}`);
    }
    const acknowledgement = validateLiveBatchAcknowledgement(await response.json());
    const expectedNextSequence = payloads[payloads.length - 1].sequence_id + 1;
    if (
      acknowledgement.rejected_sequence_ids.length > 0 ||
      acknowledgement.next_expected_sequence_id < expectedNextSequence
    ) {
      throw new Error("Transcript replay acknowledgement reported an incomplete history");
    }
  }

  return {
    eventCount: ordered.length,
    latestSequenceId: ordered.at(-1)?.sequence_id ?? null,
  };
}

function parseMeetingStateSnapshot(
  raw: string,
  sessionId: string,
): LiveIntelligenceSnapshot | null {
  try {
    const candidate = JSON.parse(raw) as Record<string, unknown>;
    if (
      !candidate ||
      candidate.meeting_id !== sessionId ||
      !Number.isSafeInteger(candidate.version) ||
      (candidate.version as number) < 0 ||
      !Array.isArray(candidate.pain_points) ||
      !Array.isArray(candidate.facts) ||
      !Array.isArray(candidate.gaps) ||
      !Array.isArray(candidate.suggestions) ||
      !Array.isArray(candidate.semantic_hints) ||
      !Array.isArray(candidate.identity_review_candidates) ||
      !Array.isArray(candidate.governance_candidates) ||
      !Array.isArray(candidate.governance_alerts) ||
      !Number.isSafeInteger(candidate.last_event_seq) ||
      (candidate.last_event_seq as number) < -1
    ) {
      return null;
    }
    return candidate as unknown as LiveIntelligenceSnapshot;
  } catch {
    return null;
  }
}

export interface UseIntelligencePanel {
  state: LiveIntelligenceSnapshot;
  status: IntelligenceConnectionStatus;
  sidecarStatus: IntelligenceSidecarStatus | null;
  snapshotMarkerRef: MutableRefObject<HTMLElement | null>;
  latestStateVersionRef: MutableRefObject<number>;
  retry: () => void;
}

export function useIntelligencePanel(
  sessionId: string | null,
  lang: string | null,
  enabled: boolean,
  socketFactory: IntelligenceSocketFactory = DEFAULT_SOCKET_FACTORY,
): UseIntelligencePanel {
  const [state, setState] = useState<LiveIntelligenceSnapshot>(() =>
    emptyLiveIntelligenceSnapshot(sessionId ?? ""),
  );
  const [status, setStatus] = useState<IntelligenceConnectionStatus>("idle");
  const [sidecarStatus, setSidecarStatus] = useState<IntelligenceSidecarStatus | null>(null);
  const [retryGeneration, setRetryGeneration] = useState(0);
  const retriesRef = useRef(0);
  const everConnectedRef = useRef(false);
  const snapshotMarkerRef = useRef<HTMLElement | null>(null);
  const latestStateVersionRef = useRef(0);

  const retry = useCallback(() => {
    setState(emptyLiveIntelligenceSnapshot(sessionId ?? ""));
    setStatus("connecting");
    void retryIntelligenceSidecar()
      .catch(() => false)
      .finally(() => setRetryGeneration((generation) => generation + 1));
  }, [sessionId]);

  useEffect(() => {
    if (!enabled || !sessionId || !isIntelligenceLanguage(lang)) {
      setStatus("idle");
      setSidecarStatus(null);
      return;
    }
    const activeSessionId = sessionId;
    const activeLang = lang;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unlistenSidecarStatus: (() => void) | null = null;
    let recoveryTargetSequence: number | null = null;
    let latestEventSequence = -1;
    let connectionEventSequence = -1;
    let recoverySuccessStatus: IntelligenceConnectionStatus = "recovered";
    let socketGeneration = 0;
    let latestSidecarPhase: IntelligenceSidecarStatus["phase"] | null = null;
    let readyResumeUsed = false;
    let visibleFingerprint = visibleIntelligenceFingerprint(
      emptyLiveIntelligenceSnapshot(activeSessionId),
    );
    retriesRef.current = 0;
    everConnectedRef.current = false;
    latestStateVersionRef.current = 0;
    setState(emptyLiveIntelligenceSnapshot(activeSessionId));

    function updateSnapshotMarker(version: number, sequence: number) {
      latestStateVersionRef.current = version;
      snapshotMarkerRef.current?.setAttribute("data-intelligence-version", String(version));
      snapshotMarkerRef.current?.setAttribute(
        "data-intelligence-last-event-seq",
        String(sequence),
      );
    }

    function resetVisibleState() {
      const empty = emptyLiveIntelligenceSnapshot(activeSessionId);
      visibleFingerprint = visibleIntelligenceFingerprint(empty);
      updateSnapshotMarker(empty.version, empty.last_event_seq);
      setState(empty);
    }

    function markRecoveredIfCaughtUp() {
      if (
        isIntelligenceRecoverySequenceCaughtUp(
          connectionEventSequence,
          recoveryTargetSequence,
        )
      ) {
        recoveryTargetSequence = null;
        setStatus(recoverySuccessStatus);
        return true;
      }
      return false;
    }

    async function recoverFromTranscriptHistory(transport: MeetingIntelligenceTransport) {
      try {
        const replay = await replayTranscriptHistory(
          transport,
          activeSessionId,
          activeLang,
        );
        if (cancelled) return;
        if (replay.eventCount === 0) {
          if (markRecoveredIfCaughtUp()) return;
          recoveryTargetSequence = null;
          setStatus(recoverySuccessStatus === "connected" ? "connected" : "reset");
          return;
        }
        recoveryTargetSequence = mergeIntelligenceRecoverySequence(
          recoveryTargetSequence,
          replay.latestSequenceId,
        );
        if (!markRecoveredIfCaughtUp()) {
          setStatus("recovering");
        }
      } catch {
        if (!cancelled) setStatus("reset");
      }
    }

    async function repairRecoveredSession(transport: MeetingIntelligenceTransport) {
      try {
        const repair = await repairIntelligenceDispatcher();
        if (cancelled) return;
        if (repair.latestSequenceId !== null) {
          recoveryTargetSequence = mergeIntelligenceRecoverySequence(
            recoveryTargetSequence,
            repair.latestSequenceId,
          );
        }
        // The replacement socket may have caught up while this serialized
        // dispatcher command was waiting behind queued transcript sends. A
        // late incomplete repair result must not regress a valid snapshot back
        // to a permanent recovering state.
        if (markRecoveredIfCaughtUp()) return;
        if (repair.completed) {
          if (recoveryTargetSequence === null) {
            setStatus(recoverySuccessStatus);
            return;
          }
          if (markRecoveredIfCaughtUp()) return;

          // An empty dispatcher suffix is "complete" even after the backend
          // restarted from sequence zero. Give a durable snapshot one brief
          // chance to arrive, then repair the reset from canonical history.
          setStatus("recovering");
          await new Promise((resolve) => setTimeout(resolve, PERSISTED_SNAPSHOT_GRACE_MS));
          if (cancelled || markRecoveredIfCaughtUp()) return;
          if (
            shouldReplayCanonicalHistoryAfterRepair({
              dispatcherCompleted: repair.completed,
              connectionSequence: connectionEventSequence,
              targetSequence: recoveryTargetSequence,
            })
          ) {
            await recoverFromTranscriptHistory(transport);
          }
          return;
        }
        // The dispatcher owns only unacknowledged work. If its bounded suffix
        // cannot repair a full backend reset, replay canonical recording history.
        await recoverFromTranscriptHistory(transport);
      } catch {
        await recoverFromTranscriptHistory(transport);
      }
    }

    function resumeExhaustedSocketIfReady(
      phase: IntelligenceSidecarStatus["phase"],
    ): boolean {
      if (
        !shouldResumeIntelligenceSocketOnSidecarReady({
          phase,
          hasSocket: ws !== null,
          retryScheduled: retryTimer !== null,
          retryCount: retriesRef.current,
          maxRetries: MAX_RETRIES,
          resumeUsed: readyResumeUsed,
        })
      ) {
        return false;
      }
      readyResumeUsed = true;
      retriesRef.current = 0;
      void connect();
      return true;
    }

    async function refreshSidecarStatus() {
      try {
        const lifecycle = await getIntelligenceSidecarStatus();
        if (!cancelled) {
          latestSidecarPhase = lifecycle?.phase ?? null;
          if (latestSidecarPhase !== "ready") readyResumeUsed = false;
          setSidecarStatus(lifecycle);
          if (latestSidecarPhase === "ready") {
            resumeExhaustedSocketIfReady(latestSidecarPhase);
          }
        }
      } catch {
        if (!cancelled) setSidecarStatus(null);
      }
    }

    function scheduleRetry(generation?: number) {
      if (cancelled || (generation !== undefined && generation !== socketGeneration)) return;
      if (retryTimer) return;
      socketGeneration += 1;
      const staleSocket = ws;
      ws = null;
      staleSocket?.close();
      void refreshSidecarStatus();
      resetVisibleState();
      connectionEventSequence = -1;
      recoveryTargetSequence = mergeIntelligenceRecoverySequence(
        recoveryTargetSequence,
        latestEventSequence >= 0 ? latestEventSequence : null,
      );
      if (retriesRef.current >= MAX_RETRIES) {
        if (resumeExhaustedSocketIfReady(latestSidecarPhase ?? "unavailable")) return;
        setStatus("unavailable");
        return;
      }
      setStatus(everConnectedRef.current ? "recovering" : "connecting");
      const delay = RETRY_BASE_MS * 2 ** retriesRef.current;
      retriesRef.current += 1;
      retryTimer = setTimeout(() => {
        retryTimer = null;
        void connect();
      }, delay);
    }

    async function connect() {
      if (cancelled) return;
      const generation = ++socketGeneration;
      setStatus(everConnectedRef.current ? "recovering" : "connecting");

      let transport: MeetingIntelligenceTransport;
      try {
        transport = await loadMeetingIntelligenceTransport();
      } catch {
        scheduleRetry(generation);
        return;
      }
      if (cancelled || generation !== socketGeneration) return;

      let activeSocket: WebSocket;
      try {
        activeSocket = socketFactory(
          buildIntelligenceWebSocketUrl(transport, activeSessionId),
          buildIntelligenceWebSocketProtocols(transport),
        );
      } catch {
        scheduleRetry(generation);
        return;
      }
      ws = activeSocket;
      activeSocket.addEventListener("open", () => {
        if (cancelled || generation !== socketGeneration) return;
        const wasConnected = everConnectedRef.current;
        retriesRef.current = 0;
        everConnectedRef.current = true;
        // Reset to a clean slate on every (re)connection, not just the initial
        // mount. Independent review finding (confirmed real): after a backend
        // restart, its in-memory state resets to version 0/1/2..., but this
        // hook's version guard (`incoming.version > current.version`) would
        // otherwise reject all of it forever against a stale pre-restart state
        // held from before the reconnect, leaving the panel silently frozen.
        resetVisibleState();
        connectionEventSequence = -1;
        recoveryTargetSequence = mergeIntelligenceRecoverySequence(
          recoveryTargetSequence,
          latestEventSequence >= 0 ? latestEventSequence : null,
        );
        recoverySuccessStatus = wasConnected ? "recovered" : "connected";
        setStatus(wasConnected ? "reset" : "connecting");
        void repairRecoveredSession(transport);
      });
      activeSocket.addEventListener("message", (event: MessageEvent) => {
        if (cancelled || generation !== socketGeneration) return;
        const incoming = parseMeetingStateSnapshot(String(event.data), activeSessionId);
        if (!incoming) return;
        latestEventSequence = Math.max(latestEventSequence, incoming.last_event_seq);
        connectionEventSequence = Math.max(connectionEventSequence, incoming.last_event_seq);
        updateSnapshotMarker(incoming.version, incoming.last_event_seq);
        const incomingFingerprint = visibleIntelligenceFingerprint(incoming);
        if (incomingFingerprint !== visibleFingerprint) {
          visibleFingerprint = incomingFingerprint;
          setState(incoming);
        }
        markRecoveredIfCaughtUp();
      });
      activeSocket.addEventListener("close", () => {
        if (ws === activeSocket) ws = null;
        scheduleRetry(generation);
      });
    }

    void refreshSidecarStatus();
    void onIntelligenceSidecarStatus((nextStatus) => {
      if (cancelled) return;
      latestSidecarPhase = nextStatus.phase;
      if (nextStatus.phase !== "ready") readyResumeUsed = false;
      setSidecarStatus(nextStatus);
      // Sidecar recovery can legitimately outlast the socket's bounded retry
      // schedule (for example while quarantining and replaying a corrupt
      // cache). Once Rust reports the authenticated backend ready, re-arm an
      // exhausted socket exactly once instead of leaving the panel permanently
      // unavailable until the user presses Retry.
      resumeExhaustedSocketIfReady(nextStatus.phase);
    }).then((unlisten) => {
      if (!cancelled) {
        unlistenSidecarStatus = unlisten;
      } else {
        unlisten();
      }
    });
    void connect();
    return () => {
      cancelled = true;
      socketGeneration += 1;
      if (retryTimer) clearTimeout(retryTimer);
      unlistenSidecarStatus?.();
      ws?.close();
    };
  }, [sessionId, lang, enabled, socketFactory, retryGeneration]);

  return {
    state,
    status,
    sidecarStatus,
    snapshotMarkerRef,
    latestStateVersionRef,
    retry,
  };
}
