import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const componentUrl = new URL(
  '../../src/components/IntelligencePanel/IntelligencePanel.tsx',
  import.meta.url,
);
const pageUrl = new URL('../../src/app/page.tsx', import.meta.url);
const transcriptPanelUrl = new URL(
  '../../src/app/_components/TranscriptPanel.tsx',
  import.meta.url,
);
const tauriConfigUrl = new URL('../../src-tauri/tauri.conf.json', import.meta.url);
const recordingControlsUrl = new URL(
  '../../src/components/RecordingControls.tsx',
  import.meta.url,
);

test('keeps panel status and primary question available to assistive technology', async () => {
  const source = await readFile(componentUrl, 'utf8');

  assert.match(source, /<aside[\s\S]*?className=\{PANEL_CLASS\}[\s\S]*?aria-label=\{t\.copilot\}/);
  assert.match(source, /data-intelligence-status=\{status\}/);
  assert.match(source, /role="status"/);
  assert.match(source, /aria-live="polite"/);
  assert.match(source, /aria-labelledby="ask-now-heading"/);
  assert.match(source, /<h3\s+id="ask-now-heading"/);
  assert.match(source, /<h3\s+id="intelligence-topic-heading"/);
  assert.match(source, /aria-pressed=\{pain\.pain_id === view\.pain\?\.pain_id\}/);
  assert.match(source, /<section className="mt-4 border-t border-gray-100 pt-3" aria-label=\{t\.issueDraft\}>/);
  assert.match(source, /<h3 id="issue-draft-heading"/);
  assert.match(source, /onClick=\{\(\) => void copyIssueDraft\(\)\}/);
  assert.match(source, /onClick=\{\(\) => void copyAskNow\(\)\}/);
  assert.match(source, /navigator\.clipboard\.writeText\(visibleAskNow\.text\)/);
  assert.match(source, /setIssueCopyStatus\('idle'\);\s*}\s*, \[state\.version\]\);/);
  assert.match(source, /setFeedbackSaveFailed\(askOccurrence\)/);
  assert.match(source, /feedbackSaveFailed === askOccurrence/);
  assert.match(source, /setUsefulOccurrence\(\(current\) => current === askOccurrence \? null : current\)/);
  assert.match(source, /setDismissedOccurrence\(\(current\) => current === askOccurrence \? null : current\)/);
  assert.match(source, /dismissalCheckedOccurrence !== askOccurrence/);
  assert.match(source, /setDismissalCheckedOccurrence\(askOccurrence\)/);
});

test('does not visually truncate long topic or fact labels', async () => {
  const source = await readFile(componentUrl, 'utf8');

  assert.doesNotMatch(source, /max-w-full truncate rounded-full/);
  assert.doesNotMatch(source, /min-w-0 truncate text-\[11px\] font-semibold/);
  assert.match(source, /max-w-full break-words rounded-full/);
  assert.match(source, /min-w-0 break-words text-\[11px\] font-semibold/);
});

test('stacks transcript and intelligence surfaces at narrow logical widths', async () => {
  const [component, page, transcriptPanel, tauriConfig] = await Promise.all([
    readFile(componentUrl, 'utf8'),
    readFile(pageUrl, 'utf8'),
    readFile(transcriptPanelUrl, 'utf8'),
    readFile(tauriConfigUrl, 'utf8').then(JSON.parse),
  ]);
  const mainWindow = tauriConfig.app.windows[0];

  assert.match(component, /max-\[700px\]:h-\[45%\]/);
  assert.match(component, /max-\[700px\]:w-full/);
  assert.match(page, /max-\[700px\]:flex-col/);
  assert.match(transcriptPanel, /min-h-0 w-full flex-1/);
  assert.equal(mainWindow.minWidth, 720);
  assert.equal(mainWindow.minHeight, 560);
});

test('labels the icon-only recording controls', async () => {
  const source = await readFile(recordingControlsUrl, 'utf8');

  assert.match(source, /aria-label="Start recording"/);
  assert.match(source, /aria-label=\{isPaused \? 'Resume recording' : 'Pause recording'\}/);
  assert.match(source, /aria-label="Stop recording"/);
});
