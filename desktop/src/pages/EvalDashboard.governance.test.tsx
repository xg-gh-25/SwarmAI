/**
 * Bug1 (run_685db747): the governance proposal card fetched `evidence[]` but never
 * rendered it, so proposals with the same source_class/kind were visually identical
 * and unjudgeable — the root cause of "clicked A but accepted B". These tests drive
 * the extracted pure GovernanceProposalCard and assert the evidence is visible.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GovernanceProposalCard } from './EvalDashboard';

const baseProposal = {
  id: 'CLASS_A:rule',
  proposal_kind: 'rule' as const,
  source_class: 'CLASS_A',
  occurrence_count: 7,
  proposed_rule: 'Recurring CLASS_A pattern (7x) with no structural fix',
  confidence: 0.6,
};

const noop = () => {};

describe('GovernanceProposalCard — Bug1 evidence render', () => {
  it('renders each evidence entry so the proposal is judgeable', () => {
    const evidence = [
      'Skipped adversarial stage, missing gate enforcement per stage',
      '3 unvalidated subsystems batch-deployed to prod in one push',
    ];
    render(<GovernanceProposalCard proposal={{ ...baseProposal, evidence }} onAct={noop} pending={false} />);
    // both real correction excerpts must appear in the rendered card
    expect(screen.getByText(/Skipped adversarial stage/)).toBeTruthy();
    expect(screen.getByText(/3 unvalidated subsystems/)).toBeTruthy();
  });

  it('renders gracefully when evidence is empty (no crash, muted note)', () => {
    const { container } = render(
      <GovernanceProposalCard proposal={{ ...baseProposal, evidence: [] }} onAct={noop} pending={false} />,
    );
    // still shows the proposal, does not throw
    expect(screen.getByText(/Recurring CLASS_A pattern/)).toBeTruthy();
    expect(container).toBeTruthy();
  });

  it('renders gracefully when evidence field is missing entirely', () => {
    const { id, proposal_kind, source_class, occurrence_count, proposed_rule, confidence } = baseProposal;
    const noEvidence = { id, proposal_kind, source_class, occurrence_count, proposed_rule, confidence };
    render(<GovernanceProposalCard proposal={noEvidence} onAct={noop} pending={false} />);
    expect(screen.getByText(/Recurring CLASS_A pattern/)).toBeTruthy();
  });

  it('accept button fires onAct with this proposal id (identity-safe)', () => {
    const onAct = vi.fn();
    render(<GovernanceProposalCard proposal={baseProposal} onAct={onAct} pending={false} />);
    screen.getByRole('button', { name: /accept/i }).click();
    expect(onAct).toHaveBeenCalledWith('CLASS_A:rule', 'accept');
  });
});
