import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppLayout } from "../App";
import { mergeSnapshot } from "../hooks/useMeetingSocket";
import {
  emptyMeetingState,
  type InformationGap,
  type Lang,
  type MeetingFact,
  type MeetingState,
  type PainPoint,
  type QuestionSuggestion,
  type SuggestionRole,
  type TranscriptEvent,
} from "../types/contracts";

const ev = (seq: number, speaker: string, text: string, lang: Lang): TranscriptEvent => ({
  event_id: `e-${seq}`,
  meeting_id: "m",
  seq,
  speaker: { id: speaker, display_name: speaker, role: null },
  text,
  lang,
  ts_start: 0,
  ts_end: 0,
  is_final: true,
  source: "replay",
});

const pain = (cat: string, title: string): PainPoint => ({
  pain_id: `m::${cat}`,
  meeting_id: "m",
  template_id: cat,
  title,
  instance_key: `pi-test-${cat}`,
  identity_status: "anchored",
  identity_aliases: [],
  identity_revision: 0,
  status: "open",
  kind: "evidence",
  evidence_event_ids: ["e-0"],
  created_seq: 0,
});

const fact = (cat: string, slot: string, value: string): MeetingFact => ({
  fact_id: `f-${slot}`,
  meeting_id: "m",
  pain_id: `m::${cat}`,
  slot,
  value,
  kind: "evidence",
  extraction_origin: "deterministic",
  status: "active",
  evidence_event_ids: ["e-0"],
  supersedes: null,
  superseded_by: null,
  contradicts: [],
  confidence: 1,
  created_seq: 0,
});

const gap = (cat: string, slot: string, prio: number): InformationGap => ({
  gap_id: `m::${cat}::${slot}`,
  meeting_id: "m",
  pain_id: `m::${cat}`,
  slot,
  status: "open",
  resolved_by_fact_id: null,
  base_priority: prio,
  reopen_count: 0,
});

const sug = (cat: string, slot: string, role: SuggestionRole, text: string): QuestionSuggestion => ({
  suggestion_id: `m::${cat}::${slot}`,
  gap_id: `m::${cat}::${slot}`,
  role,
  text,
  reason: "…",
  status: "active",
  priority: 0,
});

const EN_FREQ_Q = "How often is that process required?";
const EN_VOLUME_Q = "How much data is processed each time?";

const jaState: MeetingState = {
  ...emptyMeetingState("m"),
  version: 3,
  transcript: [ev(0, "営業担当", "検索画面を使いやすくしてほしいです。", "ja")],
  pain_points: [pain("vague_requirement", "要求内容が曖昧")],
  facts: [fact("vague_requirement", "business_impact", "対応工数の増加")],
  gaps: [gap("vague_requirement", "expected_behavior", 0)],
  suggestions: [sug("vague_requirement", "expected_behavior", "ask_now", "理想の状態は何ですか？")],
};

const enState: MeetingState = {
  ...emptyMeetingState("m"),
  version: 3,
  transcript: [ev(0, "Operations", "One person copies rows into a spreadsheet.", "en")],
  pain_points: [pain("manual_work", "Manual workaround")],
  facts: [fact("manual_work", "business_impact", "delays report ~1h")],
  gaps: [gap("manual_work", "frequency", 1), gap("manual_work", "volume", 2)],
  suggestions: [
    sug("manual_work", "frequency", "ask_now", EN_FREQ_Q),
    sug("manual_work", "volume", "follow_up", EN_VOLUME_Q),
  ],
};

const enPromoted: MeetingState = {
  ...enState,
  version: 5,
  facts: [...enState.facts, fact("manual_work", "frequency", "every business day")],
  gaps: [gap("manual_work", "volume", 2)],
  suggestions: [sug("manual_work", "volume", "ask_now", EN_VOLUME_Q)],
};

const koState: MeetingState = {
  ...emptyMeetingState("m"),
  version: 3,
  transcript: [ev(0, "물류팀", "WMS와 NetSuite의 재고 데이터가 일치하지 않습니다.", "ko")],
  pain_points: [pain("data_mismatch", "시스템 간 데이터 불일치")],
  facts: [fact("data_mismatch", "business_impact", "출하가 지연됩니다")],
  gaps: [gap("data_mismatch", "source_of_truth", 0)],
  suggestions: [
    sug(
      "data_mismatch",
      "source_of_truth",
      "ask_now",
      "현재 어떤 시스템의 데이터를 기준 정보로 사용하고 있습니까?",
    ),
  ],
};

describe("generic rendering", () => {
  it("renders the backend-supplied pain title (not hardcoded)", () => {
    render(<AppLayout state={jaState} connected />);
    expect(screen.getByText("要求内容が曖昧")).toBeInTheDocument();
    expect(screen.queryByText("EC・倉庫間の在庫差異")).toBeNull();
  });

  it("renders JA slot labels, not snake_case", () => {
    render(<AppLayout state={jaState} connected />);
    expect(screen.getByText("業務影響")).toBeInTheDocument(); // known fact label
    expect(screen.getByText("望ましい状態")).toBeInTheDocument(); // missing gap label
    expect(screen.queryByText("business_impact")).toBeNull();
    expect(screen.queryByText("expected_behavior")).toBeNull();
  });

  it("renders EN slot labels for an English meeting", () => {
    render(<AppLayout state={enState} connected />);
    expect(screen.getByText("Business impact")).toBeInTheDocument();
    expect(screen.getByText("Frequency")).toBeInTheDocument();
    expect(screen.queryByText("frequency")).toBeNull();
  });

  it("renders the English ASK NOW question", () => {
    render(<AppLayout state={enState} connected />);
    expect(screen.getByText(EN_FREQ_Q)).toBeInTheDocument();
  });

  it("renders Korean chrome, slot labels, and the backend question", () => {
    render(<AppLayout state={koState} connected />);
    expect(screen.getByRole("region", { name: "지금 질문" })).toBeInTheDocument();
    expect(screen.getByText("업무 영향")).toBeInTheDocument();
    expect(screen.getByText("기준 정보")).toBeInTheDocument();
    expect(
      screen.getByText("현재 어떤 시스템의 데이터를 기준 정보로 사용하고 있습니까?"),
    ).toBeInTheDocument();
  });
});

describe("retraction + promotion (generic)", () => {
  it("drops the answered question and promotes the next on a newer snapshot", () => {
    const { rerender } = render(<AppLayout state={enState} connected />);
    expect(screen.getByText(EN_FREQ_Q)).toBeInTheDocument();
    rerender(<AppLayout state={enPromoted} connected />);
    expect(screen.queryByText(EN_FREQ_Q)).toBeNull();
    expect(screen.getByText(EN_VOLUME_Q)).toBeInTheDocument();
  });
});

describe("at most two follow-ups", () => {
  it("renders no more than two FOLLOW UP cards", () => {
    const overloaded: MeetingState = {
      ...enState,
      suggestions: [
        sug("manual_work", "frequency", "ask_now", EN_FREQ_Q),
        sug("manual_work", "volume", "follow_up", EN_VOLUME_Q),
        sug("manual_work", "time_cost", "follow_up", "How much time?"),
        sug("manual_work", "owner", "follow_up", "Who does it?"),
      ],
    };
    render(<AppLayout state={overloaded} connected />);
    const follow = screen.getByRole("region", { name: "Follow up" });
    expect(follow.querySelectorAll("li").length).toBeLessThanOrEqual(2);
  });
});

describe("small-talk stays quiet", () => {
  it("shows transcript but no pain point or ASK NOW question", () => {
    const smallTalk: MeetingState = {
      ...emptyMeetingState("m"),
      version: 2,
      transcript: [
        ev(0, "A", "おはようございます。", "ja"),
        ev(1, "B", "よろしくお願いします。", "ja"),
      ],
    };
    render(<AppLayout state={smallTalk} connected />);
    expect(screen.getByText("おはようございます。")).toBeInTheDocument();
    expect(screen.getByText("No active pain point")).toBeInTheDocument();
    expect(screen.getByText("No question yet")).toBeInTheDocument();
  });
});

describe("version guard intact", () => {
  it("ignores identical or older snapshots", () => {
    expect(mergeSnapshot(enPromoted, enState)).toBe(enPromoted);
    expect(mergeSnapshot(enState, { ...enState })).toBe(enState);
  });
});
