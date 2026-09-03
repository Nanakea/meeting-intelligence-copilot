import { useEffect, useState } from 'react';

import { searchProblemContext } from '@/services/contextConnectorService';
import type { ConnectedProblemBrief } from '@/services/intelligenceContracts';

const EMPTY_RESULT: ConnectedProblemBrief = {
  status: 'current',
  state_version: 0,
  pain_id: '',
  citations: [],
  unavailable_connector_ids: [],
};
const DEADLINE_MS = 2_000;

export function useProblemContext(
  sessionId: string | null,
  painId: string | null,
  stateVersion: number,
  enabled: boolean,
) {
  const [result, setResult] = useState<ConnectedProblemBrief>(EMPTY_RESULT);
  const [status, setStatus] = useState<'idle' | 'searching' | 'ready' | 'unavailable'>('idle');
  const [responseMs, setResponseMs] = useState(0);

  useEffect(() => {
    if (!enabled || !sessionId || !painId) {
      setResult(EMPTY_RESULT);
      setStatus('idle');
      return;
    }
    const controller = new AbortController();
    const startedAt = performance.now();
    setStatus('searching');
    const deadline = window.setTimeout(() => {
      controller.abort();
      setResult(EMPTY_RESULT);
      setResponseMs(Math.round(performance.now() - startedAt));
      setStatus('unavailable');
    }, DEADLINE_MS);
    const timer = window.setTimeout(() => {
      void searchProblemContext({
        session_id: sessionId,
        pain_id: painId,
        state_version: stateVersion,
        connector_ids: [],
        limit: 5,
      }, controller.signal).then((next) => {
        if (controller.signal.aborted) return;
        window.clearTimeout(deadline);
        if (next.status === 'stale' || next.status === 'problem_not_found') {
          setResult(EMPTY_RESULT);
          setStatus('idle');
          return;
        }
        setResult(next);
        setResponseMs(Math.round(performance.now() - startedAt));
        setStatus(next.status === 'unavailable' || next.status === 'timeout'
          ? 'unavailable'
          : 'ready');
      }).catch(() => {
        if (controller.signal.aborted) return;
        window.clearTimeout(deadline);
        setResult(EMPTY_RESULT);
        setStatus('unavailable');
      });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      window.clearTimeout(deadline);
      controller.abort();
    };
  }, [enabled, painId, sessionId, stateVersion]);

  return { result, status, responseMs };
}
