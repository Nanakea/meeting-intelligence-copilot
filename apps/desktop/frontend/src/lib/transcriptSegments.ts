import type { Transcript, TranscriptSegmentData } from '@/types';

export interface TranscriptSegmentProjection {
  transcriptStorage: Transcript[] | null;
  segments: TranscriptSegmentData[];
}

function toSegment(transcript: Transcript): TranscriptSegmentData {
  return {
    id: transcript.id,
    timestamp: transcript.audio_start_time ?? 0,
    endTime: transcript.audio_end_time,
    text: transcript.text,
    confidence: transcript.confidence,
  };
}

export function projectTranscriptSegments(
  previous: TranscriptSegmentProjection,
  transcripts: Transcript[],
): TranscriptSegmentProjection {
  if (previous.transcriptStorage !== transcripts) {
    return {
      transcriptStorage: transcripts,
      segments: transcripts.map(toSegment),
    };
  }

  for (let index = previous.segments.length; index < transcripts.length; index += 1) {
    previous.segments.push(toSegment(transcripts[index]));
  }
  return previous;
}
