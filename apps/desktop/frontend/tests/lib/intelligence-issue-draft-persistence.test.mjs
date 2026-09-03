import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const cacheModule = import('../../src/services/liveIssueDraftCache.ts');

function draft(title, language = 'en') {
  return {
    title,
    markdown: `# ${title}`,
    unresolvedCount: 1,
    ready: false,
    language,
  };
}

test('keeps live issue drafts isolated by opaque recording session and clears explicitly', async () => {
  const {
    cacheLiveIssueDraft,
    clearCachedLiveIssueDraft,
    getCachedLiveIssueDraft,
  } = await cacheModule;
  const first = 'meeting-intel-11111111111111111111111111111111';
  const second = 'meeting-intel-22222222222222222222222222222222';

  cacheLiveIssueDraft(first, draft('Inventory mismatch'));
  cacheLiveIssueDraft(second, draft('連携エラー', 'ja'));

  assert.equal(getCachedLiveIssueDraft(first)?.title, 'Inventory mismatch');
  assert.equal(getCachedLiveIssueDraft(second)?.language, 'ja');
  clearCachedLiveIssueDraft(first);
  assert.equal(getCachedLiveIssueDraft(first), null);
  assert.equal(getCachedLiveIssueDraft(second)?.title, '連携エラー');
  clearCachedLiveIssueDraft(second);
});

test('bounds abandoned live drafts while preserving the newest sessions', async () => {
  const {
    cacheLiveIssueDraft,
    clearCachedLiveIssueDraft,
    getCachedLiveIssueDraft,
  } = await cacheModule;
  const sessions = Array.from(
    { length: 9 },
    (_, index) => `meeting-intel-${String(index).padStart(32, '0')}`,
  );
  for (const [index, sessionId] of sessions.entries()) {
    cacheLiveIssueDraft(sessionId, draft(`Issue ${index}`));
  }

  assert.equal(getCachedLiveIssueDraft(sessions[0]), null);
  assert.equal(getCachedLiveIssueDraft(sessions[8])?.title, 'Issue 8');
  sessions.forEach(clearCachedLiveIssueDraft);
});

test('wires structured persistence, regeneration, and legacy read-only fallback', async () => {
  const [stopSource, pageSource, nativeSource, replaySource] = await Promise.all([
    readFile(new URL('../../src/hooks/useRecordingStop.ts', import.meta.url), 'utf8'),
    readFile(new URL('../../src/app/meeting-details/page.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../../src-tauri/src/meeting_intelligence_issue_draft.rs', import.meta.url), 'utf8'),
    readFile(new URL('../../src/services/intelligenceIssueDraft.ts', import.meta.url), 'utf8'),
  ]);

  assert.match(stopSource, /getCachedLiveStructuredIssueDrafts\(stoppedSessionId\)/);
  assert.match(stopSource, /saveMeetingIssueDrafts\(meetingId, structuredIssueDrafts\)/);
  assert.match(stopSource, /getCachedLiveIssueDraft\(stoppedSessionId\)/);
  assert.match(stopSource, /saveMeetingIssueDraft\(meetingId, preparedIssueDraft\)/);
  assert.match(stopSource, /clearCachedLiveIssueDraft\(stoppedSessionId!/);
  assert.match(pageSource, /getMeetingIssueDraft\(meetingId\)/);
  assert.match(nativeSource, /MAX_DRAFT_BYTES: usize = 64 \* 1024/);
  assert.match(nativeSource, /schema_version, draft_json/);
  assert.match(replaySource, /\/ingest\/live\/\$\{encodeURIComponent\(sessionId\)\}\/batch/);
  assert.match(replaySource, /resolveIssueDraftBatch/);
  assert.match(replaySource, /method: 'DELETE'/);
  assert.doesNotMatch(nativeSource, /log::(?:info|warn|error)!\([^;]*draft_markdown/s);
});

test('finalizes authoritative drafts before cleanup and persists separate export types', async () => {
  const [recordingSource, exportSource, serviceSource, migrationSource] = await Promise.all([
    readFile(new URL('../../src-tauri/src/audio/recording_commands.rs', import.meta.url), 'utf8'),
    readFile(new URL('../../src-tauri/src/meeting_intelligence_issue_export.rs', import.meta.url), 'utf8'),
    readFile(new URL('../../src/services/intelligenceIssueDraft.ts', import.meta.url), 'utf8'),
    readFile(new URL('../../src-tauri/migrations/20260827000000_structured_issue_drafts_v5.sql', import.meta.url), 'utf8'),
  ]);

  const draftRequest = recordingSource.indexOf('request_final_issue_drafts(');
  const cleanupRequest = recordingSource.indexOf('request_intelligence_session_cleanup(', draftRequest);
  assert.ok(draftRequest >= 0 && cleanupRequest > draftRequest);
  assert.match(exportSource, /jira_work_type/);
  assert.match(exportSource, /azure_work_item_type/);
  assert.match(serviceSource, /get_issue_export_settings/);
  assert.match(serviceSource, /set_issue_export_settings/);
  assert.match(migrationSource, /CREATE TABLE meeting_issue_export_settings/);
});
