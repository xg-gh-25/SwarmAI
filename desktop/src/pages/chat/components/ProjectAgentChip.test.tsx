/**
 * ProjectAgentChip — the session's Brain next to the TSCC button.
 *
 * Questions, one per test:
 * - does the metadata mapper read ``project_agent`` from the backend payload (the SSE
 *   event and the HTTP route share it), and drop a value that names no Brain?
 * - is nothing shown when the session works under no Brain?
 * - does the chip name the Brain, tell guidance from an enforced pin, and open that
 *   Brain in Brain Hub (the General Brain: Brain Hub itself)?
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { ProjectAgentChip } from './ProjectAgentChip';
import { promptMetadataToCamelCase } from '../../../services/tscc';

afterEach(() => { cleanup(); });

/** The detail of each swarm:show-brain-hub event dispatched while *fn* runs. */
function brainHubOpens(fn: () => void): unknown[] {
  const seen: unknown[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail ?? null);
  window.addEventListener('swarm:show-brain-hub', on);
  try { fn(); } finally { window.removeEventListener('swarm:show-brain-hub', on); }
  return seen;
}

describe('ProjectAgentChip', () => {
  it('maps project_agent from the backend payload', () => {
    expect(promptMetadataToCamelCase({ project_agent: { brain: 'SwarmAI', pinned: false, mode: '' } }).projectAgent)
      .toEqual({ brain: 'SwarmAI', pinned: false, mode: '' });
    expect(promptMetadataToCamelCase({ project_agent: { brain: 'Demo', pinned: true, mode: 'research' } }).projectAgent)
      .toEqual({ brain: 'Demo', pinned: true, mode: 'research' });
    expect(promptMetadataToCamelCase({ project_agent: null }).projectAgent).toBeNull();
    expect(promptMetadataToCamelCase({ project_agent: { brain: '' } }).projectAgent).toBeNull();
    expect(promptMetadataToCamelCase({}).projectAgent).toBeNull();
  });

  it('shows nothing without a Brain', () => {
    const { container } = render(<ProjectAgentChip fact={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the Brain and opens it in Brain Hub', () => {
    render(<ProjectAgentChip fact={{ brain: 'SwarmAI', pinned: false, mode: '' }} />);
    const chip = screen.getByTestId('project-agent-chip');
    expect(chip).toHaveTextContent('SwarmAI');
    expect(chip.getAttribute('title')).toMatch(/guidance/);
    expect(brainHubOpens(() => fireEvent.click(chip))).toEqual([{ brain: 'SwarmAI' }]);
  });

  it('tells an enforced pin apart, with its mode', () => {
    render(<ProjectAgentChip fact={{ brain: 'SwarmAI', pinned: true, mode: 'code-change' }} />);
    const title = screen.getByTestId('project-agent-chip').getAttribute('title') ?? '';
    expect(title).toMatch(/enforced/);
    expect(title).toContain('code-change');
  });

  it('opens Brain Hub itself for the General Brain', () => {
    render(<ProjectAgentChip fact={{ brain: 'General', pinned: true, mode: 'research' }} />);
    const chip = screen.getByTestId('project-agent-chip');
    expect(chip).toHaveTextContent('General Brain');
    expect(brainHubOpens(() => fireEvent.click(chip))).toEqual([null]);
  });
});
