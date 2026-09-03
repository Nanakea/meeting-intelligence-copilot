import { localCopilotUrl } from "./localCopilotUrl.ts";

export const CAPABILITY_TOKEN_HEADER = "X-Meeting-Intelligence-Token";
export const INTELLIGENCE_WEBSOCKET_PROTOCOL = "meeting-intelligence-v1";
const WEBSOCKET_TOKEN_PREFIX = "token.";
const CAPABILITY_TOKEN_PATTERN = /^[A-Za-z0-9._~-]{32,256}$/;

export interface MeetingIntelligenceTransport {
  baseUrl: string;
  token: string | null;
  authEnabled: boolean;
}

export interface LiveBatchAcknowledgement {
  status: "applied" | "buffered" | "duplicate" | "rejected";
  received_sequence_id: number | null;
  next_expected_sequence_id: number;
  state_version: number;
  rejected_sequence_ids: number[];
}

declare global {
  interface Window {
    __MEETING_COPILOT_URL__?: string;
    __MEETING_INTELLIGENCE_TOKEN__?: string;
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isValidCapabilityToken(token: unknown): token is string {
  return typeof token === "string" && CAPABILITY_TOKEN_PATTERN.test(token);
}

export function validateIntelligenceTransport(
  candidate: unknown,
): MeetingIntelligenceTransport {
  if (!candidate || typeof candidate !== "object") {
    throw new Error("Meeting Intelligence transport configuration is unavailable");
  }

  const record = candidate as Record<string, unknown>;
  if (typeof record.authEnabled !== "boolean") {
    throw new Error("Meeting Intelligence transport configuration is invalid");
  }

  const token = record.token === null ? null : record.token;
  if (record.authEnabled && !isValidCapabilityToken(token)) {
    throw new Error("Meeting Intelligence authentication is unavailable");
  }
  if (!record.authEnabled && token !== null) {
    throw new Error("Meeting Intelligence transport configuration is inconsistent");
  }

  return {
    baseUrl: localCopilotUrl(record.baseUrl),
    token: token as string | null,
    authEnabled: record.authEnabled,
  };
}

export function buildIntelligenceHeaders(
  transport: MeetingIntelligenceTransport,
): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (transport.authEnabled && transport.token) {
    headers[CAPABILITY_TOKEN_HEADER] = transport.token;
  }
  return headers;
}

export function buildIntelligenceWebSocketUrl(
  transport: MeetingIntelligenceTransport,
  sessionId: string,
): string {
  const url = new URL(
    `/ws/meeting/${encodeURIComponent(sessionId)}`,
    transport.baseUrl,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function validateLiveBatchAcknowledgement(
  candidate: unknown,
): LiveBatchAcknowledgement {
  if (!candidate || typeof candidate !== "object") {
    throw new Error("Meeting Intelligence replay acknowledgement is unavailable");
  }
  const record = candidate as Record<string, unknown>;
  const validStatus =
    record.status === "applied" ||
    record.status === "buffered" ||
    record.status === "duplicate" ||
    record.status === "rejected";
  const validReceived =
    record.received_sequence_id === null ||
    (Number.isSafeInteger(record.received_sequence_id) &&
      (record.received_sequence_id as number) >= 0);
  const validRejected =
    Array.isArray(record.rejected_sequence_ids) &&
    record.rejected_sequence_ids.every(
      (value) => Number.isSafeInteger(value) && (value as number) >= 0,
    );
  if (
    !validStatus ||
    !validReceived ||
    !Number.isSafeInteger(record.next_expected_sequence_id) ||
    (record.next_expected_sequence_id as number) < 0 ||
    !Number.isSafeInteger(record.state_version) ||
    (record.state_version as number) < 0 ||
    !validRejected
  ) {
    throw new Error("Meeting Intelligence replay acknowledgement is invalid");
  }
  return record as unknown as LiveBatchAcknowledgement;
}

export function buildIntelligenceWebSocketProtocols(
  transport: MeetingIntelligenceTransport,
): string[] {
  if (!transport.authEnabled || !transport.token) return [];
  return [
    INTELLIGENCE_WEBSOCKET_PROTOCOL,
    `${WEBSOCKET_TOKEN_PREFIX}${transport.token}`,
  ];
}

export async function loadMeetingIntelligenceTransport(): Promise<MeetingIntelligenceTransport> {
  if (typeof window !== "undefined" && window.__TAURI_INTERNALS__) {
    const { invoke } = await import("@tauri-apps/api/core");
    const transport = await invoke<unknown>("get_meeting_intelligence_transport");
    return validateIntelligenceTransport(transport);
  }

  const token =
    typeof window !== "undefined" ? window.__MEETING_INTELLIGENCE_TOKEN__ ?? null : null;
  return validateIntelligenceTransport({
    baseUrl:
      typeof window !== "undefined" ? window.__MEETING_COPILOT_URL__ : undefined,
    token,
    authEnabled: token !== null,
  });
}
