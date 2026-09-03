import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const DEFAULT_DURATION_SECONDS = 4 * 60 * 60;
const DEFAULT_BACKEND_PORT = 8124;
const DEFAULT_CDP_PORT = 9224;
const DEFAULT_SYSTEM_DEVICE = "CABLE Input (VB-Audio Virtual Cable)";
const CDP_COMMAND_TIMEOUT_MS = 20_000;
const CDP_RECOVERY_TIMEOUT_MS = 30_000;
const MAXIMUM_CDP_RECONNECTS = 8;
const FOUR_HOUR_GATE_SECONDS = 4 * 60 * 60;
const MINIMUM_GATE_ACCEPTED_EVENTS = 10_000;
const MAXIMUM_RSS_GROWTH_BYTES = 100 * 1024 * 1024;
const MAXIMUM_CACHE_BYTES = 64 * 1024 * 1024;
const MAXIMUM_RECOVERY_SECONDS = 30;
const MAXIMUM_STOP_SECONDS = 15;
const MAXIMUM_TRANSPORT_TO_PANEL_P95_MS = 500;
const PANEL_PROJECTION_TIMEOUT_MS = 30_000;
const PANEL_PROJECTION_POLL_MS = 25;
const MAXIMUM_MOUNTED_HISTORY_ROWS = 64;
const MINIMUM_FOUR_HOUR_MEMORY_WARMUP_SECONDS = 5 * 60;
const PROCESS_ROLES = ["candidate", "webview", "backend", "other"];
const SAFE_PHASES = new Set([
  "starting",
  "ready",
  "degraded",
  "restarting",
  "unavailable",
  "stopped",
]);
const RETRY_SAFE_SOAK_COMMANDS = new Set([
  "emit_meeting_intelligence_soak_update",
  "get_meeting_intelligence_backend_status",
  "get_meeting_intelligence_diagnostics",
  "get_recording_stop_diagnostics",
  "get_recording_state",
  "get_transcription_status",
  "set_language_preference",
  "stop_recording",
]);

function requiredValue(args, index, option) {
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`missing value for ${option}`);
  }
  return value;
}

function positiveInteger(value, option, minimum = 1) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum) {
    throw new Error(`${option} must be an integer >= ${minimum}`);
  }
  return parsed;
}

export function parseSoakArgs(args) {
  const options = {
    durationSeconds: DEFAULT_DURATION_SECONDS,
    backendPort: DEFAULT_BACKEND_PORT,
    cdpPort: DEFAULT_CDP_PORT,
    language: "en",
    systemDevice: DEFAULT_SYSTEM_DEVICE,
    pauseIntervalSeconds: 10 * 60,
    crashIntervalSeconds: 15 * 60,
    initialCrashSeconds: 30,
    corruptCrashSeconds: 75,
    audioSegmentSeconds: 30,
    sampleIntervalSeconds: 60,
    startupTimeoutSeconds: 60,
    memoryWarmupSeconds: 0,
    syntheticEventIntervalMilliseconds: 1250,
  };

  for (let index = 0; index < args.length; index += 1) {
    const option = args[index];
    if (option === "--skip-occupied-port") {
      options.skipOccupiedPort = true;
      continue;
    }
    if (option === "--skip-corrupt-cache") {
      options.skipCorruptCache = true;
      continue;
    }
    if (option === "--skip-synthetic-events") {
      options.skipSyntheticEvents = true;
      continue;
    }
    if (option === "--skip-backend-crashes") {
      options.skipBackendCrashes = true;
      continue;
    }
    const value = requiredValue(args, index, option);
    index += 1;
    switch (option) {
      case "--meetily-exe": options.meetilyExe = resolve(value); break;
      case "--audio": options.audio = resolve(value); break;
      case "--ffplay": options.ffplay = resolve(value); break;
      case "--cache-directory": options.cacheDirectory = resolve(value); break;
      case "--output": options.output = resolve(value); break;
      case "--assistant-commit": options.assistantCommit = value.toLowerCase(); break;
      case "--meetily-commit": options.meetilyCommit = value.toLowerCase(); break;
      case "--duration-seconds": options.durationSeconds = positiveInteger(value, option, 60); break;
      case "--backend-port": options.backendPort = positiveInteger(value, option); break;
      case "--cdp-port": options.cdpPort = positiveInteger(value, option); break;
      case "--language": options.language = value; break;
      case "--system-device": options.systemDevice = value; break;
      case "--pause-interval-seconds": options.pauseIntervalSeconds = positiveInteger(value, option, 10); break;
      case "--crash-interval-seconds": options.crashIntervalSeconds = positiveInteger(value, option, 30); break;
      case "--initial-crash-seconds": options.initialCrashSeconds = positiveInteger(value, option, 10); break;
      case "--corrupt-crash-seconds": options.corruptCrashSeconds = positiveInteger(value, option, 20); break;
      case "--audio-segment-seconds": options.audioSegmentSeconds = positiveInteger(value, option, 10); break;
      case "--sample-interval-seconds": options.sampleIntervalSeconds = positiveInteger(value, option, 5); break;
      case "--startup-timeout-seconds": options.startupTimeoutSeconds = positiveInteger(value, option, 15); break;
      case "--memory-warmup-seconds": options.memoryWarmupSeconds = positiveInteger(value, option, 0); break;
      case "--synthetic-event-interval-ms": options.syntheticEventIntervalMilliseconds = positiveInteger(value, option, 250); break;
      default: throw new Error(`unknown option ${option}`);
    }
  }

  for (const key of ["meetilyExe", "audio", "ffplay", "cacheDirectory", "output", "assistantCommit", "meetilyCommit"]) {
    if (!options[key]) throw new Error(`required option is missing: ${key}`);
  }
  if (!/^[0-9a-f]{40}$/.test(options.assistantCommit)) {
    throw new Error("assistant commit must be a full Git hash");
  }
  if (!/^[0-9a-f]{40}$/.test(options.meetilyCommit)) {
    throw new Error("Meetily commit must be a full Git hash");
  }
  if (!new Set(["en", "ja"]).has(options.language)) {
    throw new Error("language must be en or ja");
  }
  for (const port of [options.backendPort, options.cdpPort]) {
    if (port > 65535) throw new Error("ports must be <= 65535");
  }
  if (options.backendPort === options.cdpPort) {
    throw new Error("backend and CDP ports must differ");
  }
  return options;
}

export function percentile(values, quantile) {
  assert(values.length > 0, "percentile requires observations");
  assert(quantile >= 0 && quantile <= 1, "quantile must be bounded");
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * quantile))];
}

export function assertPrivacySafeEvidence(value, key = "root") {
  if (Array.isArray(value)) {
    value.forEach((item) => assertPrivacySafeEvidence(item, key));
    return;
  }
  if (value && typeof value === "object") {
    for (const [childKey, childValue] of Object.entries(value)) {
      if (/(?:session.?id|meeting.?title|capability|token|transcript|raw.?exception|local.?path)/i.test(childKey)) {
        throw new Error(`privacy-unsafe evidence key: ${childKey}`);
      }
      assertPrivacySafeEvidence(childValue, childKey);
    }
    return;
  }
  if (typeof value === "string") {
    if (/meeting-intel-[0-9a-f]{32}/i.test(value) || /[a-z]:\\/i.test(value)) {
      throw new Error(`privacy-unsafe evidence value at ${key}`);
    }
  }
}

export function aggregateProcessRoleMemory(rootPid, processes) {
  const roles = Object.fromEntries(PROCESS_ROLES.map((role) => [role, 0]));
  for (const process of processes) {
    const rssBytes = Number(process.rss_bytes);
    if (!Number.isSafeInteger(rssBytes) || rssBytes < 0) {
      throw new Error("invalid process memory sample");
    }
    const name = String(process.name ?? "").toLowerCase().replace(/\.exe$/, "");
    let role = "other";
    if (Number(process.pid) === rootPid) role = "candidate";
    else if (name === "msedgewebview2") role = "webview";
    else if (name.startsWith("meeting-intelligence-backend")) role = "backend";
    roles[role] += rssBytes;
  }
  return roles;
}

export function summarizeMemorySamples(samples) {
  assert(samples.length > 0, "memory summary requires observations");
  const totalSamples = samples.map((sample) => sample.rss_bytes);
  const byRole = {};
  for (const role of PROCESS_ROLES) {
    const values = samples.map((sample) => sample.roles[role] ?? 0);
    byRole[role] = {
      baseline_rss_bytes: values[0],
      maximum_rss_bytes: Math.max(...values),
      rss_growth_bytes: Math.max(0, Math.max(...values) - values[0]),
    };
  }
  return {
    baseline_rss_bytes: totalSamples[0],
    maximum_rss_bytes: Math.max(...totalSamples),
    rss_growth_bytes: Math.max(0, Math.max(...totalSamples) - totalSamples[0]),
    by_role: byRole,
  };
}

export function evaluateSoakAcceptance(evidence) {
  const failures = [];
  const require = (condition, code) => {
    if (!condition) failures.push(code);
  };
  require(evidence.run.completed_full_duration, "duration_incomplete");
  require(evidence.run.recording_stopped, "recording_not_stopped");
  require(evidence.run.recording_drain_completed, "recording_audio_not_drained");
  require(evidence.run.dispatcher_flush_completed, "dispatcher_not_flushed");
  require(evidence.run.session_cleanup_confirmed, "session_cleanup_unconfirmed");
  require(evidence.run.candidate_process_tree_stopped, "process_tree_not_stopped");
  require(evidence.run.cdp_reconnects <= MAXIMUM_CDP_RECONNECTS, "cdp_reconnect_limit_exceeded");
  require(!evidence.run.stale_ask_now_during_recovery, "stale_ask_now_during_recovery");
  require(evidence.run.stop_elapsed_seconds <= MAXIMUM_STOP_SECONDS, "stop_deadline_exceeded");
  require(evidence.transport.dropped === 0, "transport_events_dropped");
  require(evidence.transport.rejected === 0, "transport_events_rejected");
  require(evidence.performance.recovery_max_seconds < MAXIMUM_RECOVERY_SECONDS, "recovery_limit_exceeded");
  require(evidence.performance.rss_growth_bytes < MAXIMUM_RSS_GROWTH_BYTES, "rss_growth_limit_exceeded");
  require(evidence.performance.cache_bytes < MAXIMUM_CACHE_BYTES, "cache_limit_exceeded");
  if (evidence.run.synthetic_transport_events > 0) {
    if (evidence.performance.transport_to_panel_observations === 0) {
      failures.push("transport_to_panel_observations_missing");
    } else {
      require(
        evidence.performance.transport_to_panel_p95_ms < MAXIMUM_TRANSPORT_TO_PANEL_P95_MS,
        "transport_to_panel_p95_exceeded",
      );
    }
  }
  require(
    evidence.performance.maximum_mounted_history_rows <= MAXIMUM_MOUNTED_HISTORY_ROWS,
    "mounted_history_row_limit_exceeded",
  );
  if (evidence.run.requested_duration_seconds >= FOUR_HOUR_GATE_SECONDS) {
    require(
      evidence.run.memory_warmup_seconds >= MINIMUM_FOUR_HOUR_MEMORY_WARMUP_SECONDS,
      "memory_warmup_minimum_not_met",
    );
    require(
      evidence.transport.accepted_since_memory_baseline >= MINIMUM_GATE_ACCEPTED_EVENTS,
      "accepted_event_minimum_not_met",
    );
    require(evidence.run.clean_backend_crashes > 0, "clean_crash_gate_not_exercised");
    require(evidence.run.corrupt_cache_crashes > 0, "corrupt_crash_gate_not_exercised");
    require(evidence.run.occupied_port_recovery, "occupied_port_gate_not_exercised");
    require(evidence.run.corrupt_cache_quarantined, "corrupt_cache_gate_not_exercised");
  }
  return {
    status: failures.length === 0 ? "pass" : "fail",
    failures,
    thresholds: {
      four_hour_minimum_accepted_events_after_memory_baseline: MINIMUM_GATE_ACCEPTED_EVENTS,
      four_hour_minimum_memory_warmup_seconds: MINIMUM_FOUR_HOUR_MEMORY_WARMUP_SECONDS,
      maximum_rss_growth_bytes_exclusive: MAXIMUM_RSS_GROWTH_BYTES,
      maximum_cache_bytes_exclusive: MAXIMUM_CACHE_BYTES,
      maximum_recovery_seconds_exclusive: MAXIMUM_RECOVERY_SECONDS,
      maximum_stop_seconds_inclusive: MAXIMUM_STOP_SECONDS,
      maximum_transport_to_panel_p95_ms_exclusive: MAXIMUM_TRANSPORT_TO_PANEL_P95_MS,
      maximum_mounted_history_rows_inclusive: MAXIMUM_MOUNTED_HISTORY_ROWS,
      maximum_cdp_reconnects_inclusive: MAXIMUM_CDP_RECONNECTS,
    },
  };
}

export function panelContextRestored(beforeFault, restored) {
  return beforeFault.askVisible ? restored.askVisible : restored.topicVisible;
}

export function panelContextAvailable(state, requireAsk) {
  return requireAsk ? state.askVisible : state.askVisible || state.topicVisible;
}

export function panelTransportReady(sidecar, panel) {
  return sidecar?.phase === "ready"
    && (panel?.connectionStatus === "connected" || panel?.connectionStatus === "recovered");
}

export function nextAudioOffset(currentSeconds, segmentSeconds, durationSeconds) {
  assert(Number.isFinite(currentSeconds) && currentSeconds >= 0, "audio offset must be nonnegative");
  assert(Number.isFinite(segmentSeconds) && segmentSeconds > 0, "audio segment must be positive");
  assert(Number.isFinite(durationSeconds) && durationSeconds > 0, "audio duration must be positive");
  return (currentSeconds + segmentSeconds) % durationSeconds;
}

export function buildSoakEvidence(input) {
  assert(
    typeof input.recordingDrainCompleted === "boolean",
    "recording drain completion is required",
  );
  const memory = summarizeMemorySamples(input.memorySamples);
  const recoverySeconds = input.recoverySeconds.length > 0 ? input.recoverySeconds : [0];
  const transportToPanelLatencies = input.transportToPanelLatencyMilliseconds ?? [];
  const enqueueLatencies = input.enqueueLatencyMilliseconds ?? [];
  const projectionLatencies = input.projectionLatencyMilliseconds ?? [];
  const maximumMountedHistoryRows = Math.max(
    ...input.uiSamples.map((sample) => sample.mounted_history_rows),
  );
  const maximumDomElements = Math.max(...input.uiSamples.map((sample) => sample.dom_elements));
  const evidence = {
    schema_version: 6,
    classification: "aggregate-only synthetic packaged process/audio soak evidence",
    created_at_utc: new Date().toISOString(),
    candidate: {
      assistant_commit: input.assistantCommit,
      meetily_commit: input.meetilyCommit,
      meetily_exe_sha256: input.candidateSha256,
    },
    run: {
      requested_duration_seconds: input.requestedDurationSeconds,
      observed_duration_seconds: Number(input.observedDurationSeconds.toFixed(3)),
      completed_full_duration: input.observedDurationSeconds >= input.requestedDurationSeconds,
      language: input.language,
      cloud_runtime_used: false,
      audio_playbacks: input.audioPlaybacks,
      synthetic_transport_events: input.syntheticTransportEvents ?? 0,
      backend_crashes_skipped: input.backendCrashesSkipped ?? false,
      pause_resume_cycles: input.pauseResumeCycles,
      clean_backend_crashes: input.cleanBackendCrashes,
      corrupt_cache_crashes: input.corruptCacheCrashes,
      unplanned_recoveries: input.unplannedRecoveries ?? 0,
      occupied_port_recovery: input.occupiedPortRecovery,
      corrupt_cache_quarantined: input.corruptCacheQuarantined,
      stale_ask_now_during_recovery: input.staleAskNowDuringRecovery,
      concurrent_stop_calls: input.concurrentStopCalls,
      successful_stop_calls: input.successfulStopCalls,
      stop_elapsed_seconds: Number(input.stopElapsedSeconds.toFixed(3)),
      recording_stopped: input.recordingStopped,
      recording_drain_completed: input.recordingDrainCompleted,
      dispatcher_flush_completed: input.dispatcherFlushCompleted,
      session_cleanup_confirmed: input.sessionCleanupConfirmed,
      candidate_process_tree_stopped: input.candidateProcessTreeStopped,
      cdp_reconnects: input.cdpReconnects ?? 0,
      memory_warmup_seconds: input.memoryWarmupSeconds ?? 0,
    },
    transport: input.transport,
    performance: {
      recovery_p95_seconds: Number(percentile(recoverySeconds, 0.95).toFixed(3)),
      recovery_max_seconds: Number(Math.max(...recoverySeconds).toFixed(3)),
      baseline_rss_bytes: memory.baseline_rss_bytes,
      maximum_rss_bytes: memory.maximum_rss_bytes,
      rss_growth_bytes: memory.rss_growth_bytes,
      process_role_rss: memory.by_role,
      cache_bytes: input.cacheBytes,
      maximum_mounted_history_rows: maximumMountedHistoryRows,
      maximum_dom_elements: maximumDomElements,
      maximum_stt_queue_depth: input.maximumSttQueueDepth ?? 0,
      maximum_audio_pipeline_queue_depth: input.maximumAudioPipelineQueueDepth ?? 0,
      recording_queue_peak: input.recordingQueuePeak ?? 0,
      recording_drain_ms: input.recordingDrainMilliseconds ?? 0,
      recording_final_checkpoint_ms: input.recordingFinalCheckpointMilliseconds ?? 0,
      recording_merge_ms: input.recordingMergeMilliseconds ?? 0,
      recording_checkpoint_cleanup_ms: input.recordingCheckpointCleanupMilliseconds ?? 0,
      recording_finalize_metadata_ms: input.recordingFinalizeMetadataMilliseconds ?? 0,
      transport_to_panel_observations: transportToPanelLatencies.length,
      transport_to_panel_skipped_observations: input.transportToPanelSkippedObservations ?? 0,
      transport_to_panel_p95_ms: transportToPanelLatencies.length > 0
        ? Number(percentile(transportToPanelLatencies, 0.95).toFixed(3))
        : null,
      enqueue_p95_ms: enqueueLatencies.length > 0
        ? Number(percentile(enqueueLatencies, 0.95).toFixed(3))
        : null,
      projection_p95_ms: projectionLatencies.length > 0
        ? Number(percentile(projectionLatencies, 0.95).toFixed(3))
        : null,
    },
    privacy_assertions: {
      contains_content_text: false,
      contains_human_metadata: false,
      contains_live_identity: false,
      contains_auth_secret: false,
      contains_development_location: false,
      contains_unbounded_error: false,
    },
    limitations: input.limitations,
  };
  evidence.acceptance = evaluateSoakAcceptance(evidence);
  assertPrivacySafeEvidence(evidence);
  return evidence;
}

function delay(milliseconds) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

function emitProgress(phase, details = {}) {
  console.log(JSON.stringify({ phase, ...details }));
}

function requireSoak(condition, code) {
  if (!condition) throw new Error(code);
}

async function sampleLiveUi(client) {
  const sample = await client.evaluate(`(() => ({
    mounted_history_rows: document.querySelectorAll('[id^="segment-"]').length,
    dom_elements: document.querySelectorAll('*').length,
  }))()`);
  for (const key of ["mounted_history_rows", "dom_elements"]) {
    if (!Number.isSafeInteger(sample?.[key]) || sample[key] < 0) {
      throw new Error("invalid_ui_sample");
    }
  }
  return sample;
}

async function waitFor(probe, timeoutMilliseconds, code) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const value = await probe();
    if (value) return value;
    await delay(250);
  }
  throw new Error(code);
}

function execFileAsync(file, args, options = {}) {
  return new Promise((resolveExec, rejectExec) => {
    execFile(file, args, {
      timeout: 15_000,
      maxBuffer: 1024 * 1024,
      ...options,
      windowsHide: true,
    }, (error, stdout) => {
      if (error) rejectExec(new Error("bounded helper command failed"));
      else resolveExec(stdout);
    });
  });
}

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timeout);
      if (message.error) pending.reject(new Error("CDP command failed"));
      else pending.resolve(message.result);
    });
    socket.addEventListener("close", () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timeout);
        pending.reject(new Error("cdp_connection_closed"));
      }
      this.pending.clear();
    });
  }

  static async connect(port, timeoutMilliseconds) {
    const deadline = Date.now() + timeoutMilliseconds;
    let bridgeFailure = "application_page_unavailable";
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}/json`, {
          signal: AbortSignal.timeout(1000),
        });
        const targets = await response.json();
        for (const target of targets.filter((candidate) => candidate.type === "page")) {
          let socket = null;
          try {
            socket = new WebSocket(target.webSocketDebuggerUrl);
            await new Promise((resolveSocket, rejectSocket) => {
              const timeout = setTimeout(
                () => rejectSocket(new Error("cdp_connection_timeout")),
                2_000,
              );
              socket.addEventListener("open", () => {
                clearTimeout(timeout);
                resolveSocket();
              }, { once: true });
              socket.addEventListener("error", () => {
                clearTimeout(timeout);
                rejectSocket(new Error("cdp_connection_failed"));
              }, { once: true });
            });
            const client = new CdpClient(socket);
            bridgeFailure = "cdp_page_connected";
            await client.send("Runtime.enable");
            bridgeFailure = "installer_unavailable";
            const ready = await waitFor(async () => {
              try {
                return await client.evaluate(`(() => {
                  const install = self.__MEETILY_SOAK_INSTALL__;
                  if (typeof install !== "function") return false;
                  self.__MEETILY_SOAK_PRELOAD__ = "meeting-intelligence-soak-v1";
                  self.__MEETILY_SOAK_CLEANUP__ = install();
                  return typeof self.__MEETILY_SOAK_INVOKE__ === "function";
                })()`);
              } catch {
                return false;
              }
            }, 10_000, "soak_bridge_timeout").catch(() => false);
            if (!ready) {
              const probe = await client.evaluate(`(() => ({
                document_ready: document.readyState,
                installer_type: typeof self.__MEETILY_SOAK_INSTALL__,
                marker_type: typeof self.__MEETILY_SOAK_PRELOAD__,
                invoke_type: typeof self.__MEETILY_SOAK_INVOKE__,
                location_protocol: location.protocol,
                script_assets: Array.from(document.scripts)
                  .map((script) => script.src.split("/").at(-1))
                  .filter(Boolean)
                  .slice(0, 20),
              }))()`).catch(() => ({
                document_ready: "unavailable",
                installer_type: "unavailable",
                marker_type: "unavailable",
                invoke_type: "unavailable",
                location_protocol: "unavailable",
                script_assets: [],
              }));
              emitProgress("cdp_page_probe", probe);
            }
            bridgeFailure = "soak_bridge_unavailable";
            if (ready) return client;
            client.close();
          } catch {
            socket?.close();
          }
        }
      } catch {
        // The webview and its CDP endpoint may not exist yet.
      }
      await delay(250);
    }
    throw new Error(`cdp_startup_timeout:${bridgeFailure}`);
  }

  send(method, params = {}) {
    return new Promise((resolveCommand, rejectCommand) => {
      const id = ++this.sequence;
      const timeout = setTimeout(() => {
        if (!this.pending.delete(id)) return;
        rejectCommand(new Error("cdp_command_timeout"));
      }, CDP_COMMAND_TIMEOUT_MS);
      this.pending.set(id, { resolve: resolveCommand, reject: rejectCommand, timeout });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const response = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails || response.result?.subtype === "error") {
      throw new Error("webview_evaluation_failed");
    }
    return response.result?.value;
  }

  async invoke(command, args = {}) {
    const result = await this.evaluate(`(async () => {
      try {
        const value = await window.__MEETILY_SOAK_INVOKE__(
          ${JSON.stringify(command)},
          ${JSON.stringify(args)}
        );
        return { ok: true, value };
      } catch (error) {
        const message = [
          String(error),
          error?.message,
          error?.error,
          error?.cause,
          JSON.stringify(error),
        ].filter(Boolean).join(" ").toLowerCase();
        const category = message.includes("missing required key") || message.includes("invalid args")
          ? "invalid_arguments"
          : message.includes("poison") ? "poisoned_state"
            : message.includes("not allowed") || message.includes("denied") ? "acl_denied"
              : message.includes("not found") || message.includes("unknown command")
                ? "command_unregistered" : "command_rejected";
        return { ok: false, category };
      }
    })()`);
    if (result?.ok === true) return result.value;
    const boundedCommand = /^[a-z0-9:_|-]+$/i.test(command) ? command : "unknown";
    if (result?.ok !== false) {
      throw new Error(`tauri_invoke_protocol_failed:${boundedCommand}`);
    }
    const category = /^[a-z_]+$/.test(result?.category) ? result.category : "command_rejected";
    throw new Error(`tauri_invoke_failed:${boundedCommand}:${category}`);
  }

  close() {
    this.socket.close();
  }
}

export function isRecoverableCdpError(error) {
  const code = error instanceof Error ? error.message : "";
  return code === "cdp_command_timeout"
    || code === "cdp_connection_closed"
    || code === "cdp_connection_failed";
}

export function isRetrySafeSoakCommand(command) {
  return RETRY_SAFE_SOAK_COMMANDS.has(command);
}

export class RecoveringCdpClient {
  constructor(port, client, connectClient = CdpClient.connect) {
    this.port = port;
    this.client = client;
    this.connectClient = connectClient;
    this.generation = 0;
    this.reconnectCount = 0;
    this.reconnectPromise = null;
    this.closed = false;
  }

  static async connect(port, timeoutMilliseconds) {
    return new RecoveringCdpClient(
      port,
      await CdpClient.connect(port, timeoutMilliseconds),
    );
  }

  async recover(expectedGeneration) {
    if (this.closed) throw new Error("cdp_connection_closed");
    if (this.generation !== expectedGeneration) return;
    if (this.reconnectCount >= MAXIMUM_CDP_RECONNECTS) {
      throw new Error("cdp_reconnect_budget_exhausted");
    }
    if (!this.reconnectPromise) {
      const staleClient = this.client;
      this.reconnectPromise = (async () => {
        staleClient.close();
        const replacement = await this.connectClient(this.port, CDP_RECOVERY_TIMEOUT_MS);
        if (this.closed) {
          replacement.close();
          throw new Error("cdp_connection_closed");
        }
        this.client = replacement;
        this.generation += 1;
        this.reconnectCount += 1;
        emitProgress("cdp_reconnected", { count: this.reconnectCount });
      })().finally(() => {
        this.reconnectPromise = null;
      });
    }
    await this.reconnectPromise;
  }

  async execute(operation, retryAllowed, operationCode) {
    const generation = this.generation;
    try {
      return await operation(this.client);
    } catch (error) {
      if (!isRecoverableCdpError(error)) throw error;
      await this.recover(generation);
      if (!retryAllowed) {
        throw new Error(`cdp_command_outcome_unknown:${operationCode}`);
      }
      return operation(this.client);
    }
  }

  evaluate(expression) {
    return this.execute(
      (client) => client.evaluate(expression),
      true,
      "evaluate",
    );
  }

  invoke(command, args = {}) {
    return this.execute(
      (client) => client.invoke(command, args),
      isRetrySafeSoakCommand(command),
      /^[a-z0-9:_|-]+$/i.test(command) ? command : "unknown",
    );
  }

  close() {
    this.closed = true;
    this.client.close();
  }
}

async function boundedCleanupInvoke(client, command, args = {}) {
  const operation = client.invoke(command, args)
    .then((value) => ({ completed: true, value }))
    .catch(() => ({ completed: true, value: null }));
  return Promise.race([
    operation,
    delay(5_000).then(() => ({ completed: false, value: null })),
  ]);
}

async function listenBlocker(port) {
  const server = createServer();
  const sockets = new Set();
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    socket.destroy();
  });
  server.soakSockets = sockets;
  await new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(port, "127.0.0.1", resolveListen);
  });
  return server;
}

async function closeServer(server) {
  if (!server) return;
  for (const socket of server.soakSockets ?? []) socket.destroy();
  server.closeAllConnections?.();
  await new Promise((resolveClose, rejectClose) => {
    const timeout = setTimeout(
      () => rejectClose(new Error("port_blocker_close_timeout")),
      5_000,
    );
    server.close(() => {
      clearTimeout(timeout);
      resolveClose();
    });
  });
}

async function terminateChild(child) {
  if (!child?.pid || child.exitCode !== null) return;
  child.kill();
  await waitForExit(child, 5_000);
}

async function playAudio(ffplay, audio, systemDevice, segmentSeconds) {
  const child = spawn(ffplay, ["-nodisp", "-autoexit", "-t", String(segmentSeconds), "-loglevel", "error", audio], {
    env: {
      ...process.env,
      SDL_AUDIODRIVER: "wasapi",
      SDL_AUDIO_DEVICE_NAME: systemDevice,
    },
    stdio: "ignore",
    windowsHide: true,
  });
  await delay(100);
  if (!child.pid) throw new Error("audio_playback_start_failed");
  await delay(segmentSeconds * 1000);
  await terminateChild(child);
}

async function probeAudioDuration(ffplay, audio) {
  const executable = join(dirname(ffplay), process.platform === "win32" ? "ffprobe.exe" : "ffprobe");
  const input = await stat(executable).catch(() => null);
  if (!input?.isFile()) throw new Error("ffprobe_unavailable");
  const output = String(await execFileAsync(executable, [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    audio,
  ])).trim();
  const durationSeconds = Number(output);
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    throw new Error("audio_duration_invalid");
  }
  return durationSeconds;
}

class AudioLoop {
  constructor(options) {
    this.options = options;
    this.stopped = false;
    this.playbacks = 0;
    this.current = null;
    this.wake = null;
    this.nextOffsetSeconds = options.initialAudioOffsetSeconds;
  }

  async run() {
    while (!this.stopped) {
      const child = spawn(
        this.options.ffplay,
        [
          "-ss", String(this.nextOffsetSeconds),
          "-nodisp", "-autoexit",
          "-t", String(this.options.audioSegmentSeconds),
          "-loglevel", "error",
          this.options.audio,
        ],
        {
          env: {
            ...process.env,
            SDL_AUDIODRIVER: "wasapi",
            SDL_AUDIO_DEVICE_NAME: this.options.systemDevice,
          },
          stdio: "ignore",
          windowsHide: true,
        },
      );
      this.current = child;
      await delay(100);
      if (!child.pid) throw new Error("audio_loop_start_failed");
      if (!this.stopped) {
        await Promise.race([
          delay(this.options.audioSegmentSeconds * 1000),
          new Promise((resolveWake) => { this.wake = resolveWake; }),
        ]);
      }
      this.wake = null;
      await terminateChild(child);
      this.current = null;
      if (!this.stopped) {
        this.playbacks += 1;
        this.nextOffsetSeconds = nextAudioOffset(
          this.nextOffsetSeconds,
          this.options.audioSegmentSeconds,
          this.options.audioDurationSeconds,
        );
      }
    }
  }

  async stop() {
    if (this.stopped) return;
    this.stopped = true;
    const wake = this.wake;
    this.wake = null;
    if (wake) wake();
  }
}

async function backendProcess(port) {
  const script = [
    `$connection=Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue`,
    "if(-not $connection){exit 0}",
    "$process=Get-CimInstance Win32_Process -Filter \"ProcessId=$($connection.OwningProcess)\"",
    "[ordered]@{pid=$process.ProcessId;name=[IO.Path]::GetFileName($process.ExecutablePath)}|ConvertTo-Json -Compress",
  ].join(";");
  const output = String(await execFileAsync("powershell.exe", ["-NoLogo", "-NoProfile", "-Command", script])).trim();
  if (!output) return null;
  const value = JSON.parse(output);
  if (!/^meeting-intelligence-backend(?:-[a-z0-9_-]+)?\.exe$/i.test(value.name)) {
    throw new Error("backend_port_owned_by_unexpected_process");
  }
  return value;
}

async function waitForBackendStopped(port, timeoutMilliseconds = 15_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let absentSince = null;
  while (Date.now() < deadline) {
    if (await backendProcess(port)) {
      absentSince = null;
    } else {
      absentSince ??= Date.now();
      if (Date.now() - absentSince >= 3_000) return true;
    }
    await delay(250);
  }
  return false;
}

async function terminateBackend(port) {
  const process = await backendProcess(port);
  if (!process) throw new Error("managed_backend_listener_missing");
  await execFileAsync("taskkill.exe", ["/PID", String(process.pid), "/T", "/F"]);
  return process.pid;
}

async function processTreeMemory(rootPid) {
  const script = [
    "$all=@(Get-CimInstance Win32_Process)",
    `$ids=New-Object 'System.Collections.Generic.HashSet[int]';[void]$ids.Add(${rootPid})`,
    "do{$changed=$false;foreach($p in $all){if($ids.Contains([int]$p.ParentProcessId)-and $ids.Add([int]$p.ProcessId)){$changed=$true}}}while($changed)",
    "$sum=0;$rows=@();foreach($id in $ids){$p=Get-Process -Id $id -ErrorAction SilentlyContinue;if($p){$rss=[int64]$p.WorkingSet64;$sum+=$rss;$rows+=[ordered]@{pid=[int]$id;name=$p.ProcessName;rss_bytes=$rss}}}",
    "[ordered]@{rss_bytes=$sum;process_count=$rows.Count;processes=@($rows)}|ConvertTo-Json -Compress -Depth 4",
  ].join(";");
  const sample = JSON.parse(String(await execFileAsync("powershell.exe", ["-NoLogo", "-NoProfile", "-Command", script])));
  return {
    rss_bytes: sample.rss_bytes,
    process_count: sample.process_count,
    roles: aggregateProcessRoleMemory(rootPid, sample.processes),
  };
}

async function sha256(file) {
  return createHash("sha256").update(await readFile(file)).digest("hex").toUpperCase();
}

async function corruptActiveSnapshotAndTerminate(
  cacheDirectory,
  opaqueIdentity,
  backendPort,
) {
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(join(cacheDirectory, "live-sessions.sqlite3"));
  try {
    database.exec("PRAGMA busy_timeout = 5000");
    // Hold the writer lock before killing the backend. Otherwise the one-second
    // production restart can reopen SQLite before fault injection commits.
    database.exec("BEGIN IMMEDIATE");
    const result = database.prepare("UPDATE live_sessions SET state_json = ? WHERE session_id = ?")
      .run("{invalid-json", opaqueIdentity);
    if (result.changes !== 1) throw new Error("active_cache_row_missing");
    await terminateBackend(backendPort);
    database.exec("COMMIT");
  } catch (error) {
    try {
      database.exec("ROLLBACK");
    } catch {
      // The transaction may already be closed; preserve the original failure.
    }
    throw error;
  } finally {
    database.close();
  }
}

async function quarantinedCount(cacheDirectory, opaqueIdentity) {
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(join(cacheDirectory, "live-sessions.sqlite3"), { readOnly: true });
  try {
    return Number(database.prepare(
      "SELECT COUNT(*) AS count FROM quarantined_sessions WHERE session_id = ?",
    ).get(opaqueIdentity).count);
  } finally {
    database.close();
  }
}

async function activeSessionCacheBytes(cacheDirectory, opaqueIdentity) {
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(join(cacheDirectory, "live-sessions.sqlite3"), { readOnly: true });
  try {
    const row = database.prepare(`
      SELECT COALESCE(LENGTH(CAST(state_json AS BLOB)), 0) AS state_bytes,
        (SELECT COALESCE(SUM(LENGTH(CAST(event_json AS BLOB))), 0)
         FROM transcript_events WHERE session_id = ?) AS event_bytes
      FROM live_sessions WHERE session_id = ?
    `).get(opaqueIdentity, opaqueIdentity);
    return row ? Number(row.state_bytes) + Number(row.event_bytes) : 0;
  } finally {
    database.close();
  }
}

async function waitForSidecar(client, phases, timeoutMilliseconds) {
  return waitFor(async () => {
    try {
      const status = await client.invoke("get_meeting_intelligence_backend_status");
      if (!SAFE_PHASES.has(status.phase)) throw new Error("invalid_sidecar_phase");
      return phases.has(status.phase) ? status : null;
    } catch {
      return null;
    }
  }, timeoutMilliseconds, "sidecar_state_timeout");
}

async function panelQuestionState(client) {
  return client.evaluate(`(() => {
    const panel = document.querySelector('aside[aria-label]');
    return {
      askVisible: Boolean(panel?.querySelector('#ask-now-heading')),
      topicVisible: Boolean(panel?.querySelector('#intelligence-topic-heading')),
      liveStatusVisible: Boolean(panel?.querySelector('[role="status"][aria-live="polite"]')),
      connectionStatus: panel?.getAttribute('data-intelligence-status') ?? 'missing',
      stateVersion: Number(panel?.getAttribute('data-intelligence-version') ?? -1),
      lastEventSequence: Number(panel?.getAttribute('data-intelligence-last-event-seq') ?? -1)
    };
  })()`);
}

async function emitMeasuredSyntheticUpdate(client, language) {
  const [sidecarBefore, panelBefore] = await Promise.all([
    client.invoke("get_meeting_intelligence_backend_status").catch(() => null),
    panelQuestionState(client).catch(() => null),
  ]);
  const shouldMeasure = panelTransportReady(sidecarBefore, panelBefore);
  const started = performance.now();
  const sequenceId = await client.invoke("emit_meeting_intelligence_soak_update", { language });
  const enqueued = performance.now();
  if (!Number.isSafeInteger(sequenceId) || sequenceId < 0) {
    throw new Error("invalid_synthetic_sequence_id");
  }
  if (!shouldMeasure) {
    return {
      sequenceId,
      latencyMilliseconds: null,
      enqueueMilliseconds: null,
      projectionMilliseconds: null,
    };
  }

  const deadline = performance.now() + PANEL_PROJECTION_TIMEOUT_MS;
  while (performance.now() < deadline) {
    const panel = await panelQuestionState(client).catch(() => null);
    if (panel?.lastEventSequence >= sequenceId) {
      return {
        sequenceId,
        latencyMilliseconds: performance.now() - started,
        enqueueMilliseconds: enqueued - started,
        projectionMilliseconds: performance.now() - enqueued,
      };
    }
    await delay(PANEL_PROJECTION_POLL_MS);
  }

  const [sidecarAfter, panelAfter] = await Promise.all([
    client.invoke("get_meeting_intelligence_backend_status").catch(() => null),
    panelQuestionState(client).catch(() => null),
  ]);
  if (panelTransportReady(sidecarAfter, panelAfter)) {
    throw new Error("transport_to_panel_projection_timeout");
  }
  return {
    sequenceId,
    latencyMilliseconds: null,
    enqueueMilliseconds: null,
    projectionMilliseconds: null,
  };
}

async function recoveryDiagnostics(client, panelState) {
  const [lifecycle, dispatcher] = await Promise.all([
    client.invoke("get_meeting_intelligence_backend_status").catch(() => null),
    client.invoke("get_meeting_intelligence_diagnostics").catch(() => null),
  ]);
  return {
    connection_status: panelState?.connectionStatus ?? "unavailable",
    state_version: panelState?.stateVersion ?? -1,
    last_event_sequence: panelState?.lastEventSequence ?? -1,
    ask_visible: panelState?.askVisible ?? false,
    topic_visible: panelState?.topicVisible ?? false,
    sidecar_phase: lifecycle?.phase ?? "unknown",
    sidecar_retry_count: Number(lifecycle?.retryCount ?? -1),
    dispatcher_queue_depth: Number(dispatcher?.currentQueueDepth ?? -1),
    dispatcher_retried: Number(dispatcher?.retried ?? -1),
  };
}

async function faultBackend({
  client,
  options,
  cacheDirectory,
  corrupt,
  opaqueIdentity,
  requireAsk = false,
}) {
  let beforeFault = await panelQuestionState(client);
  let preFaultRecoverySeconds = 0;
  if (!panelContextAvailable(beforeFault, requireAsk)) {
    const waitStarted = performance.now();
    emitProgress(
      "context_absent_before_fault",
      await recoveryDiagnostics(client, beforeFault),
    );
    beforeFault = await waitFor(async () => {
      try {
        const candidate = await panelQuestionState(client);
        return panelContextAvailable(candidate, requireAsk) ? candidate : null;
      } catch {
        return null;
      }
    }, 30_000, requireAsk
      ? "ask_now_absent_before_fault"
      : "intelligence_context_absent_before_fault");
    preFaultRecoverySeconds = (performance.now() - waitStarted) / 1000;
  }
  if (requireAsk) {
    requireSoak(beforeFault.askVisible, "ask_now_absent_before_fault");
  } else {
    requireSoak(
      beforeFault.askVisible || beforeFault.topicVisible,
      "intelligence_context_absent_before_fault",
    );
  }
  const started = performance.now();
  if (corrupt) {
    await corruptActiveSnapshotAndTerminate(
      cacheDirectory,
      opaqueIdentity,
      options.backendPort,
    ).catch(() => {
      throw new Error("corrupt_cache_injection_failed");
    });
  } else {
    await terminateBackend(options.backendPort);
  }
  await waitForSidecar(
    client,
    new Set(["starting", "degraded", "restarting", "unavailable"]),
    30_000,
  );

  const recovering = await waitFor(async () => {
    try {
      const state = await panelQuestionState(client);
      return state.liveStatusVisible && !state.askVisible ? state : null;
    } catch {
      return null;
    }
  }, 30_000, "recovery_ui_timeout");
  const ready = await waitForSidecar(client, new Set(["ready"]), 45_000);
  const recording = await client.invoke("get_recording_state");
  requireSoak(recording.is_recording, "recording_stopped_during_recovery");
  requireSoak(ready.managed, "recovered_backend_not_managed");
  requireSoak(ready.authEnabled, "recovered_backend_not_authenticated");
  try {
    await waitFor(async () => {
      try {
        const restored = await panelQuestionState(client);
        return panelContextRestored(beforeFault, restored);
      } catch {
        return false;
      }
    }, 60_000, beforeFault.askVisible
      ? "ask_now_not_restored_after_replay"
      : "resolved_context_not_restored_after_replay");
  } catch (error) {
    const diagnostic = await panelQuestionState(client).catch(() => null);
    emitProgress(
      "context_restore_timeout",
      await recoveryDiagnostics(client, diagnostic),
    );
    throw error;
  }
  return {
    preFaultRecoverySeconds,
    recoverySeconds: (performance.now() - started) / 1000,
    staleAskNow: Boolean(recovering.askVisible),
  };
}

async function writeEvidence(output, evidence) {
  await mkdir(dirname(output), { recursive: true });
  const temporary = `${output}.tmp`;
  await writeFile(temporary, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  await rename(temporary, output);
}

async function waitForExit(child, timeoutMilliseconds) {
  if (child.exitCode !== null) return true;
  return Promise.race([
    new Promise((resolveExit) => child.once("exit", () => resolveExit(true))),
    delay(timeoutMilliseconds).then(() => false),
  ]);
}

export async function runSoak(options) {
  for (const file of [options.meetilyExe, options.audio, options.ffplay]) {
    const input = await stat(file).catch(() => null);
    if (!input?.isFile()) throw new Error("required_soak_input_unavailable");
  }
  if (await stat(options.output).catch(() => null)) {
    throw new Error("refusing to overwrite existing soak evidence");
  }
  const audioDurationSeconds = await probeAudioDuration(options.ffplay, options.audio);
  const routedAudioOptions = {
    ...options,
    audioDurationSeconds,
    initialAudioOffsetSeconds: options.skipOccupiedPort
      ? 0
      : nextAudioOffset(0, options.audioSegmentSeconds, audioDurationSeconds),
  };

  const temporaryRoot = await mkdtemp(join(tmpdir(), "meetily-intelligence-soak-"));
  const cacheDirectory = options.cacheDirectory;
  const savePath = join(temporaryRoot, "recording.wav");
  await mkdir(cacheDirectory, { recursive: true }).catch(() => {
    throw new Error("cache_directory_unavailable");
  });
  let blocker = null;
  let app = null;
  let client = null;
  let audioLoop = null;
  let audioLoopPromise = null;
  let audioLoopFailed = false;

  const metrics = {
    recoverySeconds: [],
    pauseResumeCycles: 0,
    cleanBackendCrashes: 0,
    corruptCacheCrashes: 0,
    unplannedRecoveries: 0,
    occupiedPortRecovery: options.skipOccupiedPort ?? false,
    corruptCacheQuarantined: options.skipCorruptCache ?? false,
    staleAskNowDuringRecovery: false,
    memorySamples: [],
    maximumSttQueueDepth: 0,
    maximumAudioPipelineQueueDepth: 0,
    uiSamples: [],
    syntheticTransportEvents: 0,
    transportToPanelLatencyMilliseconds: [],
    enqueueLatencyMilliseconds: [],
    projectionLatencyMilliseconds: [],
    transportToPanelSkippedObservations: 0,
  };

  function recordFaultRecovery(result) {
    if (result.preFaultRecoverySeconds > 0) {
      metrics.recoverySeconds.push(result.preFaultRecoverySeconds);
      metrics.unplannedRecoveries += 1;
    }
    metrics.recoverySeconds.push(result.recoverySeconds);
  }

  try {
    if (!options.skipOccupiedPort) blocker = await listenBlocker(options.backendPort);
    emitProgress("starting", { occupied_port_injected: Boolean(blocker) });
    app = spawn(options.meetilyExe, [], {
      cwd: dirname(options.meetilyExe),
      env: {
        ...process.env,
        MEETING_INTELLIGENCE_MANAGE_SIDECAR: "1",
        MEETING_COPILOT_URL: `http://127.0.0.1:${options.backendPort}`,
        MEETING_INTELLIGENCE_BACKEND_PORT: String(options.backendPort),
        MEETING_INTELLIGENCE_CACHE_DIR: cacheDirectory,
        MEETING_INTELLIGENCE_INTERNAL_SOAK: "1",
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${options.cdpPort}`,
      },
      stdio: "ignore",
      windowsHide: false,
    });
    app.once("error", () => undefined);
    client = await RecoveringCdpClient.connect(
      options.cdpPort,
      options.startupTimeoutSeconds * 1000,
    );
    await client.invoke("set_language_preference", { language: options.language });

    if (!options.skipOccupiedPort) {
      await waitForSidecar(client, new Set(["unavailable"]), 45_000);
      emitProgress("backend_unavailable", { recording_independence_check: true });
    }
    await client.invoke("start_recording_with_devices_and_meeting", {
      micDeviceName: null,
      systemDeviceName: options.systemDevice,
      meetingName: "Internal pilot soak",
    });
    const initialState = await client.invoke("get_recording_state");
    requireSoak(initialState.is_recording, "recording_did_not_start");
    requireSoak(
      /^meeting-intel-[0-9a-f]{32}$/.test(initialState.recording_session_id),
      "opaque_recording_identity_missing",
    );
    const opaqueIdentity = initialState.recording_session_id;

    if (!options.skipOccupiedPort) {
      emitProgress("occupied_port_audio", { playback_budget_seconds: options.audioSegmentSeconds });
      await playAudio(options.ffplay, options.audio, options.systemDevice, options.audioSegmentSeconds);
      const unavailableState = await client.invoke("get_recording_state");
      requireSoak(unavailableState.is_recording, "recording_stopped_while_backend_unavailable");
      await closeServer(blocker);
      blocker = null;
      requireSoak(
        await client.invoke("retry_meeting_intelligence_backend"),
        "backend_manual_retry_rejected",
      );
      await waitForSidecar(client, new Set(["ready"]), 45_000);
      await waitFor(async () => {
        const diagnostics = await client.invoke("get_meeting_intelligence_diagnostics");
        return diagnostics.accepted > 0 ? diagnostics : null;
      }, 90_000, "queued_audio_did_not_recover");
      // Keep the real routed-audio path active while waiting for a pain-bearing
      // utterance. A valid small-talk first segment must not deadlock bootstrap.
      audioLoop = new AudioLoop(routedAudioOptions);
      audioLoopPromise = audioLoop.run().catch(() => {
        audioLoopFailed = true;
      });
      try {
        await waitFor(async () => {
          if (audioLoopFailed) throw new Error("audio_loop_failed");
          try {
            return (await panelQuestionState(client)).askVisible;
          } catch {
            return false;
          }
        }, 60_000, "initial_ask_now_not_rendered");
      } catch (error) {
        const diagnostic = await panelQuestionState(client).catch(() => null);
        emitProgress("initial_question_timeout", await recoveryDiagnostics(client, diagnostic));
        throw error;
      }
      metrics.occupiedPortRecovery = true;
      emitProgress("occupied_port_recovered", { queued_audio_applied: true });
    } else {
      await waitForSidecar(client, new Set(["ready"]), 45_000);
    }

    if (!audioLoop) {
      audioLoop = new AudioLoop(routedAudioOptions);
      audioLoopPromise = audioLoop.run().catch(() => {
        audioLoopFailed = true;
      });
    }
    if (options.memoryWarmupSeconds > 0) {
      emitProgress("memory_warmup_started", { seconds: options.memoryWarmupSeconds });
      const warmupDeadline = performance.now() + options.memoryWarmupSeconds * 1000;
      while (performance.now() < warmupDeadline) {
        if (audioLoopFailed) throw new Error("audio_loop_failed");
        await sampleLiveUi(client);
        await delay(Math.min(5_000, Math.max(0, warmupDeadline - performance.now())));
      }
      emitProgress("memory_warmup_completed", { seconds: options.memoryWarmupSeconds });
    }
    const baseline = await processTreeMemory(app.pid);
    const baselineDiagnostics = await client.invoke("get_meeting_intelligence_diagnostics");
    metrics.memorySamples.push(baseline);
    metrics.uiSamples.push(await sampleLiveUi(client));
    const runStarted = performance.now();
    const deadline = runStarted + options.durationSeconds * 1000;
    let nextPause = runStarted + options.pauseIntervalSeconds * 1000;
    let nextSample = runStarted + options.sampleIntervalSeconds * 1000;
    let nextPeriodicCrash = runStarted + options.crashIntervalSeconds * 1000;
    let nextSyntheticEvent = runStarted;
    let initialCrashDone = options.skipBackendCrashes ?? false;
    let corruptCrashDone = (options.skipBackendCrashes || options.skipCorruptCache) ?? false;

    while (performance.now() < deadline) {
      if (audioLoopFailed) throw new Error("audio_loop_failed");
      const now = performance.now();
      if (!options.skipSyntheticEvents && now >= nextSyntheticEvent) {
        const result = await emitMeasuredSyntheticUpdate(client, options.language);
        metrics.syntheticTransportEvents += 1;
        if (result.latencyMilliseconds === null) {
          metrics.transportToPanelSkippedObservations += 1;
        } else {
          metrics.transportToPanelLatencyMilliseconds.push(result.latencyMilliseconds);
          metrics.enqueueLatencyMilliseconds.push(result.enqueueMilliseconds);
          metrics.projectionLatencyMilliseconds.push(result.projectionMilliseconds);
          if (result.latencyMilliseconds >= MAXIMUM_TRANSPORT_TO_PANEL_P95_MS) {
            const diagnostics = await client.invoke("get_meeting_intelligence_diagnostics");
            emitProgress("slow_projection", {
              total_ms: Number(result.latencyMilliseconds.toFixed(3)),
              enqueue_ms: Number(result.enqueueMilliseconds.toFixed(3)),
              projection_ms: Number(result.projectionMilliseconds.toFixed(3)),
              queue_depth: Number(diagnostics.currentQueueDepth ?? -1),
            });
          }
        }
        nextSyntheticEvent = performance.now() + options.syntheticEventIntervalMilliseconds;
      }
      if (!initialCrashDone && now - runStarted >= options.initialCrashSeconds * 1000) {
        const result = await faultBackend({
          client,
          options,
          cacheDirectory,
          corrupt: false,
          opaqueIdentity,
          requireAsk: true,
        });
        recordFaultRecovery(result);
        metrics.staleAskNowDuringRecovery ||= result.staleAskNow;
        metrics.cleanBackendCrashes += 1;
        initialCrashDone = true;
        emitProgress("backend_recovered", {
          fault: "clean_crash",
          recovery_seconds: Number(result.recoverySeconds.toFixed(3)),
        });
      }
      if (!corruptCrashDone && now - runStarted >= options.corruptCrashSeconds * 1000) {
        const result = await faultBackend({ client, options, cacheDirectory, corrupt: true, opaqueIdentity });
        recordFaultRecovery(result);
        metrics.staleAskNowDuringRecovery ||= result.staleAskNow;
        metrics.corruptCacheCrashes += 1;
        await waitFor(
          async () => (await quarantinedCount(cacheDirectory, opaqueIdentity)) > 0,
          15_000,
          "corrupt_cache_not_quarantined",
        );
        metrics.corruptCacheQuarantined = true;
        corruptCrashDone = true;
        emitProgress("backend_recovered", {
          fault: "corrupt_cache",
          recovery_seconds: Number(result.recoverySeconds.toFixed(3)),
        });
      }
      if (!options.skipBackendCrashes && now >= nextPeriodicCrash) {
        const result = await faultBackend({ client, options, cacheDirectory, corrupt: false, opaqueIdentity });
        recordFaultRecovery(result);
        metrics.staleAskNowDuringRecovery ||= result.staleAskNow;
        metrics.cleanBackendCrashes += 1;
        nextPeriodicCrash = performance.now() + options.crashIntervalSeconds * 1000;
        emitProgress("backend_recovered", {
          fault: "periodic_crash",
          recovery_seconds: Number(result.recoverySeconds.toFixed(3)),
        });
      }
      if (now >= nextPause) {
        await client.invoke("pause_recording");
        const paused = await client.invoke("get_recording_state");
        requireSoak(paused.is_paused, "recording_did_not_pause");
        await delay(1000);
        await client.invoke("resume_recording");
        const resumed = await client.invoke("get_recording_state");
        requireSoak(!resumed.is_paused, "recording_did_not_resume");
        requireSoak(resumed.is_recording, "recording_stopped_during_resume");
        metrics.pauseResumeCycles += 1;
        nextPause = performance.now() + options.pauseIntervalSeconds * 1000;
        emitProgress("pause_resume_complete", { cycles: metrics.pauseResumeCycles });
      }
      if (now >= nextSample) {
        const [sample, uiSample, transcriptionStatus] = await Promise.all([
          processTreeMemory(app.pid),
          sampleLiveUi(client),
          client.invoke("get_transcription_status"),
        ]);
        metrics.memorySamples.push(sample);
        metrics.uiSamples.push(uiSample);
        metrics.maximumSttQueueDepth = Math.max(
          metrics.maximumSttQueueDepth,
          Number(transcriptionStatus.max_chunks_in_queue ?? 0),
        );
        metrics.maximumAudioPipelineQueueDepth = Math.max(
          metrics.maximumAudioPipelineQueueDepth,
          Number(transcriptionStatus.max_audio_chunks_in_queue ?? 0),
        );
        nextSample = performance.now() + options.sampleIntervalSeconds * 1000;
        emitProgress("sample", {
          elapsed_seconds: Number(((now - runStarted) / 1000).toFixed(1)),
          process_count: sample.process_count,
          rss_bytes: sample.rss_bytes,
          role_rss_bytes: sample.roles,
          mounted_history_rows: uiSample.mounted_history_rows,
          transcription_queue_depth: Number(transcriptionStatus.chunks_in_queue ?? -1),
          maximum_stt_queue_depth: metrics.maximumSttQueueDepth,
          audio_pipeline_queue_depth: Number(transcriptionStatus.audio_chunks_in_queue ?? -1),
          maximum_audio_pipeline_queue_depth: metrics.maximumAudioPipelineQueueDepth,
        });
      }
      await delay(250);
    }

    await audioLoop.stop();
    await audioLoopPromise;
    if (audioLoopFailed) throw new Error("audio_loop_failed");
    const observedDurationSeconds = (performance.now() - runStarted) / 1000;
    const diagnostics = await client.invoke("get_meeting_intelligence_diagnostics");
    emitProgress("stopping", { accepted: diagnostics.accepted });
    const stopStarted = performance.now();
    const stops = await Promise.allSettled([
      client.invoke("stop_recording", { args: { save_path: savePath } }),
      client.invoke("stop_recording", { args: { save_path: savePath } }),
    ]);
    const stopElapsedSeconds = (performance.now() - stopStarted) / 1000;
    const successfulStopCalls = stops.filter((result) => result.status === "fulfilled").length;
    const stopped = await client.invoke("get_recording_state");
    const stoppedDiagnostics = await client.invoke("get_meeting_intelligence_diagnostics");
    const stopDiagnostics = await client.invoke("get_recording_stop_diagnostics");
    const transcriptionStatus = await client.invoke("get_transcription_status");
    metrics.maximumSttQueueDepth = Math.max(
      metrics.maximumSttQueueDepth,
      Number(transcriptionStatus.max_chunks_in_queue ?? 0),
    );
    metrics.maximumAudioPipelineQueueDepth = Math.max(
      metrics.maximumAudioPipelineQueueDepth,
      Number(transcriptionStatus.max_audio_chunks_in_queue ?? 0),
    );
    emitProgress("stop_complete", stopDiagnostics);
    requireSoak(!stopped.is_recording, "recording_not_stopped");
    requireSoak(stoppedDiagnostics.currentQueueDepth === 0, "dispatcher_queue_not_drained");
    requireSoak(stoppedDiagnostics.flushAttempted, "dispatcher_flush_not_attempted");
    requireSoak(stoppedDiagnostics.flushCompleted, "dispatcher_flush_not_completed");
    requireSoak(transcriptionStatus.chunks_in_queue === 0, "transcription_queue_not_drained");
    requireSoak(transcriptionStatus.audio_chunks_in_queue === 0, "audio_pipeline_queue_not_drained");
    requireSoak(successfulStopCalls >= 1, "all_concurrent_stop_calls_failed");
    requireSoak(!metrics.staleAskNowDuringRecovery, "stale_ask_now_during_recovery");

    const cacheBytes = await activeSessionCacheBytes(cacheDirectory, opaqueIdentity);
    const cleanupConfirmed = cacheBytes === 0
      && await quarantinedCount(cacheDirectory, opaqueIdentity) === 0;
    requireSoak(cleanupConfirmed, "session_cleanup_unconfirmed");
    metrics.memorySamples.push(await processTreeMemory(app.pid));
    metrics.uiSamples.push(await sampleLiveUi(client));
    await client.invoke("plugin:process|exit", { code: 0 }).catch(() => undefined);
    const exited = await waitForExit(app, 15_000);
    if (!exited) app.kill();
    const backendClosed = await waitForBackendStopped(options.backendPort);
    if (!backendClosed) throw new Error("backend_process_tree_not_stopped");

    const evidence = buildSoakEvidence({
      assistantCommit: options.assistantCommit,
      meetilyCommit: options.meetilyCommit,
      candidateSha256: await sha256(options.meetilyExe),
      requestedDurationSeconds: options.durationSeconds,
      observedDurationSeconds,
      language: options.language,
      audioPlaybacks: audioLoop.playbacks + (options.skipOccupiedPort ? 0 : 1),
      syntheticTransportEvents: metrics.syntheticTransportEvents,
      backendCrashesSkipped: options.skipBackendCrashes ?? false,
      pauseResumeCycles: metrics.pauseResumeCycles,
      cleanBackendCrashes: metrics.cleanBackendCrashes,
      corruptCacheCrashes: metrics.corruptCacheCrashes,
      unplannedRecoveries: metrics.unplannedRecoveries,
      occupiedPortRecovery: metrics.occupiedPortRecovery,
      corruptCacheQuarantined: metrics.corruptCacheQuarantined,
      staleAskNowDuringRecovery: metrics.staleAskNowDuringRecovery,
      concurrentStopCalls: 2,
      successfulStopCalls,
      stopElapsedSeconds,
      recordingStopped: !stopped.is_recording,
      recordingDrainCompleted: Boolean(stopDiagnostics.recordingDrainCompleted),
      dispatcherFlushCompleted: stoppedDiagnostics.flushCompleted,
      sessionCleanupConfirmed: cleanupConfirmed,
      candidateProcessTreeStopped: Boolean(exited && backendClosed),
      cdpReconnects: client.reconnectCount,
      memoryWarmupSeconds: options.memoryWarmupSeconds,
      maximumSttQueueDepth: metrics.maximumSttQueueDepth,
      maximumAudioPipelineQueueDepth: metrics.maximumAudioPipelineQueueDepth,
      recordingQueuePeak: Number(stopDiagnostics.recordingQueuePeak ?? 0),
      recordingDrainMilliseconds: Number(stopDiagnostics.recordingDrainMs ?? 0),
      recordingFinalCheckpointMilliseconds: Number(
        stopDiagnostics.recordingFinalCheckpointMs ?? 0,
      ),
      recordingMergeMilliseconds: Number(stopDiagnostics.recordingMergeMs ?? 0),
      recordingCheckpointCleanupMilliseconds: Number(
        stopDiagnostics.recordingCheckpointCleanupMs ?? 0,
      ),
      recordingFinalizeMetadataMilliseconds: Number(
        stopDiagnostics.recordingTranscriptMetadataMs ?? 0,
      ),
      transportToPanelLatencyMilliseconds: metrics.transportToPanelLatencyMilliseconds,
      enqueueLatencyMilliseconds: metrics.enqueueLatencyMilliseconds,
      projectionLatencyMilliseconds: metrics.projectionLatencyMilliseconds,
      transportToPanelSkippedObservations: metrics.transportToPanelSkippedObservations,
      transport: {
        accepted: diagnostics.accepted,
        accepted_since_memory_baseline: Math.max(
          0,
          diagnostics.accepted - baselineDiagnostics.accepted,
        ),
        retried: diagnostics.retried,
        duplicated: diagnostics.duplicated,
        missing: diagnostics.missing,
        dropped: diagnostics.dropped,
        rejected: diagnostics.rejected,
        maximum_queue_depth: diagnostics.maxQueueDepth,
        maximum_replay_history_updates: diagnostics.maxReplayHistoryUpdates,
        request_count: diagnostics.totalRequests,
        maximum_request_latency_ms: diagnostics.maxLatencyMs,
        flush_duration_ms: stoppedDiagnostics.flushDurationMs,
      },
      recoverySeconds: metrics.recoverySeconds,
      memorySamples: metrics.memorySamples,
      uiSamples: metrics.uiSamples,
      cacheBytes,
      limitations: [
        "Synthetic routed audio does not establish human usability.",
        "A run shorter than four hours is a harness smoke test, not the release soak gate.",
        "Cache corruption exercises quarantine and replay; physical disk exhaustion remains an operator fault-injection step.",
      ],
    });
    await writeEvidence(options.output, evidence);
    if (evidence.acceptance.status !== "pass") {
      throw new Error(`soak_acceptance_failed:${evidence.acceptance.failures.join(".")}`);
    }
    return evidence;
  } finally {
    await closeServer(blocker).catch(() => undefined);
    if (audioLoop) await audioLoop.stop().catch(() => undefined);
    if (audioLoopPromise) await audioLoopPromise.catch(() => undefined);
    if (client) {
      // Successful runs already exited the app before writing evidence. Avoid
      // starting a reconnect cycle against a CDP endpoint that no longer exists.
      if (app?.exitCode === null) {
        const active = await boundedCleanupInvoke(client, "get_recording_state");
        if (active.value?.is_recording) {
          await boundedCleanupInvoke(client, "stop_recording", { args: { save_path: savePath } });
        }
        await boundedCleanupInvoke(client, "plugin:process|exit", { code: 1 });
      }
      client.close();
    }
    if (app && app.exitCode === null) {
      app.kill();
      await waitForExit(app, 5_000);
    }
    let backendStopped = await waitForBackendStopped(options.backendPort, 5_000).catch(() => false);
    if (!backendStopped && await backendProcess(options.backendPort).catch(() => null)) {
      await terminateBackend(options.backendPort).catch(() => undefined);
      backendStopped = await waitForBackendStopped(options.backendPort, 5_000).catch(() => false);
    }
    await rm(temporaryRoot, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function main() {
  try {
    const options = parseSoakArgs(process.argv.slice(2));
    const evidence = await runSoak(options);
    console.log(JSON.stringify({
      status: evidence.acceptance.status,
      completed_full_duration: evidence.run.completed_full_duration,
      observed_duration_seconds: evidence.run.observed_duration_seconds,
      accepted: evidence.transport.accepted,
      recoveries: evidence.run.clean_backend_crashes + evidence.run.corrupt_cache_crashes,
    }));
    return 0;
  } catch (error) {
    const message = error instanceof Error ? error.message : "soak_failed";
    const boundedMessage = /^[a-z0-9 _.:>=?-]{1,160}$/i.test(message)
      ? message
      : "soak_failed";
    console.error(`FAIL: ${boundedMessage}`);
    return 1;
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  // runSoak has completed its bounded finally cleanup before main returns. Force
  // the CLI to release any runtime-owned WebSocket/fetch handles after that.
  process.exit(await main());
}
