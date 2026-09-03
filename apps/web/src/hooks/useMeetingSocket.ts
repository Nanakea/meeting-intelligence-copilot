// Connects to the backend WebSocket and exposes the latest MeetingState snapshot.
// The backend is the single source of truth (docs/ARCHITECTURE.md §6): the client
// keeps only the newest snapshot and ignores any whose version is not strictly
// greater than the one it holds. `mergeSnapshot` is a pure helper so the
// version-guard is testable without React or a socket.

import { useEffect, useState } from "react";

import { emptyMeetingState, type MeetingState } from "../types/contracts";

/** Return the snapshot that should be held next: `incoming` only if it is newer. */
export function mergeSnapshot(current: MeetingState, incoming: MeetingState): MeetingState {
  return incoming.version > current.version ? incoming : current;
}

function meetingSocketUrl(meetingId: string): string {
  const base =
    typeof window !== "undefined" && window.location
      ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`
      : "ws://localhost:8000";
  return `${base}/ws/meeting/${encodeURIComponent(meetingId)}`;
}

export interface UseMeetingSocket {
  state: MeetingState;
  connected: boolean;
}

export function useMeetingSocket(
  meetingId: string,
  socketFactory: (url: string) => WebSocket = (url) => new WebSocket(url),
): UseMeetingSocket {
  const [state, setState] = useState<MeetingState>(() => emptyMeetingState(meetingId));
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setState(emptyMeetingState(meetingId));
    const ws = socketFactory(meetingSocketUrl(meetingId));
    ws.addEventListener("open", () => setConnected(true));
    ws.addEventListener("close", () => setConnected(false));
    ws.addEventListener("message", (event: MessageEvent) => {
      const incoming = JSON.parse(event.data as string) as MeetingState;
      setState((current) => mergeSnapshot(current, incoming));
    });
    return () => ws.close();
    // socketFactory is a stable default; intentionally keyed on meetingId only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meetingId]);

  return { state, connected };
}
