import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppLayout } from "../App";
import { mergeSnapshot } from "../hooks/useMeetingSocket";
import { CONTRACT_FIELDS, emptyMeetingState } from "../types/contracts";

describe("empty-state rendering", () => {
  it("renders the transcript + right-column panels with placeholders", () => {
    render(<AppLayout state={emptyMeetingState("m1")} connected={false} />);

    expect(screen.getByRole("heading", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Active pain point" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ask now" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Follow up" })).toBeInTheDocument();

    expect(screen.getByText("Waiting for transcript…")).toBeInTheDocument();
    expect(screen.getByText("No active pain point")).toBeInTheDocument();
    expect(screen.getByText("No question yet")).toBeInTheDocument();
    expect(screen.getByText("No follow-up questions")).toBeInTheDocument();
  });
});

describe("snapshot version guard (mergeSnapshot)", () => {
  it("replaces state with a strictly-newer snapshot", () => {
    const current = { ...emptyMeetingState("m1"), version: 1 };
    const incoming = { ...emptyMeetingState("m1"), version: 2 };
    expect(mergeSnapshot(current, incoming)).toBe(incoming);
  });

  it("ignores an identical or older-version snapshot", () => {
    const current = { ...emptyMeetingState("m1"), version: 2 };
    const same = { ...emptyMeetingState("m1"), version: 2 };
    const older = { ...emptyMeetingState("m1"), version: 1 };
    expect(mergeSnapshot(current, same)).toBe(current);
    expect(mergeSnapshot(current, older)).toBe(current);
  });
});

describe("contract synchronization (TS contracts vs exported JSON Schema)", () => {
  const schema = JSON.parse(
    readFileSync(
      resolve(process.cwd(), "..", "..", "contracts", "meeting_state.schema.json"),
      "utf-8",
    ),
  ) as { $defs: Record<string, { properties?: Record<string, unknown> }> };

  it("mirrors every contract's field set exactly", () => {
    for (const [model, fields] of Object.entries(CONTRACT_FIELDS)) {
      const def = schema.$defs[model];
      expect(def, `missing $defs.${model} in exported schema`).toBeDefined();
      const schemaFields = Object.keys(def.properties ?? {}).sort();
      expect([...fields].sort(), `field mismatch for ${model}`).toEqual(schemaFields);
    }
  });
});
