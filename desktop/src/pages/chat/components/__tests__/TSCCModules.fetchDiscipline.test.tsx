/**
 * Regression tests for TSCC panel fetch discipline and the degraded banner.
 *
 * Two defects from the review of the TSCC recall rework (run_abab234c):
 *
 * 1. The shell prefetched the security scan on mount for the summary strip while
 *    SecurityTab fetched it again on tab open — so opening Security ran the scan
 *    twice, and merely opening the panel ran it once even for someone who only
 *    looks at the Flow tab. That scan is not a cached read: it regexes the whole
 *    assembled prompt through every credential detector, and the endpoint's own
 *    contract says it runs when the user opens the security panel.
 * 2. The fail-loud `degraded` signal reached no consumer, so a prompt that
 *    assembled without part of its core context looked healthy in the UI.
 *
 * Properties asserted: the security scan runs zero times until the Security tab
 * is opened, exactly once after, and stays at once across tab switches; the
 * recall snapshot is fetched once and shared by strip and tab; a degraded
 * prompt renders a visible warning.
 *
 * Testing methodology: React Testing Library with the tscc service layer mocked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SystemPromptModule } from '../TSCCModules';
import type { SystemPromptMetadata } from '../../../../types';

vi.mock('../../../../services/tscc', () => ({
  getSystemPromptMetadata: vi.fn(),
  getRecallSnapshot: vi.fn(),
  getSecurityScan: vi.fn(),
}));

import {
  getSystemPromptMetadata,
  getRecallSnapshot,
  getSecurityScan,
} from '../../../../services/tscc';

const metadata: SystemPromptMetadata = {
  files: [{ filename: 'SWARMAI.md', tokens: 500, truncated: false }],
  totalTokens: 500,
  fullText: '# System Prompt',
};

const recallSnapshot = {
  ran: true,
  hits: [
    {
      domain: 'library',
      source: 'context-arch.md',
      score: 0.61,
      hasScore: true,
      method: 'fts',
      text: 'loader is canonical',
    },
  ],
  body: '',
  tokens: 120,
  latencyMs: 48,
  keywords: ['context'],
};

const scanResult = {
  grade: 'A',
  critical: 0,
  high: 0,
  medium: 0,
  info: 1,
  findings: [{ label: 'No plaintext credentials', status: 'pass', detail: '' }],
};

/** Tab buttons must be addressed by role — the summary strip renders the same
 *  words ("Recall", "Security") as stat labels, so a bare text query is
 *  ambiguous. */
const tab = (label: string) =>
  screen.getByRole('button', { name: new RegExp(label) });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getSystemPromptMetadata).mockResolvedValue(metadata);
  vi.mocked(getRecallSnapshot).mockResolvedValue(recallSnapshot as never);
  vi.mocked(getSecurityScan).mockResolvedValue(scanResult as never);
});

describe('TSCC panel fetch discipline', () => {
  it('does not run the security scan just because the panel opened', async () => {
    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);

    // Recall is a cheap snapshot read, so it may load eagerly for the strip.
    await waitFor(() => expect(getRecallSnapshot).toHaveBeenCalledTimes(1));
    expect(getSecurityScan).not.toHaveBeenCalled();
  });

  it('runs the security scan exactly once when the Security tab is opened', async () => {
    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);
    await waitFor(() => expect(getRecallSnapshot).toHaveBeenCalledTimes(1));

    fireEvent.click(tab('Security'));

    await waitFor(() => expect(getSecurityScan).toHaveBeenCalledTimes(1));
    // The strip and the tab share this one result — no second scan.
    await waitFor(() => expect(screen.getAllByText('A').length).toBeGreaterThan(0));
    expect(getSecurityScan).toHaveBeenCalledTimes(1);
  });

  it('does not rescan when leaving and re-entering the Security tab', async () => {
    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);

    fireEvent.click(tab('Security'));
    await waitFor(() => expect(getSecurityScan).toHaveBeenCalledTimes(1));

    fireEvent.click(tab('Flow'));
    fireEvent.click(tab('Security'));

    await waitFor(() => expect(screen.getAllByText('A').length).toBeGreaterThan(0));
    expect(getSecurityScan).toHaveBeenCalledTimes(1);
  });

  it('fetches the recall snapshot once and shares it with the Recall tab', async () => {
    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);
    await waitFor(() => expect(getRecallSnapshot).toHaveBeenCalledTimes(1));

    fireEvent.click(tab('Recall'));

    // Real hits render from the shared snapshot, with no extra request.
    await waitFor(() =>
      expect(screen.getByText('context-arch.md')).toBeInTheDocument(),
    );
    expect(getRecallSnapshot).toHaveBeenCalledTimes(1);
  });
});

describe('TSCC budget gauge', () => {
  it('measures against the model\'s real budget, not a hardcoded 100K', async () => {
    // 45K assembled on a 200K-window model (50K budget) is 90% of the ceiling.
    // Against the old hardcoded 100K it read a comfortable 45%.
    render(
      <SystemPromptModule
        sessionId="s1"
        metadata={{ ...metadata, totalTokens: 45_000, effectiveTokenBudget: 50_000 }}
      />,
    );
    fireEvent.click(tab('Prompt'));

    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('↑ 50K budget')).toBeInTheDocument();
    expect(screen.queryByText('↑ 100K budget')).not.toBeInTheDocument();
  });

  it('flags over-budget against the real ceiling', async () => {
    // 60K would be "in budget" under a 100K assumption; on a 50K budget it is not.
    render(
      <SystemPromptModule
        sessionId="s1"
        metadata={{ ...metadata, totalTokens: 60_000, effectiveTokenBudget: 50_000 }}
      />,
    );
    fireEvent.click(tab('Prompt'));

    expect(screen.getByText('120%')).toBeInTheDocument();
    expect(screen.getByText('no cut · warn')).toBeInTheDocument();
  });

  it('says unknown rather than inventing a ceiling when none was reported', async () => {
    render(
      <SystemPromptModule
        sessionId="s1"
        metadata={{ ...metadata, totalTokens: 45_000, effectiveTokenBudget: 0 }}
      />,
    );
    fireEvent.click(tab('Prompt'));

    expect(
      screen.getByText(/Budget not reported for this session/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/in budget/)).not.toBeInTheDocument();
  });

  it('shows the real budget in the summary strip', async () => {
    render(
      <SystemPromptModule
        sessionId="s1"
        metadata={{ ...metadata, effectiveTokenBudget: 30_000 }}
      />,
    );
    expect(screen.getByText('30K')).toBeInTheDocument();
  });
});

describe('TSCC recall miss', () => {
  it('reports "ran but matched nothing" instead of "no recall ran"', async () => {
    vi.mocked(getRecallSnapshot).mockResolvedValue({
      ran: true,
      hits: [],
      body: '',
      tokens: 0,
      latencyMs: 41,
      keywords: ['evolution', 'pipeline'],
    } as never);

    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);
    await waitFor(() => expect(getRecallSnapshot).toHaveBeenCalledTimes(1));
    fireEvent.click(tab('Recall'));

    await waitFor(() =>
      expect(screen.getByText(/Recall ran but matched nothing/)).toBeInTheDocument(),
    );
    // The keywords that missed are the actionable part — keep them visible.
    expect(screen.getByText('evolution')).toBeInTheDocument();
    expect(screen.queryByText(/No recall ran this session/)).not.toBeInTheDocument();
  });

  it('still distinguishes a session where recall never ran', async () => {
    vi.mocked(getRecallSnapshot).mockResolvedValue({
      ran: false, hits: [], body: '', tokens: 0, latencyMs: 0, keywords: [],
    } as never);

    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);
    await waitFor(() => expect(getRecallSnapshot).toHaveBeenCalledTimes(1));
    fireEvent.click(tab('Recall'));

    await waitFor(() =>
      expect(screen.getByText(/No recall ran this session/)).toBeInTheDocument(),
    );
  });
});

describe('TSCC degraded banner', () => {
  it('surfaces a degraded prompt assembly', async () => {
    render(
      <SystemPromptModule
        sessionId="s1"
        metadata={{ ...metadata, degraded: 'missing_core_sections: SOUL' }}
      />,
    );

    expect(screen.getByText('Prompt assembled incomplete')).toBeInTheDocument();
    expect(
      screen.getByText('missing_core_sections: SOUL'),
    ).toBeInTheDocument();
  });

  it('shows no banner for a complete assembly', async () => {
    render(<SystemPromptModule sessionId="s1" metadata={metadata} />);

    expect(
      screen.queryByText('Prompt assembled incomplete'),
    ).not.toBeInTheDocument();
  });
});
