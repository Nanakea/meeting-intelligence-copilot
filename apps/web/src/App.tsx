import {
  ActivePainPoint,
  AskNowCard,
  FollowUpList,
  KnownInfo,
  MissingInfo,
  TranscriptPanel,
} from "./components/panels";
import { useMeetingSocket } from "./hooks/useMeetingSocket";
import type { MeetingState } from "./types/contracts";

export function AppLayout({ state, connected }: { state: MeetingState; connected: boolean }) {
  return (
    <div className="app">
      <header className="app-header">
        <h1>Meeting Intelligence Copilot</h1>
        <span className={`status ${connected ? "on" : "off"}`}>
          {connected ? "live" : "offline"} · {state.meeting_id} · v{state.version}
        </span>
      </header>
      <div className="columns">
        <div className="col col-left">
          <TranscriptPanel state={state} />
        </div>
        <div className="col col-right">
          <ActivePainPoint state={state} />
          <AskNowCard state={state} />
          <div className="info-row">
            <KnownInfo state={state} />
            <MissingInfo state={state} />
          </div>
          <FollowUpList state={state} />
        </div>
      </div>
    </div>
  );
}

export function App({ meetingId = "demo" }: { meetingId?: string }) {
  const { state, connected } = useMeetingSocket(meetingId);
  return <AppLayout state={state} connected={connected} />;
}

export default App;
