'use client';

import { Button } from '@/components/ui/button';
import type { ImprovementProposal } from '@/services/intelligenceContracts';

interface ImprovementWorkflowCardProps {
  proposal: ImprovementProposal;
  isJapanese: boolean;
  onEvaluate: (proposal: ImprovementProposal) => void;
  onApprove: (proposal: ImprovementProposal) => void;
  onRunShadow: (proposal: ImprovementProposal) => void;
  onReject: (proposal: ImprovementProposal) => void;
}

export function ImprovementWorkflowCard({
  proposal,
  isJapanese,
  onEvaluate,
  onApprove,
  onRunShadow,
  onReject,
}: ImprovementWorkflowCardProps) {
  const terminal = ['active', 'rejected', 'retired'].includes(proposal.status);
  return (
    <article className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold text-slate-950">{proposal.title}</p>
          <p className="text-xs font-medium uppercase tracking-wide text-teal-700">
            {proposal.kind.replaceAll('_', ' ')} · {proposal.status.replaceAll('_', ' ')}
          </p>
        </div>
        <span className="rounded-full bg-white px-2 py-1 text-xs text-slate-600">
          {proposal.signal_count} {isJapanese ? '件の確認' : 'reviews'}
        </span>
      </div>
      <p className="mt-2 text-sm text-slate-600">{proposal.rationale}</p>
      <div className="mt-3 flex flex-wrap gap-2" aria-label={isJapanese ? '改善操作' : 'Improvement actions'}>
        {['pending_evaluation', 'blocked'].includes(proposal.status) && (
          <Button size="sm" onClick={() => onEvaluate(proposal)}>
            {isJapanese ? '決定論的評価' : 'Evaluate'}
          </Button>
        )}
        {proposal.status === 'evaluated' && (
          <Button size="sm" onClick={() => onApprove(proposal)}>
            {isJapanese ? 'シャドー実行を承認' : 'Approve shadow cycle'}
          </Button>
        )}
        {proposal.status === 'approved_shadow' && (
          <Button size="sm" onClick={() => onRunShadow(proposal)}>
            {isJapanese ? 'シャドー比較を実行' : 'Run shadow comparison'}
          </Button>
        )}
        {!terminal && (
          <Button size="sm" variant="outline" onClick={() => onReject(proposal)}>
            {isJapanese ? '却下' : 'Reject'}
          </Button>
        )}
      </div>
    </article>
  );
}
