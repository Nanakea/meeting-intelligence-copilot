import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { evaluateWorkdayReadiness } from '../../src/services/workdayReadiness.ts';

function preflight(overrides = {}) {
  return {
    language: 'en',
    languageSupported: true,
    localSttReady: true,
    localSttModel: 'parakeet / local',
    microphoneConfigured: true,
    microphoneAvailable: true,
    microphoneDeviceName: 'microphone',
    systemAudioConfigured: true,
    systemAudioAvailable: true,
    systemAudioDeviceName: 'meeting output',
    backend: {
      phase: 'ready',
      reason: null,
      retryCount: 0,
      maxRetryCount: 3,
      retryAfterMs: null,
      authEnabled: true,
      managed: true,
      backendVersion: '0.6.0',
    },
    availableDiskBytes: 4 * 1024 * 1024 * 1024,
    pilotDataAvailableDiskBytes: 4 * 1024 * 1024 * 1024,
    recordingDirectory: 'private-recording-path',
    localDataDirectory: 'private-pilot-path',
    retention: 'thirty_days',
    ...overrides,
  };
}

test('reports full readiness when local capture and the copilot are available', () => {
  const result = evaluateWorkdayReadiness(preflight());

  assert.equal(result.localRecordingReady, true);
  assert.equal(result.liveCopilotReady, true);
  assert.equal(result.hasWarnings, false);
  assert.equal(result.checks.every((check) => check.state === 'pass'), true);
});

test('keeps backend unavailability nonblocking for local recording', () => {
  const result = evaluateWorkdayReadiness(preflight({
    backend: {
      ...preflight().backend,
      phase: 'unavailable',
      reason: 'startup_failed',
    },
  }));

  assert.equal(result.localRecordingReady, true);
  assert.equal(result.liveCopilotReady, false);
  assert.equal(result.checks.find((check) => check.id === 'backend')?.state, 'warning');
});

test('unknown retention warns without claiming verification or blocking recording', () => {
  for (const retention of [undefined, null, '', 'unexpected']) {
    const result = evaluateWorkdayReadiness(preflight({ retention }));
    assert.equal(result.checks.find((check) => check.id === 'retention').state, 'warning');
    assert.equal(result.localRecordingReady, true);
    assert.equal(result.hasWarnings, true);
  }
  for (const retention of ['seven_days', 'thirty_days', 'ninety_days', 'forever']) {
    assert.equal(evaluateWorkdayReadiness(preflight({ retention })).checks.at(-1).state, 'pass');
  }
});

test('requires local STT, both audio paths, and adequate known storage', () => {
  const result = evaluateWorkdayReadiness(preflight({
    localSttReady: false,
    microphoneAvailable: false,
    systemAudioAvailable: false,
    availableDiskBytes: 1024,
  }));

  assert.equal(result.localRecordingReady, false);
  assert.deepEqual(
    result.checks.filter((check) => check.state === 'action_required').map((check) => check.id),
    ['local_stt', 'microphone', 'system_audio', 'storage'],
  );
});

test('does not project device names, paths, tokens, or raw errors into readiness results', () => {
  const serialized = JSON.stringify(evaluateWorkdayReadiness(preflight({
    microphoneDeviceName: 'SECRET_DEVICE',
    recordingDirectory: 'SECRET_PATH',
  })));
  assert.doesNotMatch(serialized, /SECRET_DEVICE|SECRET_PATH|token|exception/i);
});

test('settings expose a bilingual, accessible first-workday workflow', async () => {
  const source = await readFile(
    new URL('../../src/components/WorkdayReadinessCard.tsx', import.meta.url),
    'utf8',
  );

  assert.match(source, /First workday readiness/);
  assert.match(source, /初日の準備確認/);
  assert.match(source, /aria-live="polite"/);
  assert.match(source, /Test transcript to panel/);
  assert.match(source, /runSpokenPipelinePreflight/);
  assert.match(source, /Connectors are optional/);
  assert.doesNotMatch(source, /microphoneDeviceName|recordingDirectory|localDataDirectory/);
});

test('release builds refuse dirty source and bind backend provenance', async () => {
  const [buildScript, setupScript] = await Promise.all([
    readFile(
      new URL('../../src-tauri/scripts/build-unsigned-windows.ps1', import.meta.url),
      'utf8',
    ),
    readFile(new URL('../../../scripts/setup-meetily-sidecars.ps1', import.meta.url), 'utf8'),
  ]);

  assert.match(buildScript, /status --porcelain=v1 --untracked-files=all/);
  assert.match(buildScript, /Refusing to build Windows installers from a dirty product worktree/);
  assert.match(buildScript, /precompany-build-provenance\.json/);
  assert.match(buildScript, /backend_source_commit/);
  assert.match(buildScript, /application = \$applicationRecord/);
  assert.match(buildScript, /updater_artifacts_enabled = \$false/);
  assert.match(setupScript, /build-provenance\.json/);
  assert.match(setupScript, /backendProvenance\.artifact\.sha256 -ne \$backendSourceHash/);
});
