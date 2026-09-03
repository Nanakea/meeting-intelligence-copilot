import type { Transcript } from '@/types';

export function compareTranscripts(left: Transcript, right: Transcript): number {
  const timeDifference = (left.chunk_start_time ?? 0) - (right.chunk_start_time ?? 0);
  if (timeDifference !== 0) return timeDifference;
  return (left.sequence_id ?? 0) - (right.sequence_id ?? 0);
}

export function filterUnseenTranscripts(
  incoming: readonly Transcript[],
  seenSequenceIds: Set<number>,
): Transcript[] {
  const accepted: Transcript[] = [];
  for (const transcript of incoming) {
    const sequenceId = transcript.sequence_id;
    if (sequenceId === undefined || seenSequenceIds.has(sequenceId)) continue;
    seenSequenceIds.add(sequenceId);
    accepted.push(transcript);
  }
  return accepted;
}

export function mergeOrderedTranscripts(
  current: readonly Transcript[],
  incoming: readonly Transcript[],
): Transcript[] {
  if (incoming.length === 0) return current as Transcript[];

  const orderedIncoming = incoming.length === 1
    ? incoming
    : [...incoming].sort(compareTranscripts);
  if (current.length === 0) return [...orderedIncoming];

  if (compareTranscripts(current[current.length - 1], orderedIncoming[0]) <= 0) {
    return [...current, ...orderedIncoming];
  }

  const merged: Transcript[] = [];
  let currentIndex = 0;
  let incomingIndex = 0;
  while (currentIndex < current.length && incomingIndex < orderedIncoming.length) {
    if (compareTranscripts(current[currentIndex], orderedIncoming[incomingIndex]) <= 0) {
      merged.push(current[currentIndex++]);
    } else {
      merged.push(orderedIncoming[incomingIndex++]);
    }
  }
  merged.push(...current.slice(currentIndex), ...orderedIncoming.slice(incomingIndex));
  return merged;
}

export function appendOrderedTranscripts(
  current: Transcript[],
  incoming: readonly Transcript[],
): Transcript[] {
  if (incoming.length === 0) return current;

  const orderedIncoming = incoming.length === 1
    ? incoming
    : [...incoming].sort(compareTranscripts);
  if (
    current.length === 0
    || compareTranscripts(current[current.length - 1], orderedIncoming[0]) <= 0
  ) {
    for (const transcript of orderedIncoming) current.push(transcript);
    return current;
  }

  return mergeOrderedTranscripts(current, orderedIncoming);
}
