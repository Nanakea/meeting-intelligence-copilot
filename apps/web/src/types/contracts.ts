// Wire contracts mirrored from the backend Pydantic models
// (backend/src/copilot/domain/contracts.py — see docs/DOMAIN_MODEL.md §1).
//
// Field names are snake_case on purpose: they must match the JSON the backend
// sends verbatim. The CONTRACT_FIELDS manifests below are kept exhaustive by
// TypeScript (Record<keyof T, true>), and a Vitest schema-diff test asserts they
// equal the property sets in contracts/meeting_state.schema.json. Add a field to
// an interface without updating its manifest and the frontend fails to compile;
// let the interface drift from the backend schema and the test fails.

export type Lang = "ja" | "en" | "ko";
export type FactKind = "evidence" | "inference";
export type ExtractionOrigin = "deterministic" | "semantic_confirmed";
export type FactStatus = "active" | "superseded" | "contradicted";
export type PainStatus = "open" | "mitigated";
export type PainIdentityStatus = "anchored" | "provisional";
export type GapStatus = "open" | "answered";
export type SuggestionRole = "ask_now" | "follow_up";
export type SuggestionStatus = "active" | "retracted";

export interface Speaker {
  id: string;
  display_name?: string | null;
  role?: string | null;
}

export interface TranscriptEvent {
  event_id: string;
  meeting_id: string;
  seq: number;
  speaker: Speaker;
  text: string;
  lang: Lang;
  ts_start: number;
  ts_end: number;
  is_final: boolean;
  source: string;
}

export interface MeetingFact {
  fact_id: string;
  meeting_id: string;
  pain_id: string;
  slot: string;
  value: string;
  kind: FactKind;
  extraction_origin: ExtractionOrigin;
  status: FactStatus;
  evidence_event_ids: string[];
  supersedes?: string | null;
  superseded_by?: string | null;
  contradicts: string[];
  confidence: number;
  created_seq: number;
}

export interface PainPoint {
  pain_id: string;
  meeting_id: string;
  template_id: string;
  title: string;
  instance_key: string;
  identity_status: PainIdentityStatus;
  identity_aliases: string[];
  identity_revision: number;
  subject?: string | null;
  merged_into_pain_id?: string | null;
  status: PainStatus;
  kind: FactKind;
  evidence_event_ids: string[];
  created_seq: number;
}

export interface InformationGap {
  gap_id: string;
  meeting_id: string;
  pain_id: string;
  slot: string;
  status: GapStatus;
  resolved_by_fact_id?: string | null;
  base_priority: number;
  reopen_count: number;
}

export interface QuestionSuggestion {
  suggestion_id: string;
  gap_id: string;
  role: SuggestionRole;
  text: string;
  reason: string;
  status: SuggestionStatus;
  priority: number;
}

export interface MeetingState {
  meeting_id: string;
  version: number;
  transcript: TranscriptEvent[];
  pain_points: PainPoint[];
  facts: MeetingFact[];
  gaps: InformationGap[];
  suggestions: QuestionSuggestion[];
  last_event_seq: number;
  ambiguous_routing_count: number;
  identity_decisions: ProblemIdentityDecision[];
  confirmed_semantic_hint_ids: string[];
}

export interface ProblemIdentityDecision {
  decision_id: string;
  action: "merge" | "keep_separate";
  pain_ids: string[];
  survivor_pain_id?: string | null;
  created_version: number;
}

// --- exhaustive field manifests (compile-time complete via Record<keyof T>) ---

const speakerFields: Record<keyof Speaker, true> = {
  id: true,
  display_name: true,
  role: true,
};

const transcriptEventFields: Record<keyof TranscriptEvent, true> = {
  event_id: true,
  meeting_id: true,
  seq: true,
  speaker: true,
  text: true,
  lang: true,
  ts_start: true,
  ts_end: true,
  is_final: true,
  source: true,
};

const meetingFactFields: Record<keyof MeetingFact, true> = {
  fact_id: true,
  meeting_id: true,
  pain_id: true,
  slot: true,
  value: true,
  kind: true,
  extraction_origin: true,
  status: true,
  evidence_event_ids: true,
  supersedes: true,
  superseded_by: true,
  contradicts: true,
  confidence: true,
  created_seq: true,
};

const painPointFields: Record<keyof PainPoint, true> = {
  pain_id: true,
  meeting_id: true,
  template_id: true,
  title: true,
  instance_key: true,
  identity_status: true,
  identity_aliases: true,
  identity_revision: true,
  subject: true,
  merged_into_pain_id: true,
  status: true,
  kind: true,
  evidence_event_ids: true,
  created_seq: true,
};

const informationGapFields: Record<keyof InformationGap, true> = {
  gap_id: true,
  meeting_id: true,
  pain_id: true,
  slot: true,
  status: true,
  resolved_by_fact_id: true,
  base_priority: true,
  reopen_count: true,
};

const questionSuggestionFields: Record<keyof QuestionSuggestion, true> = {
  suggestion_id: true,
  gap_id: true,
  role: true,
  text: true,
  reason: true,
  status: true,
  priority: true,
};

const meetingStateFields: Record<keyof MeetingState, true> = {
  meeting_id: true,
  version: true,
  transcript: true,
  pain_points: true,
  facts: true,
  gaps: true,
  suggestions: true,
  last_event_seq: true,
  ambiguous_routing_count: true,
  identity_decisions: true,
  confirmed_semantic_hint_ids: true,
};

const problemIdentityDecisionFields: Record<keyof ProblemIdentityDecision, true> = {
  decision_id: true,
  action: true,
  pain_ids: true,
  survivor_pain_id: true,
  created_version: true,
};

/** Maps the JSON-schema $defs model name to its expected property set. */
export const CONTRACT_FIELDS: Record<string, string[]> = {
  Speaker: Object.keys(speakerFields),
  TranscriptEvent: Object.keys(transcriptEventFields),
  MeetingFact: Object.keys(meetingFactFields),
  PainPoint: Object.keys(painPointFields),
  InformationGap: Object.keys(informationGapFields),
  QuestionSuggestion: Object.keys(questionSuggestionFields),
  MeetingState: Object.keys(meetingStateFields),
  ProblemIdentityDecision: Object.keys(problemIdentityDecisionFields),
};

export function emptyMeetingState(meetingId: string): MeetingState {
  return {
    meeting_id: meetingId,
    version: 0,
    transcript: [],
    pain_points: [],
    facts: [],
    gaps: [],
    suggestions: [],
    last_event_seq: -1,
    ambiguous_routing_count: 0,
    identity_decisions: [],
    confirmed_semantic_hint_ids: [],
  };
}
