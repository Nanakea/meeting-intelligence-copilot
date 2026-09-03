import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CAPABILITY_TOKEN_HEADER,
  INTELLIGENCE_WEBSOCKET_PROTOCOL,
  buildIntelligenceHeaders,
  buildIntelligenceWebSocketProtocols,
  buildIntelligenceWebSocketUrl,
  validateLiveBatchAcknowledgement,
  validateIntelligenceTransport,
  type MeetingIntelligenceTransport,
} from "../../src/services/intelligenceTransport.ts";
import {
  isIntelligenceRecoverySequenceCaughtUp,
  mergeIntelligenceRecoverySequence,
  shouldReplayCanonicalHistoryAfterRepair,
  shouldResumeIntelligenceSocketOnSidecarReady,
  validateIntelligenceDispatcherRepairResult,
  validateIntelligenceSidecarStatus,
} from "../../src/services/intelligenceLifecycle.ts";
import {
  createSpokenPreflightSessionId,
  validSpokenPreflightResult,
} from "../../src/services/intelligencePilot.ts";
import { selectPreflightAudioLevels } from "../../src/services/preflightAudioLevels.ts";
import { visibleIntelligenceFingerprint } from "../../src/services/intelligenceProjection.ts";
import { emptyLiveIntelligenceSnapshot } from "../../src/services/intelligenceContracts.ts";
import {
  exposeMeetingIntelligenceSoakInstaller,
  installMeetingIntelligenceSoakBridge,
  MEETING_INTELLIGENCE_SOAK_MARKER,
  type MeetingIntelligenceSoakTarget,
} from "../../src/lib/meetingIntelligenceSoakBridge.ts";

const TOKEN = "a".repeat(64);
const AUTHENTICATED: MeetingIntelligenceTransport = {
  baseUrl: "http://127.0.0.1:8000",
  token: TOKEN,
  authEnabled: true,
};

test("ignores transport metadata and evidence churn in the visible panel revision", () => {
  const first = emptyLiveIntelligenceSnapshot("meeting-intel-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  first.version = 1;
  first.last_event_seq = 0;
  first.pain_points = [{
    pain_id: "pain-1",
    meeting_id: first.meeting_id,
    template_id: "data_mismatch",
    title: "Reports do not match",
    instance_key: "pi-test",
    identity_status: "anchored",
    identity_aliases: [],
    identity_revision: 0,
    subject: "SAP <-> ERP inventory mismatch",
    status: "open",
    kind: "evidence",
    evidence_event_ids: ["event-1"],
    created_seq: 0,
  }];
  const metadataOnly = structuredClone(first);
  metadataOnly.version = 2;
  metadataOnly.last_event_seq = 1;
  metadataOnly.pain_points[0].evidence_event_ids.push("event-2");

  assert.equal(
    visibleIntelligenceFingerprint(metadataOnly),
    visibleIntelligenceFingerprint(first),
  );

  metadataOnly.pain_points[0].title = "Different visible topic";
  assert.notEqual(
    visibleIntelligenceFingerprint(metadataOnly),
    visibleIntelligenceFingerprint(first),
  );
});

test("keeps the packaged soak bridge opt-in, bounded, and removable", async () => {
  const inactive: MeetingIntelligenceSoakTarget = {};
  const removeInstaller = exposeMeetingIntelligenceSoakInstaller(inactive);
  assert.equal(typeof inactive.__MEETILY_SOAK_INSTALL__, "function");
  inactive.__MEETILY_SOAK_INSTALL__!()();
  assert.equal(inactive.__MEETILY_SOAK_INVOKE__, undefined);

  const active: MeetingIntelligenceSoakTarget = {
    __MEETILY_SOAK_PRELOAD__: MEETING_INTELLIGENCE_SOAK_MARKER,
  };
  const cleanup = installMeetingIntelligenceSoakBridge(active);
  assert.equal(typeof active.__MEETILY_SOAK_INVOKE__, "function");
  const bridgeSource = readFileSync(
    new URL("../../src/lib/meetingIntelligenceSoakBridge.ts", import.meta.url),
    "utf8",
  );
  assert.match(bridgeSource, /'emit_meeting_intelligence_soak_update'/);
  assert.match(bridgeSource, /'get_recording_stop_diagnostics'/);
  assert.match(bridgeSource, /soakAuthorization \?\?= invoke<boolean>/);
  const rustSource = readFileSync(
    new URL("../../src-tauri/src/lib.rs", import.meta.url),
    "utf8",
  );
  const soakCommand = rustSource.match(
    /async fn emit_meeting_intelligence_soak_update[\s\S]*?\r?\n}\r?\n/,
  )?.[0] ?? "";
  assert.match(soakCommand, /audio::recording_commands::is_recording\(\)\.await/);
  assert.doesNotMatch(soakCommand, /RECORDING_FLAG/);
  await assert.rejects(() => active.__MEETILY_SOAK_INVOKE__!("read_audio_file"));
  cleanup();
  assert.equal(active.__MEETILY_SOAK_INVOKE__, undefined);
  removeInstaller();
  assert.equal(inactive.__MEETILY_SOAK_INSTALL__, undefined);
});

test("keeps capability tokens and language metadata out of WebSocket URLs", () => {
  const url = buildIntelligenceWebSocketUrl(AUTHENTICATED, "meeting-1");

  assert.equal(url, "ws://127.0.0.1:8000/ws/meeting/meeting-1");
  assert.equal(url.includes(TOKEN), false);
  assert.deepEqual(buildIntelligenceWebSocketProtocols(AUTHENTICATED), [
    INTELLIGENCE_WEBSOCKET_PROTOCOL,
    `token.${TOKEN}`,
  ]);
});

test("requires a complete bounded replay acknowledgement", () => {
  assert.deepEqual(
    validateLiveBatchAcknowledgement({
      status: "applied",
      received_sequence_id: 199,
      next_expected_sequence_id: 200,
      state_version: 200,
      rejected_sequence_ids: [],
    }),
    {
      status: "applied",
      received_sequence_id: 199,
      next_expected_sequence_id: 200,
      state_version: 200,
      rejected_sequence_ids: [],
    },
  );
  assert.throws(() =>
    validateLiveBatchAcknowledgement({
      status: "applied",
      received_sequence_id: 199,
      next_expected_sequence_id: 200,
      state_version: 200,
    }),
  );
});

test("adds authentication only to authenticated HTTP transports", () => {
  assert.deepEqual(buildIntelligenceHeaders(AUTHENTICATED), {
    "Content-Type": "application/json",
    [CAPABILITY_TOKEN_HEADER]: TOKEN,
  });
  assert.deepEqual(
    buildIntelligenceHeaders({
      baseUrl: "http://localhost:8000",
      token: null,
      authEnabled: false,
    }),
    { "Content-Type": "application/json" },
  );
});

test("rejects missing, weak, or inconsistent authenticated configuration", () => {
  assert.throws(() =>
    validateIntelligenceTransport({
      baseUrl: "http://127.0.0.1:8000",
      token: null,
      authEnabled: true,
    }),
  );
  assert.throws(() =>
    validateIntelligenceTransport({
      baseUrl: "http://127.0.0.1:8000",
      token: "short",
      authEnabled: true,
    }),
  );
  assert.throws(() =>
    validateIntelligenceTransport({
      baseUrl: "http://127.0.0.1:8000",
      token: TOKEN,
      authEnabled: false,
    }),
  );
});

test("forces runtime transport URLs back to loopback", () => {
  const transport = validateIntelligenceTransport({
    baseUrl: "https://example.com/collect",
    token: null,
    authEnabled: false,
  });

  assert.equal(transport.baseUrl, "http://127.0.0.1:8000");
});

test("does not persist, log, or render the capability token", () => {
  const serviceSource = readFileSync(
    new URL("../../src/services/intelligenceTransport.ts", import.meta.url),
    "utf8",
  );
  const panelSource = readFileSync(
    new URL("../../src/components/IntelligencePanel/IntelligencePanel.tsx", import.meta.url),
    "utf8",
  );
  const hookSource = readFileSync(
    new URL("../../src/hooks/useIntelligencePanel.ts", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(serviceSource, /localStorage|sessionStorage|indexedDB/i);
  assert.doesNotMatch(serviceSource, /console\.(?:log|warn|error)/);
  assert.doesNotMatch(panelSource, /capability.?token|MEETING_INTELLIGENCE_TOKEN/i);
  assert.doesNotMatch(hookSource, /console\.(?:log|warn|error)/);
});

test("validates privacy-safe bounded lifecycle status", () => {
  assert.deepEqual(
    validateIntelligenceSidecarStatus({
      phase: "restarting",
      reason: "missing_executable",
      retryCount: 2,
      maxRetryCount: 3,
      retryAfterMs: 2000,
      authEnabled: true,
      managed: true,
      backendVersion: null,
    }),
    {
      phase: "restarting",
      reason: "missing_executable",
      retryCount: 2,
      maxRetryCount: 3,
      retryAfterMs: 2000,
      authEnabled: true,
      managed: true,
      backendVersion: null,
    },
  );
  assert.throws(() =>
    validateIntelligenceSidecarStatus({
      phase: "restarting",
      reason: "startup_failed",
      retryCount: 2,
      maxRetryCount: 3,
      retryAfterMs: 0,
      authEnabled: true,
      managed: true,
      backendVersion: null,
    }),
  );
  assert.throws(() =>
    validateIntelligenceSidecarStatus({
      phase: "spinning_forever",
      reason: null,
      retryCount: 999,
      maxRetryCount: 999,
      retryAfterMs: null,
      authEnabled: true,
      managed: true,
      backendVersion: null,
    }),
  );
  assert.throws(() =>
    validateIntelligenceSidecarStatus({
      phase: "unavailable",
      reason: "contains_a_local_path",
      retryCount: 3,
      maxRetryCount: 3,
      retryAfterMs: null,
      authEnabled: true,
      managed: true,
      backendVersion: null,
    }),
  );
});

test("validates sequence-bounded dispatcher repair results", () => {
  assert.deepEqual(
    validateIntelligenceDispatcherRepairResult({
      completed: true,
      latestSequenceId: 42,
    }),
    { completed: true, latestSequenceId: 42 },
  );
  assert.deepEqual(
    validateIntelligenceDispatcherRepairResult({
      completed: true,
      latestSequenceId: null,
    }),
    { completed: true, latestSequenceId: null },
  );
  assert.throws(() =>
    validateIntelligenceDispatcherRepairResult({
      completed: true,
      latestSequenceId: -1,
    }),
  );
});

test("never lowers or clears the known recovery sequence", () => {
  assert.equal(mergeIntelligenceRecoverySequence(null, null), null);
  assert.equal(mergeIntelligenceRecoverySequence(null, 36), 36);
  assert.equal(mergeIntelligenceRecoverySequence(36, null), 36);
  assert.equal(mergeIntelligenceRecoverySequence(36, 42), 42);
  assert.equal(mergeIntelligenceRecoverySequence(42, 36), 42);
});

test("recognizes a replacement connection that already reached its recovery target", () => {
  assert.equal(isIntelligenceRecoverySequenceCaughtUp(35, null), false);
  assert.equal(isIntelligenceRecoverySequenceCaughtUp(34, 35), false);
  assert.equal(isIntelligenceRecoverySequenceCaughtUp(35, 35), true);
  assert.equal(isIntelligenceRecoverySequenceCaughtUp(36, 35), true);
});

test("replays canonical history when a pruned dispatcher cannot prove backend recovery", () => {
  assert.equal(
    shouldReplayCanonicalHistoryAfterRepair({
      dispatcherCompleted: true,
      connectionSequence: -1,
      targetSequence: 42,
    }),
    true,
  );
  assert.equal(
    shouldReplayCanonicalHistoryAfterRepair({
      dispatcherCompleted: true,
      connectionSequence: 42,
      targetSequence: 42,
    }),
    false,
  );
  assert.equal(
    shouldReplayCanonicalHistoryAfterRepair({
      dispatcherCompleted: false,
      connectionSequence: 42,
      targetSequence: 42,
    }),
    true,
  );
});

test("re-arms exhausted socket retries only after the managed sidecar is ready", () => {
  const exhausted = {
    phase: "ready" as const,
    hasSocket: false,
    retryScheduled: false,
    retryCount: 5,
    maxRetries: 5,
    resumeUsed: false,
  };
  assert.equal(shouldResumeIntelligenceSocketOnSidecarReady(exhausted), true);
  assert.equal(
    shouldResumeIntelligenceSocketOnSidecarReady({ ...exhausted, phase: "restarting" }),
    false,
  );
  assert.equal(
    shouldResumeIntelligenceSocketOnSidecarReady({ ...exhausted, hasSocket: true }),
    false,
  );
  assert.equal(
    shouldResumeIntelligenceSocketOnSidecarReady({ ...exhausted, retryScheduled: true }),
    false,
  );
  assert.equal(
    shouldResumeIntelligenceSocketOnSidecarReady({ ...exhausted, retryCount: 4 }),
    false,
  );
  assert.equal(
    shouldResumeIntelligenceSocketOnSidecarReady({ ...exhausted, resumeUsed: true }),
    false,
  );
});

test("repairs from the active dispatcher before falling back to saved history", () => {
  const hookSource = readFileSync(
    new URL("../../src/hooks/useIntelligencePanel.ts", import.meta.url),
    "utf8",
  );
  const repairCall = hookSource.indexOf("await repairIntelligenceDispatcher()");
  const historyFallback = hookSource.indexOf("await recoverFromTranscriptHistory(transport)");
  assert(repairCall >= 0);
  assert(historyFallback > repairCall);
  assert.match(hookSource, /isIntelligenceRecoverySequenceCaughtUp/);
  assert.match(hookSource, /mergeIntelligenceRecoverySequence/);
  assert.match(
    hookSource,
    /if \(repair\.completed\)[\s\S]*await recoverFromTranscriptHistory\(transport\)/,
  );
});

test("keeps the unsigned pilot on a manual update path", () => {
  const layoutSource = readFileSync(
    new URL("../../src/app/layout.tsx", import.meta.url),
    "utf8",
  );
  const tauriConfig = readFileSync(
    new URL("../../src-tauri/tauri.conf.json", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(layoutSource, /UpdateCheckProvider/);
  assert.doesNotMatch(tauriConfig, /updater:default|\"updater\"|pubkey/);
});

test("keeps summary generation local-only in the pilot build", () => {
  const settingsSource = readFileSync(
    new URL("../../src/components/ModelSettingsModal.tsx", import.meta.url),
    "utf8",
  );
  const rustComposition = readFileSync(
    new URL("../../src-tauri/src/lib.rs", import.meta.url),
    "utf8",
  );
  const llmClient = readFileSync(
    new URL("../../src-tauri/src/summary/llm_client.rs", import.meta.url),
    "utf8",
  );

  assert.match(settingsSource, /SelectItem value="builtin-ai"/);
  assert.match(settingsSource, /SelectItem value="ollama"/);
  assert.doesNotMatch(settingsSource, /SelectItem value="(?:openai|claude|groq|openrouter|custom-openai)"/);
  assert.doesNotMatch(rustComposition, /get_(?:openai|anthropic|groq|openrouter)_models/);
  assert.match(llmClient, /Only local summary providers are enabled in this build/);
  assert.match(llmClient, /Ollama endpoint must be a plain loopback HTTP origin/);
});

test("uses only verified or preinstalled local model artifacts", () => {
  const onboarding = readFileSync(
    new URL("../../src/contexts/OnboardingContext.tsx", import.meta.url),
    "utf8",
  );
  const apiCommands = readFileSync(
    new URL("../../src-tauri/src/api/api.rs", import.meta.url),
    "utf8",
  );
  const parakeetCommands = readFileSync(
    new URL("../../src-tauri/src/parakeet_engine/commands.rs", import.meta.url),
    "utf8",
  );
  const summaryCommands = readFileSync(
    new URL("../../src-tauri/src/summary/summary_engine/commands.rs", import.meta.url),
    "utf8",
  );

  assert.match(onboarding, /large-v3-turbo-q5_0/);
  assert.match(onboarding, /whisper_download_model/);
  assert.doesNotMatch(onboarding, /parakeet_download_model|builtin_ai_download_model/);
  assert.match(apiCommands, /Only local transcription providers are enabled in this build/);
  assert.match(apiCommands, /Only local summary providers are enabled in this build/);
  assert.match(parakeetCommands, /Parakeet network downloads are disabled in this build/);
  assert.match(summaryCommands, /Built-in summary-model downloads are disabled/);
});

test("uses opaque recording-format identities for spoken preflight sessions", () => {
  const sessionId = createSpokenPreflightSessionId((target) => {
    target.fill(0xab);
    return target;
  });

  assert.equal(
    sessionId,
    "meeting-intel-abababababababababababababababab",
  );
  assert.match(sessionId, /^meeting-intel-[0-9a-f]{32}$/);
});

test("requires aggregate-only spoken preflight acknowledgements", () => {
  const sessionId = "meeting-intel-abababababababababababababababab";
  assert.equal(
    validSpokenPreflightResult(
      {
        sessionId,
        receivedSequenceId: 0,
        stateVersion: 1,
        transcriptCharacterCount: 42,
        durationMs: 1200,
      },
      sessionId,
    ),
    true,
  );
  assert.equal(
    validSpokenPreflightResult(
      {
        sessionId,
        receivedSequenceId: 0,
        stateVersion: 1,
        transcriptCharacterCount: 0,
        durationMs: 1200,
        text: "must not be trusted",
      },
      sessionId,
    ),
    false,
  );
});

test("spoken preflight uses authenticated WebSocket ingest and guaranteed cleanup", () => {
  const source = readFileSync(
    new URL("../../src/services/intelligencePilot.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /buildIntelligenceWebSocketProtocols/);
  assert.match(source, /run_meeting_intelligence_spoken_preflight/);
  assert.match(source, /method:\s*['"]DELETE['"]/);
  assert.match(source, /signal:\s*controller\.signal/);
  assert.match(source, /SPOKEN_PREFLIGHT_CLEANUP_TIMEOUT_MS\s*=\s*5_000/);
  assert.match(source, /finally\s*\{/);
  assert.doesNotMatch(source, /console\.(?:log|warn|error)/);
});

test("sidecar setup honors the external Cargo target directory", () => {
  const source = readFileSync(
    new URL("../../../scripts/setup-meetily-sidecars.ps1", import.meta.url),
    "utf8",
  );
  assert.match(source, /\[string\]\$CargoTargetDirectory = \$env:CARGO_TARGET_DIR/);
  assert.match(source, /Join-Path \$cargoTargetRoot "\$profile\\llama-helper\.exe"/);
  assert.match(
    source,
    /SetEnvironmentVariable\("CARGO_TARGET_DIR", \$cargoTargetRoot, "Process"\)/,
  );
  assert.doesNotMatch(source, /Join-Path \$repoRoot "target\\\$profile\\llama-helper\.exe"/);
});

test("pilot evidence scanning does not use PowerShell's automatic input variable", () => {
  const source = readFileSync(
    new URL("../../src-tauri/scripts/new-internal-pilot-evidence.ps1", import.meta.url),
    "utf8",
  );

  assert.match(source, /foreach \(\$artifactInput in \$inputs\)/);
  assert.match(source, /\[string\]\$RoutedAudioCorpusManifest/);
  assert.match(source, /\[string\]\$AcceptanceLedger/);
  assert.match(source, /\[string\]\$WhisperModelManifest/);
  assert.match(source, /routed_audio_corpus_manifest_sha256 = \$corpusManifestSha256/);
  assert.match(source, /acceptance_ledger_sha256 = \$acceptanceLedgerSha256/);
  assert.match(source, /whisper_model_manifest_sha256 = \$modelManifestSha256/);
  assert.match(source, /scenario_count -ne 108/);
  assert.match(source, /scenarios\.Count -ne 132/);
  assert.match(source, /korean_acceptance = "synthetic_only"/);
  assert.match(source, /WriteAllText\([\s\S]*MANIFEST\.json[\s\S]*UTF8Encoding\]::new\(\$false\)/);
  assert.doesNotMatch(source, /foreach \(\$input in \$inputs\)/);
  assert.match(source, /ForEach-Object \{ \$_\.Replace\(\$destination, \$artifactInput\.name\)/);
});

test("delete-all also purges authenticated backend recovery state", () => {
  const source = readFileSync(
    new URL("../../src-tauri/src/meeting_intelligence_pilot.rs", import.meta.url),
    "utf8",
  );

  assert.match(source, /client\.delete\(format!\("\{base_url\}\/meetings"\)\)/);
  assert.match(source, /CAPABILITY_TOKEN_HEADER/);
  assert.match(source, /recovery_cleanup_failed/);
  assert.match(source, /body\.reset/);
});

test("meeting deletion removes only recognized local folders before database rows", () => {
  const pilotSource = readFileSync(
    new URL("../../src-tauri/src/meeting_intelligence_pilot.rs", import.meta.url),
    "utf8",
  );
  const apiSource = readFileSync(
    new URL("../../src-tauri/src/api/api.rs", import.meta.url),
    "utf8",
  );

  assert(
    pilotSource.indexOf("delete_meeting_folder") <
      pilotSource.indexOf("MeetingsRepository::delete_meeting"),
  );
  assert.match(pilotSource, /audio::import::is_import_in_progress\(\)/);
  assert.match(pilotSource, /SummaryService::cancel_all_summaries\(\)/);
  assert.match(pilotSource, /delete_orphaned_meeting_data/);
  assert.match(pilotSource, /SummaryService::cancel_summary\(&meeting\.id\)/);
  const commandStart = apiSource.indexOf("pub async fn api_delete_meeting");
  const commandSource = apiSource.slice(
    commandStart,
    apiSource.indexOf("pub async fn api_get_meeting", commandStart),
  );
  assert(
    commandSource.indexOf("delete_meeting_folder") <
      commandSource.indexOf("MeetingsRepository::delete_meeting"),
  );
  assert.match(commandSource, /acquire_engine_lifecycle_lock\(\)\.await/);
  assert.match(commandSource, /SummaryService::cancel_summary\(&meeting_id\)/);
});

test("summary startup is serialized with destructive local-data operations", () => {
  const source = readFileSync(
    new URL("../../src-tauri/src/summary/commands.rs", import.meta.url),
    "utf8",
  );

  assert.match(source, /acquire_engine_lifecycle_lock\(\)\.await/);
  assert.match(source, /get_meeting_metadata\(&pool, &m_id\)/);
  assert.match(source, /register_cancellation_token\(&m_id\)/);
  assert(
    source.indexOf("register_cancellation_token(&m_id)") <
      source.indexOf("tauri::async_runtime::spawn"),
  );
});

test("summary completion cannot persist after meeting deletion wins the race", () => {
  const source = readFileSync(
    new URL("../../src-tauri/src/summary/service.rs", import.meta.url),
    "utf8",
  );
  const completionStart = source.indexOf(
    "Ok((final_markdown, english_markdown, num_chunks))",
  );
  const completionSource = source.slice(
    completionStart,
    source.indexOf("Err(e) =>", completionStart),
  );

  assert.match(completionSource, /acquire_engine_lifecycle_lock\(\)\.await/);
  assert.match(completionSource, /cancellation_token\.is_cancelled\(\)/);
  assert(
    completionSource.indexOf("cancellation_token.is_cancelled()") <
      completionSource.indexOf("update_meeting_name"),
  );
  assert(
    completionSource.indexOf("cancellation_token.is_cancelled()") <
      completionSource.indexOf("update_process_completed"),
  );
});

test("preflight keeps microphone and meeting-output levels separate", () => {
  const levels = selectPreflightAudioLevels(
    [
      {
        device_name: "Speakers",
        device_type: "output",
        rms_level: 0.4,
        peak_level: 0.7,
        is_active: true,
      },
      {
        device_name: "Microphone",
        device_type: "input",
        rms_level: 0.2,
        peak_level: 0.3,
        is_active: true,
      },
    ],
    "Microphone",
    "Speakers",
  );

  assert.equal(levels.microphone?.device_name, "Microphone");
  assert.equal(levels.systemAudio?.device_name, "Speakers");
  assert.equal(levels.microphone?.device_type, "input");
  assert.equal(levels.systemAudio?.device_type, "output");
});

test("preflight releases audio monitors before recording starts", () => {
  const source = readFileSync(
    new URL("../../src/components/RecordingPreflightDialog.tsx", import.meta.url),
    "utf8",
  );
  const stopMonitoring = source.indexOf(
    "await invoke('stop_audio_level_monitoring')",
  );
  const startRecording = source.indexOf("await onContinue()");

  assert.notEqual(stopMonitoring, -1);
  assert.notEqual(startRecording, -1);
  assert.ok(stopMonitoring < startRecording);
});

test("preflight requires and persists participant notification before recording", () => {
  const dialog = readFileSync(
    new URL("../../src/components/RecordingPreflightDialog.tsx", import.meta.url),
    "utf8",
  );
  const service = readFileSync(
    new URL("../../src/services/intelligencePilot.ts", import.meta.url),
    "utf8",
  );
  const rust = readFileSync(
    new URL("../../src-tauri/src/meeting_intelligence_pilot.rs", import.meta.url),
    "utf8",
  );
  const confirmation = dialog.indexOf("await confirmRecordingConsent()");
  const startRecording = dialog.indexOf("await onContinue()");

  assert.match(dialog, /checked=\{participantsInformed\}/);
  assert.match(dialog, /!participantsInformed/);
  assert.match(dialog, /I have informed participants/);
  assert.ok(confirmation >= 0 && confirmation < startRecording);
  assert.match(service, /confirm_meeting_recording_consent/);
  assert.match(rust, /confirmed_at_epoch_seconds/);
  assert.match(rust, /app_version: app\.package_info\(\)\.version\.to_string\(\)/);
  assert.match(rust, /consume_recording_consent/);
  assert.match(rust, /15 \* 60/);
  assert.match(
    rust,
    /pub struct RecordingConsentConfirmation \{\s*pub confirmed: bool,\s*pub confirmed_at_epoch_seconds: u64,\s*pub app_version: String,\s*\}/,
  );

  const recordingCommands = readFileSync(
    new URL("../../src-tauri/src/audio/recording_commands.rs", import.meta.url),
    "utf8",
  );
  assert.equal(
    recordingCommands.match(/consume_recording_consent\(&app\)\?/g)?.length,
    2,
  );
});

test("preflight cannot start monitoring after its dialog has been disposed", () => {
  const source = readFileSync(
    new URL("../../src/components/RecordingPreflightDialog.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /if \(disposed\) \{\s*registeredUnlisten\(\);\s*return;/);
  assert.match(
    source,
    /start_audio_level_monitoring[\s\S]{0,250}if \(disposed\) \{[\s\S]{0,150}stop_audio_level_monitoring/,
  );
});

test("preflight awaits recording startup and handles start failures", () => {
  const source = readFileSync(
    new URL("../../src/app/page.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /onContinue=\{continueAfterPreflight\}/);
  assert.match(
    source,
    /try\s*\{\s*await startRecordingAfterPreflight\(\);\s*\}\s*catch\s*\{/,
  );
  assert.doesNotMatch(
    source,
    /onContinue=\{\(\)\s*=>\s*void continueAfterPreflight\(\)\}/,
  );
});

test("preflight checks both recording and pilot-data volumes", () => {
  const dialog = readFileSync(
    new URL("../../src/components/RecordingPreflightDialog.tsx", import.meta.url),
    "utf8",
  );
  const rust = readFileSync(
    new URL("../../src-tauri/src/meeting_intelligence_pilot.rs", import.meta.url),
    "utf8",
  );

  assert.match(dialog, /preflight\?\.availableDiskBytes/);
  assert.match(dialog, /preflight\?\.pilotDataAvailableDiskBytes/);
  assert.match(dialog, /Recordings: \{preflight\.recordingDirectory\}/);
  assert.match(dialog, /Pilot and recovery data: \{preflight\.localDataDirectory\}/);
  assert.match(rust, /available_disk_bytes: available_space_for\(&recording_directory\)/);
  assert.match(rust, /pilot_data_available_disk_bytes: available_space_for\(&local_data_directory\)/);
});

test("the local-first pilot cannot initialize remote usage analytics", () => {
  const rustSources = [
    "../../src-tauri/src/analytics/commands.rs",
    "../../src-tauri/src/analytics/analytics.rs",
  ].map((path) => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n");
  const provider = readFileSync(
    new URL("../../src/components/AnalyticsProvider.tsx", import.meta.url),
    "utf8",
  );
  const settings = readFileSync(
    new URL("../../src/components/AnalyticsConsentSwitch.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(rustSources, /phc_[A-Za-z0-9]+|https:\/\/us\.i\.posthog\.com/);
  assert.match(rustSources, /let config = AnalyticsConfig::default\(\);/);
  assert.match(provider, /store\.set\('analyticsOptedIn', false\)/);
  assert.match(settings, /No remote telemetry/);
});

test("crash recovery preserves finalized audio and deletes folders before browser metadata", () => {
  const recovery = readFileSync(
    new URL("../../src/hooks/useTranscriptRecovery.ts", import.meta.url),
    "utf8",
  );
  const nativeRecovery = readFileSync(
    new URL("../../src-tauri/src/audio/incremental_saver.rs", import.meta.url),
    "utf8",
  );
  const nativeDeletion = readFileSync(
    new URL("../../src-tauri/src/meeting_intelligence_pilot.rs", import.meta.url),
    "utf8",
  );

  assert.match(recovery, /invoke<boolean>\('has_recoverable_audio'/);
  assert.match(nativeRecovery, /finalized_audio\.is_file\(\)[\s\S]{0,500}status: "success"/);
  const deleteFolder = recovery.indexOf("delete_unsaved_recovery_folder");
  const deleteBrowserMetadata = recovery.indexOf("indexedDBService.deleteMeeting(meetingId)");
  assert.ok(deleteFolder >= 0 && deleteFolder < deleteBrowserMetadata);
  assert.match(nativeDeletion, /already_saved[\s\S]{0,400}delete_meeting_folder/);
});

test("delete-all preserves browser recovery references when native file cleanup fails", () => {
  const source = readFileSync(
    new URL("../../src/components/RecordingSettings.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    source,
    /if \(result\.fileCleanupFailures === 0\) \{[\s\S]{0,250}indexedDBService\.deleteAllMeetings\(\)[\s\S]{0,250}else \{[\s\S]{0,200}retained for a safe retry/,
  );
});

test("retention UI preserves a policy that was saved before cleanup failed", () => {
  const source = readFileSync(
    new URL("../../src/components/RecordingSettings.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /let settingsSaved = false;/);
  assert.match(source, /settingsSaved = true;\s*const result = await applyMeetingRetention/);
  assert.match(source, /if \(!settingsSaved\) \{\s*setRetention\(previousRetention\);/);
  assert.match(source, /cleanup will retry/);
  assert.match(source, /disabled=\{retentionSaving\}/);
});

test("privacy settings expose all local storage locations", () => {
  const source = readFileSync(
    new URL("../../src/components/RecordingSettings.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /Meetings, transcripts, summaries, and local models/);
  assert.match(source, /Live recovery, pilot settings, feedback, and diagnostics/);
  assert.match(source, /Recording audio/);
  assert.match(source, /open_database_folder/);
  assert.match(source, /get_meeting_intelligence_data_directory/);
  assert.match(source, /open_meeting_intelligence_data_directory/);
  assert.match(source, /open_recordings_folder/);
  assert.match(source, /handleCopyPath\(preferences\.save_folder\)/);
  assert.match(source, /Local data folder could not be opened/);
  assert.match(source, /Local path could not be copied/);
  assert.match(source, /meeting audio captured locally by this app/);
  assert.match(source, /does not use meeting-platform APIs/);
  assert.doesNotMatch(source, /Zoom|Google Meet|Teams/);
  assert.match(source, /await refetchMeetings\(\)\.catch/);
  assert.match(source, /setCurrentMeeting\(\{ id: 'intro-call'/);
  assert.match(source, /secondaryFailures/);
  assert.match(source, /deletingData \|\| deletionBlocked/);
  assert.match(source, /RecordingStatus\.PROCESSING_TRANSCRIPTS/);
});

test("saved meetings expose a local content export without internal identifiers", () => {
  const panelSource = readFileSync(
    new URL("../../src/components/MeetingDetails/SummaryPanel.tsx", import.meta.url),
    "utf8",
  );
  const rustSource = readFileSync(
    new URL("../../src-tauri/src/meeting_export.rs", import.meta.url),
    "utf8",
  );

  assert.match(panelSource, /MeetingExportButton meetingId=\{meeting\.id\}/);
  assert.match(rustSource, /blocking_save_file/);
  assert.match(rustSource, /MeetingExportTranscript/);
  assert.match(rustSource, /MeetingExportIssueDraft/);
  assert.match(rustSource, /evidence_event_ids/);
  assert.doesNotMatch(rustSource, /struct LocalMeetingExport \{[^}]*meeting_id/);
  assert.doesNotMatch(rustSource, /struct LocalMeetingExport \{[^}]*folder_path/);
});
