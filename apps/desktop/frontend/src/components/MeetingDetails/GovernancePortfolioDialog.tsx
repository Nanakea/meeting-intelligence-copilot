'use client';

import { useEffect, useState } from 'react';
import { Landmark, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import type {
  GovernanceCandidate,
  GovernanceRecord,
  SolutionPortfolio,
} from '@/services/intelligenceContracts';
import {
  changeGovernanceRecord,
  exportGovernanceReviewPack,
  getGovernanceCandidates,
  getGovernancePortfolio,
  reviewGovernanceCandidate,
  type GovernanceExportFormat,
} from '@/services/governanceService';

const EMPTY_PORTFOLIO: SolutionPortfolio = { records: [], alerts: [], counts: {} };

async function loadGovernanceWorkspace(meetingId: string) {
  const [portfolio, candidates] = await Promise.all([
    getGovernancePortfolio(),
    getGovernanceCandidates(meetingId),
  ]);
  return {
    portfolio,
    candidates: candidates.filter((candidate) => candidate.status === 'pending'),
  };
}

function recordDetail(record: GovernanceRecord): string {
  const payload = record.payload;
  if ('statement' in payload) return payload.statement;
  if ('task' in payload) return payload.task;
  if ('change' in payload) return payload.change;
  return payload.subject;
}

export function GovernancePortfolioDialog({ meetingId }: { meetingId: string }) {
  const [open, setOpen] = useState(false);
  const [portfolio, setPortfolio] = useState(EMPTY_PORTFOLIO);
  const [candidates, setCandidates] = useState<GovernanceCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [currentMeetingOnly, setCurrentMeetingOnly] = useState(false);
  const [format, setFormat] = useState<GovernanceExportFormat>('zip');

  const visibleRecords = currentMeetingOnly
    ? portfolio.records.filter((record) => record.source_session_ids.includes(meetingId))
    : portfolio.records;

  const refresh = async () => {
    setLoading(true);
    try {
      const next = await loadGovernanceWorkspace(meetingId);
      setPortfolio(next.portfolio);
      setCandidates(next.candidates);
    } catch {
      toast.error('Could not load the local governance workspace');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void loadGovernanceWorkspace(meetingId)
      .then((next) => {
        if (cancelled) return;
        setPortfolio(next.portfolio);
        setCandidates(next.candidates);
      })
      .catch(() => {
        if (!cancelled) toast.error('Could not load the local governance workspace');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, open]);

  const resolve = async (record: GovernanceRecord) => {
    try {
      await changeGovernanceRecord(record.record_id, record.revision, 'status', {
        status: 'resolved',
      });
      await refresh();
    } catch {
      toast.error('The record changed. Refresh and try again.');
    }
  };

  const reviewCandidate = async (
    candidate: GovernanceCandidate,
    action: 'confirm' | 'dismiss',
  ) => {
    try {
      await reviewGovernanceCandidate(candidate.candidate_id, {
        session_id: candidate.session_id,
        state_version: candidate.state_version,
        action,
      });
      await refresh();
    } catch {
      toast.error('The candidate changed. Refresh and try again.');
    }
  };

  const exportRecords = async () => {
    if (visibleRecords.length === 0) return;
    try {
      await exportGovernanceReviewPack(visibleRecords, format);
    } catch {
      toast.error('Could not export the governance review pack');
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Landmark size={16} /> Governance
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Solution governance workspace</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-slate-600">
          Only user-confirmed, transcript-backed records appear here. AI summaries and connected
          references do not create governance records.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Refresh
          </Button>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={currentMeetingOnly}
              onChange={(event) => setCurrentMeetingOnly(event.target.checked)}
            />
            This meeting only
          </label>
          <select
            value={format}
            onChange={(event) => setFormat(event.target.value as GovernanceExportFormat)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
            aria-label="Governance export format"
          >
            <option value="zip">Complete review ZIP</option>
            <option value="raid_csv">RAID CSV</option>
            <option value="dependency_csv">Dependency CSV</option>
            <option value="json">Canonical JSON</option>
          </select>
          <Button size="sm" onClick={() => void exportRecords()} disabled={visibleRecords.length === 0}>
            Export reviewed records
          </Button>
        </div>

        {portfolio.alerts.length > 0 && (
          <section className="rounded-xl border border-amber-200 bg-amber-50 p-3" aria-label="Governance alerts">
            <h3 className="text-sm font-semibold text-amber-950">Needs attention</h3>
            <ul className="mt-2 space-y-1 text-sm text-amber-900">
              {portfolio.alerts.slice(0, 10).map((alert) => (
                <li key={alert.alert_id}>{alert.label}</li>
              ))}
            </ul>
          </section>
        )}


        {candidates.length > 0 && (
          <section className="space-y-3" aria-label="Post-meeting governance review">
            <div>
              <h3 className="font-semibold text-slate-950">Review meeting candidates</h3>
              <p className="text-sm text-slate-600">
                These transcript-backed proposals remain temporary until you confirm them.
              </p>
            </div>
            {candidates.map((candidate) => (
              <article key={candidate.candidate_id} className="rounded-xl border border-sky-200 bg-sky-50 p-4">
                <p className="text-[11px] font-bold uppercase tracking-wide text-sky-700">
                  {candidate.kind.replaceAll('_', ' ')} candidate
                </p>
                <h4 className="mt-1 break-words font-semibold text-slate-950">{candidate.title}</h4>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => void reviewCandidate(candidate, 'confirm')}>
                    Confirm record
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void reviewCandidate(candidate, 'dismiss')}>
                    Dismiss
                  </Button>
                </div>
              </article>
            ))}
          </section>
        )}

        <div className="space-y-3">
          {visibleRecords.map((record) => (
            <article key={record.record_id} className="rounded-xl border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                    {record.kind.replaceAll('_', ' ')} · {record.status}
                  </p>
                  <h3 className="mt-1 break-words font-semibold text-slate-950">{record.title}</h3>
                  <p className="mt-2 break-words text-sm text-slate-700">{recordDetail(record)}</p>
                </div>
                {record.status !== 'resolved' && record.status !== 'superseded' && (
                  <Button variant="outline" size="sm" onClick={() => void resolve(record)}>
                    Mark resolved
                  </Button>
                )}
              </div>
              <p className="mt-3 text-xs text-slate-500">
                Revision {record.revision}. Content is labelled by transcript-derived and user-edited field provenance.
              </p>
            </article>
          ))}
          {!loading && visibleRecords.length === 0 && (
            <p className="rounded-xl border border-dashed p-4 text-sm text-slate-500">
              No confirmed governance records yet.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
