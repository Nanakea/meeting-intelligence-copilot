import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
  claimRecordingPostProcessing,
  clearRecordingStoppedMetadata,
  mergeFinalizedTranscriptHistory,
  publishRecordingStoppedMetadata,
  releaseRecordingPostProcessing,
  waitForRecordingStoppedMetadata,
} from '../../src/services/recordingStopMetadata.ts';
import type { RecordingStoppedPayload } from '../../src/services/recordingService.ts';

function payload(sessionId: string, meetingName: string): RecordingStoppedPayload {
  return {
    message: 'stopped',
    folder_path: `recordings/${sessionId}`,
    meeting_name: meetingName,
    recording_session_id: sessionId,
    recording_save_status: 'saved' as const,
  };
}

test('a prior recording can never satisfy the next recording metadata wait', async () => {
  const first = 'meeting-intel-11111111111111111111111111111111';
  const second = 'meeting-intel-22222222222222222222222222222222';
  publishRecordingStoppedMetadata(payload(first, 'First'), null);

  assert.equal(await waitForRecordingStoppedMetadata(second, 5), null);
  assert.equal(
    (await waitForRecordingStoppedMetadata(first, 5))?.meeting_name,
    'First',
  );
  clearRecordingStoppedMetadata(first);
});

test('a matching late event resolves only its session waiter', async () => {
  const first = 'meeting-intel-33333333333333333333333333333333';
  const second = 'meeting-intel-44444444444444444444444444444444';
  const firstWait = waitForRecordingStoppedMetadata(first, 100);
  const secondWait = waitForRecordingStoppedMetadata(second, 10);

  publishRecordingStoppedMetadata(payload(first, 'Current'), null);

  assert.equal((await firstWait)?.meeting_name, 'Current');
  assert.equal(await secondWait, null);
  clearRecordingStoppedMetadata(first);
});

test('legacy payloads use only the active opaque session fallback', async () => {
  const sessionId = 'meeting-intel-55555555555555555555555555555555';
  const legacy = payload(sessionId, 'Legacy');
  delete legacy.recording_session_id;

  assert.equal(publishRecordingStoppedMetadata(legacy, sessionId), sessionId);
  assert.equal(
    (await waitForRecordingStoppedMetadata(sessionId, 5))?.meeting_name,
    'Legacy',
  );
  clearRecordingStoppedMetadata(sessionId);
});

test('post-processing is claimed once across independent hook instances', () => {
  const first = 'meeting-intel-66666666666666666666666666666666';
  const second = 'meeting-intel-77777777777777777777777777777777';

  assert.equal(claimRecordingPostProcessing(first), true);
  assert.equal(claimRecordingPostProcessing(first), false);
  assert.equal(claimRecordingPostProcessing(second), true);
  assert.equal(claimRecordingPostProcessing(null), true);
  assert.equal(claimRecordingPostProcessing(null), false);
  releaseRecordingPostProcessing(null);
  assert.equal(claimRecordingPostProcessing(null), true);
  releaseRecordingPostProcessing(null);
  releaseRecordingPostProcessing(first);
  assert.equal(claimRecordingPostProcessing(first), true);
  releaseRecordingPostProcessing(first);
  releaseRecordingPostProcessing(second);
});

test('tray completion listener stays mounted across recording status transitions', async () => {
  const provider = await readFile(
    new URL('../../src/contexts/RecordingPostProcessingProvider.tsx', import.meta.url),
    'utf8',
  );
  const context = await readFile(
    new URL('../../src/contexts/RecordingStateContext.tsx', import.meta.url),
    'utf8',
  );

  assert.match(provider, /const stopHandlerRef = useRef\(handleRecordingStop\)/);
  assert.match(provider, /void stopHandlerRef\.current\(event\.payload\)/);
  assert.match(provider, /stopHandlerRef\.current = handleRecordingStop/);
  assert.match(context, /const setStatus = useCallback\([\s\S]{0,400}\}, \[\]\);/);
  assert.doesNotMatch(context, /\}, \[state\.status\]\);/);
});

test('post-processing resets the completed session when a new recording starts', async () => {
  const source = await readFile(
    new URL('../../src/hooks/useRecordingStop.ts', import.meta.url),
    'utf8',
  );

  assert.match(
    source,
    /stoppedIntelligenceSessionIdRef\.current = null;\s*activeIntelligenceSessionIdRef\.current = nextSessionId;/,
  );
  assert.match(
    source,
    /if \(meetingPersisted && recoveryHandoffAcknowledged\) \{[\s\S]{0,300}clearRecordingStoppedMetadata\(completedSessionId\);[\s\S]{0,300}stoppedIntelligenceSessionIdRef\.current = null;[\s\S]{0,200}activeIntelligenceSessionIdRef\.current = null;[\s\S]{0,200}else \{\s*releaseRecordingPostProcessing\(postProcessingSessionId\);/,
  );
  assert.match(source, /recoverLatestNativeRecordingStoppedMetadata\(\)/);
  assert.match(source, /recoverNativeRecordingStoppedMetadata\(stoppedSessionId\)/);
  assert.match(source, /acknowledgeNativeRecordingStoppedMetadata\(stoppedSessionId\)/);
  assert.doesNotMatch(source, /last_recording_(?:folder_path|meeting_name)/);
});

test('the global provider recovers an unacknowledged stop only after native capture ends', async () => {
  const source = await readFile(
    new URL('../../src/contexts/RecordingPostProcessingProvider.tsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /invoke<boolean>\('is_recording'\)/);
  assert.match(source, /if \(!nativeRecordingActive\) \{[\s\S]{0,250}stopHandlerRef\.current\(true\)/);
});

test('recording listeners unregister when components dispose during async setup', async () => {
  const [controls, context, stopHook] = await Promise.all([
    '../../src/components/RecordingControls.tsx',
    '../../src/contexts/RecordingStateContext.tsx',
    '../../src/hooks/useRecordingStop.ts',
  ].map((relativePath) => readFile(new URL(relativePath, import.meta.url), 'utf8')));

  for (const source of [controls, context]) {
    assert.match(source, /let disposed = false;/);
    assert.match(source, /if \(disposed\) \{\s*unlisten\(\);\s*return false;/);
    assert.match(source, /return \(\) => \{\s*disposed = true;/);
  }
  assert.match(stopHook, /let disposed = false;/);
  assert.match(
    stopHook,
    /if \(disposed\) \{\s*registeredUnlisten\(\);\s*return;\s*\}/,
  );
  assert.match(stopHook, /return \(\) => \{\s*disposed = true;/);

  const transcriptContext = await readFile(
    new URL('../../src/contexts/TranscriptContext.tsx', import.meta.url),
    'utf8',
  );
  assert.match(transcriptContext, /const meetingId = payload\.recording_session_id;/);
  assert.match(transcriptContext, /let disposed = false;/);
  assert.match(transcriptContext, /if \(disposed\) \{\s*unlisten\(\);\s*return false;/);
});

test('local meeting persistence is idempotent and acknowledged after browser recovery state', async () => {
  const [stopHook, transcriptContext, indexedDb, storage, repository] = await Promise.all([
    '../../src/hooks/useRecordingStop.ts',
    '../../src/contexts/TranscriptContext.tsx',
    '../../src/services/indexedDBService.ts',
    '../../src/services/storageService.ts',
    '../../src-tauri/src/database/repositories/transcript.rs',
  ].map((relativePath) => readFile(new URL(relativePath, import.meta.url), 'utf8')));

  assert.match(stopHook, /folderPath,\s*stoppedSessionId,/);
  assert.match(
    stopHook,
    /await markMeetingAsSaved\(stoppedSessionId \?\? undefined\);\s*recoveryHandoffAcknowledged = true;\s*if \(stoppedSessionId\) \{\s*clearRecordingStoppedMetadata\(stoppedSessionId\);\s*await acknowledgeNativeRecordingStoppedMetadata\(stoppedSessionId\);/,
  );
  assert.match(stopHook, /let recoveryHandoffAcknowledged = false/);
  assert.match(
    stopHook,
    /if \(meetingPersisted && recoveryHandoffAcknowledged\)/,
  );
  assert.match(stopHook, /label: 'Retry cleanup'/);
  assert.match(storage, /recordingSessionId: string \| null = null/);
  assert.match(repository, /ON CONFLICT\(id\) DO NOTHING/);
  assert.match(repository, /result\.rows_affected\(\) == 0/);
  assert.match(stopHook, /recoverNativeRecordingStoppedTranscripts/);
  assert.match(stopHook, /mergeFinalizedTranscriptHistory/);
  assert.match(
    transcriptContext,
    /if \(!meetingId\) \{[\s\S]{0,250}throw new Error\('Browser recovery meeting ID is unavailable'\)/,
  );
  assert.match(transcriptContext, /explicitMeetingId \|\| currentMeetingId/);
  assert.match(
    indexedDb,
    /transaction\.oncomplete = \(\) => resolve\(\)/,
  );
  assert.match(indexedDb, /index\('meetingId'\)[\s\S]{0,100}openCursor/);
});

test('finalized native history repairs late segments without duplicating rendered events', () => {
  const transcript = (sequenceId: number, text: string) => ({
    id: `segment-${sequenceId}`,
    text,
    timestamp: '00:00:00',
    sequence_id: sequenceId,
  });

  const merged = mergeFinalizedTranscriptHistory(
    [transcript(0, 'rendered'), transcript(1, 'stale')],
    [transcript(1, 'final'), transcript(2, 'late')],
  );

  assert.deepEqual(
    merged.map((item) => [item.sequence_id, item.text]),
    [[0, 'rendered'], [1, 'final'], [2, 'late']],
  );
});

test('saved finite retention is reapplied after database startup', async () => {
  const rustSource = await readFile(
    new URL('../../src-tauri/src/meeting_intelligence_pilot.rs', import.meta.url),
    'utf8',
  );
  const startupSource = await readFile(
    new URL('../../src-tauri/src/lib.rs', import.meta.url),
    'utf8',
  );

  assert.match(rustSource, /apply_saved_meeting_intelligence_retention/);
  assert.match(rustSource, /acquire_engine_lifecycle_lock\(\)\.await/);
  assert.match(
    startupSource,
    /initialize_database_on_startup[\s\S]{0,1200}apply_saved_meeting_intelligence_retention/,
  );
});

test('recording lifecycle logs do not serialize private metadata or raw errors', async () => {
  const sources = await Promise.all([
    '../../src/contexts/RecordingStateContext.tsx',
    '../../src/contexts/RecordingPostProcessingProvider.tsx',
    '../../src/hooks/useRecordingStop.ts',
    '../../src/components/RecordingSettings.tsx',
  ].map((relativePath) => readFile(new URL(relativePath, import.meta.url), 'utf8')));
  const source = sources.join('\n');

  for (const forbidden of [
    "Recording stopped event:', payload",
    "recording-stop-complete event:', event.payload",
    'Successfully saved COMPLETE meeting with ID:',
    "Current meeting set:', meetingData.title",
    "Error in handleRecordingStop:', error",
    'description: error instanceof Error ? error.message : String(error)',
    "Failed to load recording preferences:', error",
    "Failed to get default folder path:', defaultError",
    "Failed to load notification preference:', error",
  ]) {
    assert.doesNotMatch(source, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
});

test('a transcription timeout cannot skip persistence of a finalized recording', async () => {
  const source = await readFile(
    new URL('../../src/hooks/useRecordingStop.ts', import.meta.url),
    'utf8',
  );

  assert.match(source, /if \(isCallApi\) \{/);
  assert.doesNotMatch(
    source,
    /if \(isCallApi && transcriptionComplete(?:\s*==\s*true)?\) \{/,
  );
  assert.match(source, /unlistenComplete\?\.\(\);/);
  assert.match(source, /using status polling/);
});

test('native recording shutdown uses one bounded packaged stop deadline', async () => {
  const source = await readFile(
    new URL('../../src-tauri/src/audio/recording_commands.rs', import.meta.url),
    'utf8',
  );

  assert.match(source, /const RECORDING_STOP_DEADLINE: Duration = Duration::from_secs\(15\)/);
  assert.match(source, /remaining_stop_budget\(stop_deadline, TRANSCRIPTION_JOIN_BUDGET\)/);
  assert.match(source, /remaining_stop_budget\(stop_deadline, RECORDING_SAVE_BUDGET\)/);
  assert.match(source, /finish_intelligence_session\(&app, stop_deadline\)/);
  assert.doesNotMatch(source, /Duration::from_secs\((?:300|600)\)/);
  assert.doesNotMatch(source, /ZERO transcript chunks lost/);
});

test('an exhausted stop deadline still schedules bounded recovery cleanup', async () => {
  const source = await readFile(
    new URL('../../src-tauri/src/audio/recording_commands.rs', import.meta.url),
    'utf8',
  );

  assert.match(source, /if cleanup_budget\.is_zero\(\)/);
  assert.match(source, /tauri::async_runtime::spawn\(async move \{/);
  assert.match(source, /cleanup continued after the stop deadline/);
  assert.match(
    source,
    /flush_and_stop[\s\S]{0,2500}cleanup_budget\.is_zero\(\)[\s\S]{0,1000}request_intelligence_session_cleanup/,
  );
});

test('frontend post-stop settle cannot add another minute', async () => {
  const source = await readFile(
    new URL('../../src/hooks/useRecordingStop.ts', import.meta.url),
    'utf8',
  );

  assert.match(source, /const MAX_WAIT_TIME = 5000;/);
  assert.doesNotMatch(source, /const MAX_WAIT_TIME = 60000;/);
});

test('speech recognition errors never fake a completed recording stop', async () => {
  const source = await readFile(
    new URL('../../src/components/RecordingControls.tsx', import.meta.url),
    'utf8',
  );

  assert.doesNotMatch(source, /onRecordingStop\(false\)/);
  assert.match(source, /recording remains active/);
  assert.equal((source.match(/trackTranscriptionError\('speech_recognition_failed'\)/g) ?? []).length, 2);
  assert.doesNotMatch(source, /trackTranscriptionError\(errorMessage\)/);
  assert.doesNotMatch(source, /String\(event\.payload\)/);
});

test('tray stop has no unused AppData path dependency and follows actual capture state', async () => {
  const source = await readFile(
    new URL('../../src-tauri/src/tray.rs', import.meta.url),
    'utf8',
  );

  assert.equal((source.match(/save_path: String::new\(\)/g) ?? []).length, 2);
  assert.equal((source.match(/stop_result\.is_ok\(\) \|\| !crate::is_recording\(\)\.await/g) ?? []).length, 2);
  assert.doesNotMatch(source, /Failed to get app data dir:/);
  assert.doesNotMatch(source, /Failed to stop recording: \{\}/);
});

test('main stop has no unused AppData path dependency', async () => {
  const source = await readFile(
    new URL('../../src/components/RecordingControls.tsx', import.meta.url),
    'utf8',
  );

  assert.doesNotMatch(source, /appDataDir/);
  assert.match(source, /save_path:\s*''/);
  assert.doesNotMatch(source, /recording-\$\{timestamp\}\.wav/);

  const rustWrapper = await readFile(
    new URL('../../src-tauri/src/lib.rs', import.meta.url),
    'utf8',
  );
  const wrapperStart = rustWrapper.indexOf('async fn stop_recording');
  const wrapperEnd = rustWrapper.indexOf('async fn is_recording()', wrapperStart);
  assert.ok(wrapperStart >= 0 && wrapperEnd > wrapperStart);
  const wrapper = rustWrapper.slice(wrapperStart, wrapperEnd);
  assert.match(wrapper, /save_path: String::new\(\)/);
  assert.doesNotMatch(wrapper, /create_dir_all|args\.save_path|Creating directory/);
  assert.doesNotMatch(wrapper, /Failed to stop audio recording: \{\}/);
});

test('native stop cannot report failure after capture is committed', async () => {
  const source = await readFile(
    new URL('../../src-tauri/src/audio/recording_commands.rs', import.meta.url),
    'utf8',
  );

  assert.match(source, /Recording stopped event was unavailable; native stop remains committed/);
  assert.doesNotMatch(
    source,
    /"recording-stopped",[\s\S]{0,500}\.map_err\(\|e\| e\.to_string\(\)\)\?/,
  );
});

test('delayed post-stop navigation cannot clear a newly started recording', async () => {
  const source = await readFile(
    new URL('../../src/hooks/useRecordingStop.ts', import.meta.url),
    'utf8',
  );

  assert.match(source, /const completedSessionId = stoppedSessionId;/);
  assert.match(source, /currentSessionId !== completedSessionId/);
  assert.match(source, /Skipping completed-meeting navigation because a new recording started/);
  assert.match(
    source,
    /currentSessionId !== completedSessionId[\s\S]{0,300}return;[\s\S]{0,300}clearTranscripts\(\)/,
  );
});
