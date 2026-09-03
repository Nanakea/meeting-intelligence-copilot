import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  aggregateProcessRoleMemory,
  assertPrivacySafeEvidence,
  buildSoakEvidence,
  evaluateSoakAcceptance,
  parseSoakArgs,
  panelContextAvailable,
  panelContextRestored,
  panelTransportReady,
  nextAudioOffset,
  percentile,
  RecoveringCdpClient,
  isRecoverableCdpError,
  isRetrySafeSoakCommand,
  summarizeMemorySamples,
} from "../../scripts/meeting-intelligence-soak.mjs";

const COMMIT_A = "a".repeat(40);
const COMMIT_B = "b".repeat(40);
const HASH = "C".repeat(64);

test("parses a bounded loopback soak configuration", () => {
  const options = parseSoakArgs([
    "--meetily-exe", "candidate.exe",
    "--audio", "input.wav",
    "--ffplay", "ffplay.exe",
    "--cache-directory", "cache",
    "--output", "evidence.json",
    "--assistant-commit", COMMIT_A,
    "--meetily-commit", COMMIT_B,
    "--duration-seconds", "120",
    "--backend-port", "8124",
    "--cdp-port", "9224",
    "--language", "ja",
    "--synthetic-event-interval-ms", "750",
    "--memory-warmup-seconds", "300",
    "--skip-backend-crashes",
  ]);

  assert.equal(options.durationSeconds, 120);
  assert.equal(options.backendPort, 8124);
  assert.equal(options.cdpPort, 9224);
  assert.equal(options.language, "ja");
  assert.equal(options.syntheticEventIntervalMilliseconds, 750);
  assert.equal(options.memoryWarmupSeconds, 300);
  assert.equal(options.skipBackendCrashes, true);
  assert.equal(options.assistantCommit, COMMIT_A);
  assert.throws(() => parseSoakArgs(["--duration-seconds", "59"]));
  assert.throws(() => parseSoakArgs([
    "--meetily-exe", "candidate.exe",
    "--audio", "input.wav",
    "--ffplay", "ffplay.exe",
    "--cache-directory", "cache",
    "--output", "evidence.json",
    "--assistant-commit", COMMIT_A,
    "--meetily-commit", COMMIT_B,
    "--backend-port", "8124",
    "--cdp-port", "8124",
  ]));
});

test("computes deterministic nearest-rank percentiles", () => {
  assert.equal(percentile([4, 1, 3, 2], 0.95), 4);
  assert.equal(percentile([4, 1, 3, 2], 0.5), 3);
  assert.equal(percentile([...Array(20).fill(100), 3_000], 0.95), 100);
  assert.throws(() => percentile([], 0.95));
});

test("advances through routed audio and wraps at media duration", () => {
  assert.equal(nextAudioOffset(0, 30, 1800), 30);
  assert.equal(nextAudioOffset(1770, 30, 1800), 0);
  assert.equal(nextAudioOffset(1795, 30, 1800), 25);
  assert.throws(() => nextAudioOffset(0, 30, 0));
});

test("rejects sensitive evidence keys and values", () => {
  assert.throws(() => assertPrivacySafeEvidence({ session_id: "hidden" }));
  assert.throws(() => assertPrivacySafeEvidence({ note: "meeting-intel-0123456789abcdef0123456789abcdef" }));
  assert.throws(() => assertPrivacySafeEvidence({ note: "C:\\private\\cache" }));
  assert.doesNotThrow(() => assertPrivacySafeEvidence({ candidate_sha256: HASH }));
});

test("builds aggregate-only source-bound evidence", () => {
  const evidence = buildSoakEvidence({
    assistantCommit: COMMIT_A,
    meetilyCommit: COMMIT_B,
    candidateSha256: HASH,
    requestedDurationSeconds: 14400,
    observedDurationSeconds: 14400.25,
    language: "en",
    audioPlaybacks: 300,
    syntheticTransportEvents: 9000,
    transportToPanelLatencyMilliseconds: [40, 100, 120],
    enqueueLatencyMilliseconds: [1, 2, 3],
    projectionLatencyMilliseconds: [39, 98, 117],
    transportToPanelSkippedObservations: 2,
    backendCrashesSkipped: false,
    pauseResumeCycles: 20,
    cleanBackendCrashes: 10,
    corruptCacheCrashes: 1,
    unplannedRecoveries: 2,
    occupiedPortRecovery: true,
    corruptCacheQuarantined: true,
    staleAskNowDuringRecovery: false,
    concurrentStopCalls: 2,
    successfulStopCalls: 2,
    stopElapsedSeconds: 2.5,
    recordingStopped: true,
    recordingDrainCompleted: true,
    recordingQueuePeak: 7,
    recordingDrainMilliseconds: 8,
    recordingFinalCheckpointMilliseconds: 9,
    recordingMergeMilliseconds: 10,
    recordingCheckpointCleanupMilliseconds: 11,
    recordingFinalizeMetadataMilliseconds: 12,
    dispatcherFlushCompleted: true,
    sessionCleanupConfirmed: true,
    candidateProcessTreeStopped: true,
    cdpReconnects: 1,
    memoryWarmupSeconds: 300,
    transport: {
      accepted: 10_000,
      accepted_since_memory_baseline: 10_000,
      retried: 5,
      duplicated: 2,
      missing: 0,
      dropped: 0,
      rejected: 0,
      maximum_queue_depth: 3,
      request_count: 1007,
      maximum_request_latency_ms: 42,
    },
    recoverySeconds: [3, 5, 4],
    memorySamples: [
      { rss_bytes: 100, roles: { candidate: 40, webview: 50, backend: 10, other: 0 } },
      { rss_bytes: 125, roles: { candidate: 45, webview: 65, backend: 10, other: 5 } },
    ],
    uiSamples: [
      { mounted_history_rows: 12, dom_elements: 500 },
      { mounted_history_rows: 18, dom_elements: 550 },
    ],
    cacheBytes: 4096,
    limitations: ["Synthetic test."],
  });

  assert.equal(evidence.run.completed_full_duration, true);
  assert.equal(evidence.schema_version, 6);
  assert.equal(evidence.run.dispatcher_flush_completed, true);
  assert.equal(evidence.run.synthetic_transport_events, 9000);
  assert.equal(evidence.run.unplanned_recoveries, 2);
  assert.equal(evidence.run.session_cleanup_confirmed, true);
  assert.equal(evidence.run.cdp_reconnects, 1);
  assert.equal(evidence.run.memory_warmup_seconds, 300);
  assert.equal(evidence.performance.recovery_p95_seconds, 5);
  assert.equal(evidence.performance.rss_growth_bytes, 25);
  assert.equal(evidence.performance.process_role_rss.webview.rss_growth_bytes, 15);
  assert.equal(evidence.performance.maximum_mounted_history_rows, 18);
  assert.equal(evidence.performance.transport_to_panel_observations, 3);
  assert.equal(evidence.performance.transport_to_panel_skipped_observations, 2);
  assert.equal(evidence.performance.transport_to_panel_p95_ms, 120);
  assert.equal(evidence.performance.enqueue_p95_ms, 3);
  assert.equal(evidence.performance.projection_p95_ms, 117);
  assert.equal(evidence.performance.recording_queue_peak, 7);
  assert.equal(evidence.performance.recording_merge_ms, 10);
  assert.equal(evidence.performance.recording_finalize_metadata_ms, 12);
  assert.equal(evidence.acceptance.status, "pass");
  const withoutWarmup = structuredClone(evidence);
  withoutWarmup.run.memory_warmup_seconds = 0;
  assert.deepEqual(
    evaluateSoakAcceptance(withoutWarmup).failures,
    ["memory_warmup_minimum_not_met"],
  );
  const slowProjection = structuredClone(evidence);
  slowProjection.performance.transport_to_panel_p95_ms = 500;
  assert.deepEqual(
    evaluateSoakAcceptance(slowProjection).failures,
    ["transport_to_panel_p95_exceeded"],
  );
  const missingProjectionEvidence = structuredClone(evidence);
  missingProjectionEvidence.performance.transport_to_panel_observations = 0;
  missingProjectionEvidence.performance.transport_to_panel_p95_ms = null;
  assert.deepEqual(
    evaluateSoakAcceptance(missingProjectionEvidence).failures,
    ["transport_to_panel_observations_missing"],
  );
  const slowStop = structuredClone(evidence);
  slowStop.run.stop_elapsed_seconds = 15.001;
  assert.deepEqual(
    evaluateSoakAcceptance(slowStop).failures,
    ["stop_deadline_exceeded"],
  );
  const incompleteAudioDrain = structuredClone(evidence);
  incompleteAudioDrain.run.recording_drain_completed = false;
  assert.deepEqual(
    evaluateSoakAcceptance(incompleteAudioDrain).failures,
    ["recording_audio_not_drained"],
  );
  assert.equal(JSON.stringify(evidence).includes("meeting-intel-"), false);
  assert.doesNotThrow(() => assertPrivacySafeEvidence(evidence));
});

test("recovers one transient CDP timeout and retries only safe soak operations", async () => {
  let closed = false;
  let replacementInvocations = 0;
  const first = {
    close: () => { closed = true; },
    invoke: async () => { throw new Error("cdp_command_timeout"); },
  };
  const replacement = {
    close: () => undefined,
    invoke: async () => {
      replacementInvocations += 1;
      return 42;
    },
  };
  const client = new RecoveringCdpClient(9224, first, async () => replacement);

  assert.equal(
    await client.invoke("emit_meeting_intelligence_soak_update", { language: "en" }),
    42,
  );
  assert.equal(closed, true);
  assert.equal(client.reconnectCount, 1);
  assert.equal(replacementInvocations, 1);
  assert.equal(isRecoverableCdpError(new Error("cdp_command_timeout")), true);
  assert.equal(isRecoverableCdpError(new Error("webview_evaluation_failed")), false);
  assert.equal(isRetrySafeSoakCommand("get_recording_state"), true);
  assert.equal(isRetrySafeSoakCommand("start_recording_with_devices_and_meeting"), false);
});

test("reconnects but does not replay an ambiguous recording-start command", async () => {
  let replacementInvocations = 0;
  const client = new RecoveringCdpClient(9224, {
    close: () => undefined,
    invoke: async () => { throw new Error("cdp_connection_closed"); },
  }, async () => ({
    close: () => undefined,
    invoke: async () => {
      replacementInvocations += 1;
      return true;
    },
  }));

  await assert.rejects(
    client.invoke("start_recording_with_devices_and_meeting", {}),
    /cdp_command_outcome_unknown:start_recording_with_devices_and_meeting/,
  );
  assert.equal(client.reconnectCount, 1);
  assert.equal(replacementInvocations, 0);
});

test("classifies and summarizes process-tree memory without executable paths", () => {
  const roles = aggregateProcessRoleMemory(10, [
    { pid: 10, name: "meetily", rss_bytes: 100 },
    { pid: 11, name: "msedgewebview2.exe", rss_bytes: 50 },
    { pid: 12, name: "meeting-intelligence-backend", rss_bytes: 20 },
    { pid: 13, name: "helper", rss_bytes: 5 },
  ]);
  assert.deepEqual(roles, { candidate: 100, webview: 50, backend: 20, other: 5 });
  assert.deepEqual(summarizeMemorySamples([
    { rss_bytes: 175, roles },
    { rss_bytes: 205, roles: { ...roles, webview: 80 } },
  ]).by_role.webview, {
    baseline_rss_bytes: 50,
    maximum_rss_bytes: 80,
    rss_growth_bytes: 30,
  });
});

test("fails the release gate for excessive memory and insufficient four-hour traffic", () => {
  const evidence = buildSoakEvidence({
    assistantCommit: COMMIT_A,
    meetilyCommit: COMMIT_B,
    candidateSha256: HASH,
    requestedDurationSeconds: 14400,
    observedDurationSeconds: 14400,
    language: "en",
    audioPlaybacks: 1,
    syntheticTransportEvents: 9000,
    transportToPanelLatencyMilliseconds: [100],
    backendCrashesSkipped: false,
    pauseResumeCycles: 1,
    cleanBackendCrashes: 1,
    corruptCacheCrashes: 1,
    occupiedPortRecovery: true,
    corruptCacheQuarantined: true,
    staleAskNowDuringRecovery: false,
    concurrentStopCalls: 2,
    successfulStopCalls: 2,
    stopElapsedSeconds: 2,
    recordingStopped: true,
    recordingDrainCompleted: true,
    dispatcherFlushCompleted: true,
    sessionCleanupConfirmed: true,
    candidateProcessTreeStopped: true,
    memoryWarmupSeconds: 300,
    transport: {
      accepted: 9_999, accepted_since_memory_baseline: 9_999,
      retried: 0, duplicated: 0, missing: 0, dropped: 0,
      rejected: 0, maximum_queue_depth: 1, request_count: 9_999, maximum_request_latency_ms: 1,
    },
    recoverySeconds: [1],
    memorySamples: [
      { rss_bytes: 100, roles: { candidate: 25, webview: 25, backend: 25, other: 25 } },
      { rss_bytes: 100 + 100 * 1024 * 1024, roles: { candidate: 25, webview: 25, backend: 25, other: 25 } },
    ],
    uiSamples: [{ mounted_history_rows: 12, dom_elements: 500 }],
    cacheBytes: 0,
    limitations: ["Synthetic test."],
  });
  const acceptance = evaluateSoakAcceptance(evidence);
  assert.equal(acceptance.status, "fail");
  assert.deepEqual(acceptance.failures, [
    "rss_growth_limit_exceeded",
    "accepted_event_minimum_not_met",
  ]);
});

test("allows a short crash-free isolation run but never a four-hour gate", () => {
  const short = buildSoakEvidence({
    assistantCommit: COMMIT_A,
    meetilyCommit: COMMIT_B,
    candidateSha256: HASH,
    requestedDurationSeconds: 60,
    observedDurationSeconds: 60,
    language: "en",
    audioPlaybacks: 1,
    syntheticTransportEvents: 50,
    transportToPanelLatencyMilliseconds: [100],
    backendCrashesSkipped: true,
    pauseResumeCycles: 0,
    cleanBackendCrashes: 0,
    corruptCacheCrashes: 0,
    occupiedPortRecovery: true,
    corruptCacheQuarantined: true,
    staleAskNowDuringRecovery: false,
    concurrentStopCalls: 2,
    successfulStopCalls: 1,
    stopElapsedSeconds: 1,
    recordingStopped: true,
    recordingDrainCompleted: true,
    dispatcherFlushCompleted: true,
    sessionCleanupConfirmed: true,
    candidateProcessTreeStopped: true,
    transport: {
      accepted: 50, retried: 0, duplicated: 0, missing: 0, dropped: 0,
      rejected: 0, maximum_queue_depth: 1, request_count: 50, maximum_request_latency_ms: 1,
    },
    recoverySeconds: [],
    memorySamples: [
      { rss_bytes: 100, roles: { candidate: 25, webview: 25, backend: 25, other: 25 } },
    ],
    uiSamples: [{ mounted_history_rows: 12, dom_elements: 500 }],
    cacheBytes: 0,
    limitations: ["Synthetic test."],
  });
  assert.equal(short.acceptance.status, "pass");
  short.run.requested_duration_seconds = 14400;
  const fullGate = evaluateSoakAcceptance(short);
  assert.equal(fullGate.status, "fail");
  assert(fullGate.failures.includes("clean_crash_gate_not_exercised"));
  assert(fullGate.failures.includes("corrupt_crash_gate_not_exercised"));
});

test("fails when the live history viewport mounts an unbounded row set", () => {
  const source = readFileSync(
    new URL("../../src/app/_components/TranscriptPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /flex min-h-0 w-full flex-1 flex-col overflow-hidden/);
  assert.match(source, /min-h-0 flex-1 pb-20/);
  assert.match(source, /h-full w-2\/3 max-w-\[750px\]/);

  const evidence = buildSoakEvidence({
    assistantCommit: COMMIT_A,
    meetilyCommit: COMMIT_B,
    candidateSha256: HASH,
    requestedDurationSeconds: 60,
    observedDurationSeconds: 60,
    language: "en",
    audioPlaybacks: 1,
    syntheticTransportEvents: 50,
    transportToPanelLatencyMilliseconds: [100],
    backendCrashesSkipped: true,
    pauseResumeCycles: 0,
    cleanBackendCrashes: 0,
    corruptCacheCrashes: 0,
    occupiedPortRecovery: true,
    corruptCacheQuarantined: true,
    staleAskNowDuringRecovery: false,
    concurrentStopCalls: 2,
    successfulStopCalls: 1,
    stopElapsedSeconds: 1,
    recordingStopped: true,
    recordingDrainCompleted: true,
    dispatcherFlushCompleted: true,
    sessionCleanupConfirmed: true,
    candidateProcessTreeStopped: true,
    transport: {
      accepted: 50, retried: 0, duplicated: 0, missing: 0, dropped: 0,
      rejected: 0, maximum_queue_depth: 1, request_count: 50, maximum_request_latency_ms: 1,
    },
    recoverySeconds: [],
    memorySamples: [
      { rss_bytes: 100, roles: { candidate: 25, webview: 25, backend: 25, other: 25 } },
    ],
    uiSamples: [{ mounted_history_rows: 65, dom_elements: 5000 }],
    cacheBytes: 0,
    limitations: ["Synthetic test."],
  });

  assert.deepEqual(evidence.acceptance.failures, ["mounted_history_row_limit_exceeded"]);
});

test("restores ASK when open and topic context when already resolved", () => {
  assert.equal(panelContextAvailable({ askVisible: true, topicVisible: false }, true), true);
  assert.equal(panelContextAvailable({ askVisible: false, topicVisible: true }, true), false);
  assert.equal(panelContextAvailable({ askVisible: false, topicVisible: true }, false), true);
  assert.equal(
    panelContextRestored(
      { askVisible: true, topicVisible: true },
      { askVisible: false, topicVisible: true },
    ),
    false,
  );
  assert.equal(
    panelContextRestored(
      { askVisible: true, topicVisible: true },
      { askVisible: true, topicVisible: true },
    ),
    true,
  );
  assert.equal(
    panelContextRestored(
      { askVisible: false, topicVisible: true },
      { askVisible: false, topicVisible: true },
    ),
    true,
  );
});

test("measures panel transport only in ready healthy connection states", () => {
  assert.equal(panelTransportReady({ phase: "ready" }, { connectionStatus: "connected" }), true);
  assert.equal(panelTransportReady({ phase: "ready" }, { connectionStatus: "recovered" }), true);
  assert.equal(panelTransportReady({ phase: "restarting" }, { connectionStatus: "connected" }), false);
  assert.equal(panelTransportReady({ phase: "ready" }, { connectionStatus: "recovering" }), false);
});

test("timeout diagnostics remain aggregate-only", () => {
  const panelSource = readFileSync(
    new URL("../../src/components/IntelligencePanel/IntelligencePanel.tsx", import.meta.url),
    "utf8",
  );
  const harnessSource = readFileSync(
    new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url),
    "utf8",
  );
  assert.match(panelSource, /data-intelligence-last-event-seq/);
  assert.match(harnessSource, /context_restore_timeout/);
  assert.match(harnessSource, /context_absent_before_fault/);
  assert.match(harnessSource, /sidecar_retry_count/);
  assert.match(harnessSource, /dispatcher_queue_depth/);
  assert.match(harnessSource, /maximum_stt_queue_depth/);
  assert.match(harnessSource, /maximum_audio_pipeline_queue_depth/);
  assert.match(harnessSource, /transcription_queue_not_drained/);
  assert.match(harnessSource, /audio_pipeline_queue_not_drained/);
  assert.match(harnessSource, /PANEL_PROJECTION_TIMEOUT_MS = 30_000/);
  assert.doesNotMatch(harnessSource, /context_restore_timeout[\s\S]{0,500}session_id/);
  assert.doesNotMatch(harnessSource, /context_absent_before_fault[\s\S]{0,500}session_id/);
});

test("operational transcript logs exclude transcript and meeting-title content", () => {
  const source = readFileSync(new URL("../../src/contexts/TranscriptContext.tsx", import.meta.url), "utf8");
  assert.equal(source.includes("text: update.text.substring"), false);
  assert.equal(source.includes("text: newTranscript.text.substring"), false);
  assert.equal(source.includes("skipping:', update.text.substring"), false);
  assert.equal(source.includes("Retrieved meeting name:', meetingName"), false);
  assert.equal(source.includes("Received transcript update"), false);
  assert.equal(source.includes("Buffered transcript with sequence_id"), false);
  assert.equal(source.includes("currentMeetingId:', currentMeetingId"), false);
  assert.equal(source.includes("sessionStorage:', sessionStorage.getItem"), false);
});

test("recording command logs never interpolate private recording metadata", () => {
  const source = readFileSync(new URL("../../src-tauri/src/lib.rs", import.meta.url), "utf8");
  for (const forbidden of [
    'meeting: {:?}\", meeting_name',
    'mic: {:?}, system: {:?}',
    'Saving transcript to: {}\", file_path',
    'meeting={:?}',
  ]) {
    assert.equal(source.includes(forbidden), false, `private log pattern remains: ${forbidden}`);
  }
});

test("routes cache fault injection to the managed backend cache", () => {
  const source = readFileSync(new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url), "utf8");
  assert.match(source, /MEETING_INTELLIGENCE_CACHE_DIR: cacheDirectory/);
  assert.match(source, /corruptActiveSnapshotAndTerminate\(/);
  const faultFunction = source.slice(
    source.indexOf("async function corruptActiveSnapshotAndTerminate"),
    source.indexOf("async function quarantinedCount"),
  );
  assert(faultFunction.indexOf('database.exec("BEGIN IMMEDIATE")') >= 0);
  assert(
    faultFunction.indexOf('database.exec("BEGIN IMMEDIATE")')
      < faultFunction.indexOf("await terminateBackend(backendPort)"),
  );
  assert(
    faultFunction.indexOf("await terminateBackend(backendPort)")
      < faultFunction.indexOf('database.exec("COMMIT")'),
  );
});

test("keeps routed audio active while waiting for the initial question", () => {
  const source = readFileSync(new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url), "utf8");
  const recoveryStart = source.indexOf('}, 90_000, "queued_audio_did_not_recover")');
  const askDeadline = source.indexOf('}, 60_000, "initial_ask_now_not_rendered")');
  const mainLoop = source.indexOf("while (performance.now() < deadline)", askDeadline);

  assert(recoveryStart >= 0);
  assert(askDeadline > recoveryStart);
  assert(mainLoop > askDeadline);
  const bootstrap = source.slice(recoveryStart, askDeadline);
  assert.match(bootstrap, /audioLoop = new AudioLoop\(routedAudioOptions\)/);
  assert.match(bootstrap, /audioLoopPromise = audioLoop\.run\(\)/);
  assert.match(source.slice(askDeadline, mainLoop), /if \(!audioLoop\)/);
  assert.match(source, /"-ss", String\(this\.nextOffsetSeconds\)/);
});

test("audio-loop stop is idempotent and leaves child termination to the run loop", () => {
  const source = readFileSync(new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url), "utf8");
  const audioLoop = source.slice(
    source.indexOf("class AudioLoop"),
    source.indexOf("async function backendProcess"),
  );
  const stop = audioLoop.slice(audioLoop.indexOf("async stop()"));

  assert.match(stop, /if \(this\.stopped\) return/);
  assert.match(stop, /this\.wake = null/);
  assert.doesNotMatch(stop, /terminateChild/);
  assert.doesNotMatch(source, /taskkill\.exe[\s\S]{0,100}child\.pid/);
});

test("standalone soak exits only after bounded run cleanup completes", () => {
  const source = readFileSync(new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url), "utf8");
  const main = source.slice(source.indexOf("async function main()"));

  assert.match(main, /const evidence = await runSoak\(options\)/);
  assert.match(main, /process\.exit\(await main\(\)\)/);
  assert(
    source.indexOf("} finally {") < source.indexOf("async function main()"),
    "runSoak cleanup must remain inside the awaited call",
  );
});

test("final cleanup never reconnects to an application that already exited", () => {
  const source = readFileSync(new URL("../../scripts/meeting-intelligence-soak.mjs", import.meta.url), "utf8");
  const cleanup = source.slice(source.lastIndexOf("} finally {"), source.indexOf("async function main()"));

  assert.match(cleanup, /if \(app\?\.exitCode === null\)/);
  assert.match(cleanup, /boundedCleanupInvoke\(client, "get_recording_state"\)/);
  assert.match(cleanup, /boundedCleanupInvoke\(client, "plugin:process\|exit"/);
  assert(cleanup.indexOf("app?.exitCode === null") < cleanup.indexOf('"get_recording_state"'));
});
