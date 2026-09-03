import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppLayout } from "../App";
import { mergeSnapshot } from "../hooks/useMeetingSocket";
import {
  emptyMeetingState,
  type InformationGap,
  type MeetingFact,
  type MeetingState,
  type PainPoint,
  type QuestionSuggestion,
  type TranscriptEvent,
} from "../types/contracts";

const PAIN_ID = "demo::data_mismatch";
const SOURCE_Q = "現在、在庫の正として扱っているのはEC側と倉庫側のどちらでしょうか？";
const IMPACT_Q = "この在庫差異により、業務にどのような影響が出ていますか？";
const Q: Record<string, string> = {
  source_of_truth: SOURCE_Q,
  business_impact: IMPACT_Q,
  owner: "この在庫差異の対応は、どなたが担当されていますか？",
  affected_scope: "この差異は、どの商品や倉庫の範囲で発生していますか？",
};

const ev = (seq: number, speaker: string, text: string): TranscriptEvent => ({
  event_id: `demo-${seq.toString().padStart(4, "0")}`,
  meeting_id: "demo",
  seq,
  speaker: { id: speaker, display_name: speaker, role: null },
  text,
  lang: "ja",
  ts_start: 0,
  ts_end: 0,
  is_final: true,
  source: "replay",
});

const pain: PainPoint = {
  pain_id: PAIN_ID,
  meeting_id: "demo",
  template_id: "data_mismatch",
  title: "EC・倉庫間の在庫差異",
  instance_key: "pi-test-data-mismatch",
  identity_status: "anchored",
  identity_aliases: [],
  identity_revision: 0,
  status: "open",
  kind: "evidence",
  evidence_event_ids: ["demo-0000"],
  created_seq: 0,
};

const fact = (slot: string, value: string): MeetingFact => ({
  fact_id: `f-${slot}`,
  meeting_id: "demo",
  pain_id: PAIN_ID,
  slot,
  value,
  kind: "evidence",
  extraction_origin: "deterministic",
  status: "active",
  evidence_event_ids: ["demo-0002"],
  supersedes: null,
  superseded_by: null,
  contradicts: [],
  confidence: 1,
  created_seq: 2,
});

const gap = (slot: string, status: "open" | "answered", prio: number): InformationGap => ({
  gap_id: `${PAIN_ID}::${slot}`,
  meeting_id: "demo",
  pain_id: PAIN_ID,
  slot,
  status,
  resolved_by_fact_id: status === "answered" ? `f-${slot}` : null,
  base_priority: prio,
  reopen_count: 0,
});

const sug = (slot: string, role: "ask_now" | "follow_up", prio: number): QuestionSuggestion => ({
  suggestion_id: `${PAIN_ID}::${slot}`,
  gap_id: `${PAIN_ID}::${slot}`,
  role,
  text: Q[slot],
  reason: "…",
  status: "active",
  priority: prio,
});

// Checkpoint: frequency + correction_process known; source_of_truth is ASK NOW.
const checkpoint: MeetingState = {
  ...emptyMeetingState("demo"),
  version: 5,
  transcript: [
    ev(0, "EC担当", "最近、倉庫側とEC側で在庫が合わないことがあります。"),
    ev(2, "EC担当", "担当者が手動で直しています。"),
    ev(4, "EC担当", "最近はほぼ毎朝ですね。"),
  ],
  pain_points: [pain],
  facts: [fact("correction_process", "手動修正"), fact("frequency", "ほぼ毎朝")],
  gaps: [
    gap("source_of_truth", "open", 0),
    gap("business_impact", "open", 1),
    gap("owner", "open", 2),
    gap("frequency", "answered", 4),
    gap("correction_process", "answered", 5),
  ],
  suggestions: [
    sug("source_of_truth", "ask_now", 0),
    sug("business_impact", "follow_up", 1),
    sug("owner", "follow_up", 2),
  ],
};

// After source_of_truth answered: promoted ASK NOW = business_impact.
const promoted: MeetingState = {
  ...checkpoint,
  version: 7,
  facts: [...checkpoint.facts, fact("source_of_truth", "倉庫側")],
  gaps: [
    gap("source_of_truth", "answered", 0),
    gap("business_impact", "open", 1),
    gap("owner", "open", 2),
    gap("affected_scope", "open", 3),
  ],
  suggestions: [
    sug("business_impact", "ask_now", 1),
    sug("owner", "follow_up", 2),
    sug("affected_scope", "follow_up", 3),
  ],
};

describe("transcript", () => {
  it("renders chronological speaker/text entries without internal ids", () => {
    render(<AppLayout state={checkpoint} connected />);
    expect(screen.getByText("最近、倉庫側とEC側で在庫が合わないことがあります。")).toBeInTheDocument();
    expect(screen.getAllByText("EC担当").length).toBeGreaterThan(0);
    expect(screen.queryByText(/demo-0000/)).toBeNull();
  });
});

describe("right column", () => {
  it("renders the active pain point", () => {
    render(<AppLayout state={checkpoint} connected />);
    expect(screen.getByText("EC・倉庫間の在庫差異")).toBeInTheDocument();
  });

  it("renders known facts", () => {
    render(<AppLayout state={checkpoint} connected />);
    expect(screen.getByText("手動修正")).toBeInTheDocument();
    expect(screen.getByText("ほぼ毎朝")).toBeInTheDocument();
  });

  it("renders unresolved gaps", () => {
    render(<AppLayout state={checkpoint} connected />);
    // localized source_of_truth label must be present as an open-gap chip.
    expect(screen.getByText("在庫の基準")).toBeInTheDocument();
  });

  it("renders the ASK NOW source_of_truth question at the checkpoint", () => {
    render(<AppLayout state={checkpoint} connected />);
    expect(screen.getByText(SOURCE_Q)).toBeInTheDocument();
  });

  it("renders at most two FOLLOW UP cards", () => {
    const overloaded: MeetingState = {
      ...checkpoint,
      suggestions: [
        sug("source_of_truth", "ask_now", 0),
        sug("business_impact", "follow_up", 1),
        sug("owner", "follow_up", 2),
        sug("affected_scope", "follow_up", 3),
      ],
    };
    render(<AppLayout state={overloaded} connected />);
    const followSection = screen.getByRole("region", { name: "Follow up" });
    expect(followSection.querySelectorAll("li").length).toBeLessThanOrEqual(2);
  });
});

describe("retraction + promotion", () => {
  it("drops the source_of_truth question and promotes business_impact on the newer snapshot", () => {
    const { rerender } = render(<AppLayout state={checkpoint} connected />);
    expect(screen.getByText(SOURCE_Q)).toBeInTheDocument();

    rerender(<AppLayout state={promoted} connected />);
    expect(screen.queryByText(SOURCE_Q)).toBeNull();
    expect(screen.getByText(IMPACT_Q)).toBeInTheDocument();
  });
});

describe("version guard", () => {
  it("ignores identical or older snapshots", () => {
    expect(mergeSnapshot(checkpoint, { ...checkpoint })).toBe(checkpoint);
    expect(mergeSnapshot(promoted, checkpoint)).toBe(promoted);
    expect(mergeSnapshot(checkpoint, promoted)).toBe(promoted);
  });
});

describe("empty state", () => {
  it("still shows placeholders with no pain or suggestion", () => {
    render(<AppLayout state={emptyMeetingState("demo")} connected={false} />);
    expect(screen.getByText("Waiting for transcript…")).toBeInTheDocument();
    expect(screen.getByText("No active pain point")).toBeInTheDocument();
    expect(screen.getByText("No question yet")).toBeInTheDocument();
  });
});
