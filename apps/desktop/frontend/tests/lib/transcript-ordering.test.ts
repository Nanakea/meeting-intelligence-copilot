import assert from 'node:assert/strict';
import test from 'node:test';

import type { Transcript } from '../../src/types/index.ts';
import {
  appendOrderedTranscripts,
  filterUnseenTranscripts,
  mergeOrderedTranscripts,
} from '../../src/lib/transcriptOrdering.ts';
import {
  projectTranscriptSegments,
  type TranscriptSegmentProjection,
} from '../../src/lib/transcriptSegments.ts';

function transcript(sequenceId: number, startTime = sequenceId): Transcript {
  return {
    id: `transcript-${sequenceId}`,
    text: `text-${sequenceId}`,
    timestamp: '00:00:00',
    sequence_id: sequenceId,
    chunk_start_time: startTime,
  };
}

test('ordered transcript batches use the append path', () => {
  const existing = [transcript(0), transcript(1)];
  const next = transcript(2);

  const merged = appendOrderedTranscripts(existing, [next]);

  assert.equal(merged, existing);
  assert.deepEqual(merged.map(item => item.sequence_id), [0, 1, 2]);
  assert.equal(merged[0], existing[0]);
  assert.equal(merged[2], next);
});

test('out-of-order batches are merged chronologically without replacing existing objects', () => {
  const first = transcript(0, 0);
  const third = transcript(2, 2);
  const second = transcript(1, 1);

  const existing = [first, third];
  const merged = appendOrderedTranscripts(existing, [second]);

  assert.notEqual(merged, existing);
  assert.deepEqual(merged.map(item => item.sequence_id), [0, 1, 2]);
  assert.equal(merged[0], first);
  assert.equal(merged[2], third);
});

test('seen sequence IDs suppress replay duplicates and duplicate batch entries', () => {
  const seen = new Set([0]);
  const accepted = filterUnseenTranscripts(
    [transcript(0), transcript(1), transcript(1), transcript(2)],
    seen,
  );

  assert.deepEqual(accepted.map(item => item.sequence_id), [1, 2]);
  assert.deepEqual([...seen], [0, 1, 2]);
});

test('empty merges preserve the current array identity', () => {
  const existing = [transcript(0)];
  assert.equal(mergeOrderedTranscripts(existing, []), existing);
});

test('live segment projection appends without rebuilding prior references', () => {
  const transcripts: Transcript[] = [];
  let projection: TranscriptSegmentProjection = {
    transcriptStorage: transcripts,
    segments: [],
  };
  const segmentStorage = projection.segments;

  for (let sequenceId = 0; sequenceId < 10_000; sequenceId += 1) {
    transcripts.push(transcript(sequenceId));
    projection = projectTranscriptSegments(projection, transcripts);
  }

  assert.equal(projection.segments, segmentStorage);
  assert.equal(projection.segments.length, 10_000);
  assert.equal(projection.segments[9_999].id, 'transcript-9999');
});
