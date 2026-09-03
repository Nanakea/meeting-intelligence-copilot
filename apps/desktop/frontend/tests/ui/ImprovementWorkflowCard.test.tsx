import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ImprovementWorkflowCard } from '@/components/MeetingDetails/ImprovementWorkflowCard';
import type { ImprovementProposal } from '@/services/intelligenceContracts';

const proposal: ImprovementProposal = {
  proposal_id: 'internal-proposal-id',
  fingerprint: 'internal-fingerprint',
  kind: 'retrieval_weights',
  title: 'Tune evidence ranking',
  rationale: 'Reviewed citations support this bounded change.',
  changes: {},
  signal_count: 120,
  signal_snapshot_hash: 'signal-hash',
  train_snapshot_hash: 'train-hash',
  holdout_snapshot_hash: 'holdout-hash',
  independent_corpus_hash: 'corpus-hash',
  base_profile_revision: 0,
  status: 'pending_evaluation',
  revision: 1,
  created_at: '2026-08-31T00:00:00Z',
  updated_at: '2026-08-31T00:00:00Z',
};

function callbacks() {
  return {
    onEvaluate: vi.fn(),
    onApprove: vi.fn(),
    onRunShadow: vi.fn(),
    onReject: vi.fn(),
  };
}

test('shows only valid actions and never renders internal identifiers', async () => {
  const actions = callbacks();
  const user = userEvent.setup();
  render(
    <ImprovementWorkflowCard proposal={proposal} isJapanese={false} {...actions} />,
  );
  await user.click(screen.getByRole('button', { name: 'Evaluate' }));
  expect(actions.onEvaluate).toHaveBeenCalledWith(proposal);
  expect(screen.queryByText(proposal.proposal_id)).not.toBeInTheDocument();
  expect(screen.queryByText(proposal.fingerprint)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Run shadow comparison' })).not.toBeInTheDocument();
});

test('shadow action is keyboard accessible and localized', async () => {
  const actions = callbacks();
  const user = userEvent.setup();
  const shadow = { ...proposal, status: 'approved_shadow' as const, revision: 3 };
  render(
    <ImprovementWorkflowCard proposal={shadow} isJapanese {...actions} />,
  );
  const button = screen.getByRole('button', { name: 'シャドー比較を実行' });
  button.focus();
  await user.keyboard('{Enter}');
  expect(actions.onRunShadow).toHaveBeenCalledWith(shadow);
  expect(screen.getByLabelText('改善操作')).toBeInTheDocument();
});
