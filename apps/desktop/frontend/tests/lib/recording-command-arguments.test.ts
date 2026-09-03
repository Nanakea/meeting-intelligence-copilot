import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

import { buildStartRecordingCommandArguments } from "../../src/services/recordingCommandArguments.ts";

test("uses Tauri camelCase argument names for recording identity and devices", () => {
  assert.deepEqual(
    buildStartRecordingCommandArguments("CABLE Output", "CABLE Input", "Pilot Meeting"),
    {
      micDeviceName: "CABLE Output",
      systemDeviceName: "CABLE Input",
      meetingName: "Pilot Meeting",
    },
  );
});

test("preserves null default-device selections", () => {
  assert.deepEqual(
    buildStartRecordingCommandArguments(null, null, "Default Devices"),
    {
      micDeviceName: null,
      systemDeviceName: null,
      meetingName: "Default Devices",
    },
  );
});

test("recording start delegates model validation to the configured Rust provider", async () => {
  const source = await readFile(
    new URL("../../src/hooks/useRecordingStart.ts", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /parakeet_(?:init|has_available_models|get_available_models)/);
  assert.match(source, /recordingService\.startRecordingWithDevices/);
});

test("recording start cannot report failure after audio capture is committed", async () => {
  const source = await readFile(
    new URL("../../src-tauri/src/audio/recording_commands.rs", import.meta.url),
    "utf8",
  );

  assert.match(source, /Recording started event was unavailable; backend state remains authoritative/);
  assert.doesNotMatch(source, /"recording-started"[\s\S]{0,700}\.map_err\(/);
  assert.doesNotMatch(source, /"error": validation_error|return Err\(validation_error\)/);
  assert.doesNotMatch(source, /Failed to start recording: \{\}", e/);

  const wrapperSource = await readFile(
    new URL("../../src-tauri/src/lib.rs", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(wrapperSource, /Failed to show recording started notification: \{\}/);
  assert.doesNotMatch(wrapperSource, /Failed to start (?:audio )?recording(?: via tauri command)?: \{\}/);
});

test("recording start is guarded while its asynchronous request is active", async () => {
  const source = await readFile(
    new URL("../../src/components/RecordingControls.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /if \(startInFlightRef\.current \|\| isStarting \|\| isValidatingModel\) return;/);
  assert.match(source, /startInFlightRef\.current = true;\s*setIsStarting\(true\);/);
  assert.match(source, /finally \{\s*startInFlightRef\.current = false;\s*setIsStarting\(false\);\s*\}/);
});

test("recording stop is synchronously guarded and always releases processing state", async () => {
  const source = await readFile(
    new URL("../../src/components/RecordingControls.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /isStopping \|\| stopInFlightRef\.current/);
  assert.match(source, /stopInFlightRef\.current = true;/);
  assert.match(
    source,
    /finally \{\s*setIsProcessing\(false\);\s*stopInFlightRef\.current = false;\s*setIsStopping\(false\);\s*\}/,
  );
  assert.doesNotMatch(source, /No recording in progress[\s\S]{0,300}return;/);
});

test("native transcript persistence survives final STT drain and precedes transport", async () => {
  const [source, worker] = await Promise.all([
    "../../src-tauri/src/audio/recording_commands.rs",
    "../../src-tauri/src/audio/transcription/worker.rs",
  ].map((relativePath) => readFile(new URL(relativePath, import.meta.url), "utf8")));

  const workerStarts = [...source.matchAll(/transcription::start_transcription_task/g)];
  assert.equal(workerStarts.length, 2);
  for (const workerStart of workerStarts) {
    const precedingStartPath = source.slice(
      Math.max(0, workerStart.index! - 2_500),
      workerStart.index,
    );
    assert.match(precedingStartPath, /ACTIVE_TRANSCRIPT_SINK/);
    assert.match(precedingStartPath, /"recording-started"/);
    assert.match(precedingStartPath, /Publish the session identity before the worker can emit sequence 0/);
  }
  assert.match(
    source,
    /join_transcription_task[\s\S]{0,1000}ACTIVE_TRANSCRIPT_SINK\.lock\(\)\.unwrap\(\)\.take\(\)/,
  );
  const persist = worker.indexOf("persist_transcript_update_internal(&update)");
  const emit = worker.indexOf('emit("transcript-update", &update)');
  const enqueue = worker.indexOf("dispatcher.enqueue(update.clone())");
  assert.ok(persist >= 0 && persist < emit && emit < enqueue);
  assert.match(worker, /if persisted \{[\s\S]{0,500}dispatcher\.enqueue/);
});

test("the panel explains an incompatible STT and language pair without exposing internals", async () => {
  const source = await readFile(
    new URL(
      "../../src/components/IntelligencePanel/IntelligencePanel.tsx",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(source, /ingressStatus/);
  assert.match(
    source,
    /The selected speech model and meeting language are not compatible\. Recording continues\./,
  );
  assert.doesNotMatch(source, /localWhisper|parakeet-tdt|MEETING_INTELLIGENCE_TOKEN/);
});
