import { useEffect, useState } from 'react';

import { searchQuestionContext } from '@/services/contextConnectorService';
import type { ConnectedQuestionBrief } from '@/services/intelligenceContracts';

const EMPTY_RESULT: ConnectedQuestionBrief = {
  status: 'current',
  state_version: 0,
  suggestion_id: '',
  citations: [],
  unavailable_connector_ids: [],
};
const QUESTION_CONTEXT_DEADLINE_MS = 2_000;

export function useQuestionContext(
  sessionId: string | null,
  suggestionId: string | null,
  stateVersion: number,
  enabled: boolean,
) {
  const [result, setResult] = useState<ConnectedQuestionBrief>(EMPTY_RESULT);
  const [status, setStatus] = useState<'idle' | 'searching' | 'ready' | 'unavailable'>('idle');
  const [responseMs, setResponseMs] = useState(0);

  useEffect(() => {
    if (!enabled || !sessionId || !suggestionId) {
      setResult(EMPTY_RESULT);
      setStatus('idle');
      return;
    }
    const controller = new AbortController();
    const startedAt = performance.now();
    setStatus('searching');
    const deadline = setTimeout(() => {
      controller.abort();
      setResult(EMPTY_RESULT);
      setResponseMs(Math.round(performance.now() - startedAt));
      setStatus('unavailable');
    }, QUESTION_CONTEXT_DEADLINE_MS);
    const timer = setTimeout(() => {
      void searchQuestionContext({
        session_id: sessionId,
        suggestion_id: suggestionId,
        state_version: stateVersion,
        connector_ids: [],
        limit: 5,
      }, controller.signal)
        .then((next) => {
          if (controller.signal.aborted) return;
          clearTimeout(deadline);
          if (next.status === 'stale') {
            setResult(EMPTY_RESULT);
            setStatus('idle');
            return;
          }
          setResult(next);
          setResponseMs(Math.round(performance.now() - startedAt));
          setStatus(next.status === 'unavailable' ? 'unavailable' : 'ready');
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          clearTimeout(deadline);
          setResult(EMPTY_RESULT);
          setStatus('unavailable');
        });
    }, 350);

    return () => {
      clearTimeout(timer);
      clearTimeout(deadline);
      controller.abort();
    };
  }, [enabled, sessionId, stateVersion, suggestionId]);

  return { result, status, responseMs };
}
