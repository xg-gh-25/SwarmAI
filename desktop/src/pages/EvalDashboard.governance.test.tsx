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
  id: 'GC14',
  gc_id: 'GC14',
  occurrence_count: 3,
  proposed_rule: 'Code constant change → grep MEMORY/KNOWLEDGE for the old value',
  checked: 'checked against: R30 — new because it names the grep step',
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
    expect(screen.getByText(/Code constant change/)).toBeTruthy();
    expect(container).toBeTruthy();
  });

  it('renders gracefully when evidence field is missing entirely', () => {
    const { id, gc_id, occurrence_count, proposed_rule } = baseProposal;
    const noEvidence = { id, gc_id, occurrence_count, proposed_rule };
    render(<GovernanceProposalCard proposal={noEvidence} onAct={noop} pending={false} />);
    expect(screen.getByText(/Code constant change/)).toBeTruthy();
  });

  it('shows the GC id and what it was checked against', () => {
    render(<GovernanceProposalCard proposal={baseProposal} onAct={noop} pending={false} />);
    expect(screen.getByText('GC14')).toBeTruthy();
    expect(screen.getByText(/checked against: R30/)).toBeTruthy();
  });

  it('accept button fires onAct with this proposal id (identity-safe)', () => {
    const onAct = vi.fn();
    render(<GovernanceProposalCard proposal={baseProposal} onAct={onAct} pending={false} />);
    screen.getByRole('button', { name: /accept/i }).click();
    expect(onAct).toHaveBeenCalledWith('GC14', 'accept');
  });
});
