/**
 * Unit tests for the TSCCSnapshotCard component.
 *
 * Tests the snapshot card including:
 * - Collapsed by default showing timestamp and reason
 * - Expands on click to show all snapshot fields
 * - Agents, capabilities, sources, activity, and summary displayed correctly
 *
 * Testing methodology: unit tests with React Testing Library.
 */

import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TSCCSnapshotCard } from '../TSCCSnapshotCard';
import type { TSCCSnapshot } from '../../../../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const makeSampleSnapshot = (
  overrides: Partial<TSCCSnapshot> = {},
): TSCCSnapshot => ({
  snapshotId: 'snap-001',
  threadId: 'thread-1',
  timestamp: '2026-02-26T10:30:00Z',
  reason: 'Plan decomposition completed',
  lifecycleState: 'active',
  activeAgents: ['SwarmAgent', 'CodeAgent'],
  activeCapabilities: {
    skills: ['code-review'],
    mcps: ['filesystem'],
    tools: ['search'],
  },
  whatAiDoing: ['Analyzing code structure'],
  activeSources: [
    { path: 'src/main.py', origin: 'Project' },
    { path: 'docs/README.md', origin: 'Library' },
  ],
  keySummary: ['Initial analysis complete', 'Found 3 issues'],
  ...overrides,
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('TSCCSnapshotCard', () => {
  it('renders collapsed by default with timestamp and reason', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    expect(
      screen.getByText('Plan decomposition completed'),
    ).toBeInTheDocument();
    // Timestamp is formatted via toLocaleString — just check the button exists
    expect(
      screen.getByRole('button', { expanded: false }),
    ).toBeInTheDocument();
  });

  it('does not show expanded details by default', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    expect(screen.queryByText('Agents')).not.toBeInTheDocument();
    expect(screen.queryByText('Capabilities')).not.toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    expect(screen.queryByText('Activity')).not.toBeInTheDocument();
    expect(screen.queryByText('Summary')).not.toBeInTheDocument();
  });

  it('expands on click to show all sections', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('Agents')).toBeInTheDocument();
    expect(screen.getByText('Capabilities')).toBeInTheDocument();
    expect(screen.getByText('Sources')).toBeInTheDocument();
    expect(screen.getByText('Activity')).toBeInTheDocument();
    expect(screen.getByText('Summary')).toBeInTheDocument();
  });

  it('displays agent names when expanded', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('SwarmAgent')).toBeInTheDocument();
    expect(screen.getByText('CodeAgent')).toBeInTheDocument();
  });

  it('displays grouped capabilities when expanded', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('Skills: code-review')).toBeInTheDocument();
    expect(screen.getByText('MCPs: filesystem')).toBeInTheDocument();
    expect(screen.getByText('Tools: search')).toBeInTheDocument();
  });

  it('displays sources with origin tags when expanded', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('src/main.py')).toBeInTheDocument();
    expect(screen.getByText('Project')).toBeInTheDocument();
    expect(screen.getByText('docs/README.md')).toBeInTheDocument();
    expect(screen.getByText('Library')).toBeInTheDocument();
  });

  it('displays activity items when expanded', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(
      screen.getByText('Analyzing code structure'),
    ).toBeInTheDocument();
  });

  it('displays key summary items when expanded', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    fireEvent.click(screen.getByRole('button'));
    expect(
      screen.getByText('Initial analysis complete'),
    ).toBeInTheDocument();
    expect(screen.getByText('Found 3 issues')).toBeInTheDocument();
  });

  it('collapses again on second click', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(screen.getByText('Agents')).toBeInTheDocument();
    fireEvent.click(btn);
    expect(screen.queryByText('Agents')).not.toBeInTheDocument();
  });

  it('hides agents section when no agents', () => {
    const snap = makeSampleSnapshot({ activeAgents: [] });
    render(<TSCCSnapshotCard snapshot={snap} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByText('Agents')).not.toBeInTheDocument();
  });

  it('hides capabilities section when all empty', () => {
    const snap = makeSampleSnapshot({
      activeCapabilities: { skills: [], mcps: [], tools: [] },
    });
    render(<TSCCSnapshotCard snapshot={snap} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByText('Capabilities')).not.toBeInTheDocument();
  });

  it('hides sources section when no sources', () => {
    const snap = makeSampleSnapshot({ activeSources: [] });
    render(<TSCCSnapshotCard snapshot={snap} />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
  });

  it('has correct aria-label with reason', () => {
    render(<TSCCSnapshotCard snapshot={makeSampleSnapshot()} />);
    expect(
      screen.getByRole('article', {
        name: 'Snapshot: Plan decomposition completed',
      }),
    ).toBeInTheDocument();
  });
});
