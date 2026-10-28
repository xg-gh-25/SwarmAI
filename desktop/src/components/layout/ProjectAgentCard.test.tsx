/**
 * ProjectAgentCard tests — the Brain Hub review card of a Project Agent (design
 * 2026-10-28-project-agent-flow §5). Checks: the verdict and every part of the
 * definition are shown, with sources that open their file; rows marked for deletion,
 * ticked aliases and the Hive choice go to confirm with the revision shown; a 409
 * says the definition changed; a reviewed definition is read-only; the Runner Agent
 * confirms through its own endpoint (brain null).
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const invalidate = vi.fn();
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: invalidate }) }));
const confirmProjectAgent = vi.fn();
vi.mock('../../services/ddd', () => ({ confirmProjectAgent: (...a: unknown[]) => confirmProjectAgent(...a) }));

import { ProjectAgentCard } from './ProjectAgentCard';

const AGENT = {
  path: 'project-agent.yaml', schema: 2, status: 'draft' as const, revision: 'abcdef1234567890',
  identity: { summary: 'A signing library', inScope: [], outOfScope: ['encryption'], source: 'PRODUCT.md' },
  intake: { handles: ['fix a signer bug', 'deprecate an API'], repos: ['lib'],
    proposedAliases: [{ name: 'signer', source: 'PRODUCT.md' }, { name: 'tok', source: 'TECH.md' }] },
  reviewFocus: [{ area: 'security', why: 'constant-time compare', source: 'IMPROVEMENT.md' }],
  reservedForHuman: ['open_pr', 'push'], gates: ['no_git_push'], placements: ['chat-tab'],
  modes: [
    { id: 'code-change', when: 'a change to the library code', profile: 'bugfix',
      profileRules: [{ when: 'a public API change', profile: 'full' }], placements: ['chat-tab'], repos: ['lib'],
      setup: [{ command: 'uv sync', source: '.github/workflows/ci.yml', repo: 'lib' }],
      validations: [{ id: 'tests', command: 'pytest -q', source: '.github/workflows/ci.yml', repo: 'lib', cwd: 'src' }],
      skills: ['s_release'], delivery: 'bundle', reservedForHuman: ['open_pr', 'push'], gates: ['no_git_push'] },
    { id: 'research', when: 'a question', profile: 'research', profileRules: [], placements: ['chat-tab'], repos: [],
      setup: [], validations: [], skills: [], delivery: 'bundle', reservedForHuman: ['open_pr', 'push'], gates: [] },
  ],
  defaultMode: 'code-change', problems: [], notes: [], sourceCheck: null,
  sourcePaths: { 'PRODUCT.md': '/b/2-understanding/PRODUCT.md', 'lib:.github/workflows/ci.yml': '/r/lib/.github/workflows/ci.yml' },
};

beforeEach(() => { cleanup(); invalidate.mockReset(); confirmProjectAgent.mockReset(); });

describe('ProjectAgentCard', () => {
  it('shows the verdict, what it takes on, its modes and the shared rules', () => {
    render(<ProjectAgentCard brain="Lib" agent={AGENT} onRevise={vi.fn()} />);
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('draft');
    expect(screen.getByTestId('pa-revision').textContent).toContain('abcdef1');
    const def = screen.getByTestId('bm-agent-def').textContent ?? '';
    for (const s of ['A signing library', 'encryption', 'fix a signer bug', 'code-change', 'default', 'bugfix',
      'full', 'research', 'open a PR', 'push', 'no_git_push', 'security', 'constant-time compare', 'signer']) {
      expect(def).toContain(s);
    }
    expect(screen.queryByTestId('pa-mode-detail-code-change')).toBeNull();      // behind Details
    fireEvent.click(screen.getByTestId('pa-mode-more-code-change'));
    const detail = screen.getByTestId('pa-mode-detail-code-change').textContent ?? '';
    for (const s of ['uv sync', 'pytest -q', 'in src', 's_release', 'Repos: lib']) expect(detail).toContain(s);
  });

  it('a source opens its file', () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail.path);
    document.addEventListener('swarm:open-file', on);
    render(<ProjectAgentCard brain="Lib" agent={AGENT} onRevise={vi.fn()} />);
    fireEvent.click(screen.getAllByText('PRODUCT.md', { selector: 'button' })[0]);
    fireEvent.click(screen.getByTestId('pa-open-file'));
    document.removeEventListener('swarm:open-file', on);
    expect(seen).toEqual(['/b/2-understanding/PRODUCT.md', 'Projects/Lib/project-agent.yaml']);
  });

  it('confirm sends the deletions, the ticked aliases and the Hive choice with the revision shown', async () => {
    confirmProjectAgent.mockResolvedValue({ revision: 'new', reviewedAt: 't', reviewedBy: 'me', aliasesAdded: ['tok'],
      sourceCheck: { ok: true, missing: [] } });
    render(<ProjectAgentCard brain="Lib" agent={AGENT} onRevise={vi.fn()} />);
    fireEvent.click(screen.getByTestId('pa-del-intake.handles[1]'));
    fireEvent.click(screen.getByTestId('pa-del-modes[code-change].profile_rules[0]'));
    fireEvent.click(screen.getByTestId('pa-del-modes[code-change].profile_rules[0]'));   // undo
    fireEvent.click(screen.getByTestId('pa-del-gates[no_git_push]'));
    fireEvent.click(screen.getByTestId('pa-del-modes[research]'));
    expect(screen.queryByTestId('pa-del-modes[code-change]')).toBeNull();               // the default mode stays
    fireEvent.click(screen.getByTestId('pa-alias-tok'));
    fireEvent.click(screen.getByTestId('pa-hive'));
    expect(screen.getByTestId('bm-agent-confirm').textContent).toBe('Delete 3 and confirm');
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(screen.getByTestId('pa-confirmed')).toBeTruthy());
    expect(confirmProjectAgent).toHaveBeenCalledWith('Lib', {
      revision: 'abcdef1234567890', allow_hive: true, aliases: ['tok'],
      remove: ['intake.handles[1]', 'gates[no_git_push]', 'modes[research]'],
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['brain-detail', 'Lib'] });
  });

  it('a definition changed since it was shown is not confirmed', async () => {
    confirmProjectAgent.mockRejectedValue({ response: { status: 409, data: { detail: 'changed' } } });
    render(<ProjectAgentCard brain="Lib" agent={AGENT} onRevise={vi.fn()} />);
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(screen.getByTestId('pa-error').textContent).toContain('changed since it was shown'));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['brain-detail', 'Lib'] });   // fetch the current version
  });

  it('what a mode adds on top of the shared rules is shown and can be deleted', async () => {
    confirmProjectAgent.mockResolvedValue({ revision: 'n', reviewedAt: 't', reviewedBy: 'me', aliasesAdded: [], sourceCheck: { ok: true, missing: [] } });
    const research = { ...AGENT.modes[1], reservedForHuman: ['open_pr', 'push', 'publish_release'], gates: ['extra_gate'] };
    render(<ProjectAgentCard brain="Lib" agent={{ ...AGENT, modes: [AGENT.modes[0], research] }} onRevise={vi.fn()} />);
    const block = screen.getByTestId('pa-mode-research').textContent ?? '';
    expect(block).toContain('publish a release');
    expect(block).toContain('extra_gate');
    expect(screen.getByTestId('pa-mode-code-change').textContent).not.toContain('also');   // nothing beyond shared
    fireEvent.click(screen.getByTestId('pa-del-modes[research].gates[extra_gate]'));
    fireEvent.click(screen.getByTestId('pa-del-modes[research].reserved_for_human[publish_release]'));
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(confirmProjectAgent).toHaveBeenCalled());
    expect(confirmProjectAgent.mock.calls[0][1].remove).toEqual(
      ['modes[research].gates[extra_gate]', 'modes[research].reserved_for_human[publish_release]']);
  });

  it('problems from the backend are shown', async () => {
    confirmProjectAgent.mockRejectedValue({ response: { status: 422, data: { detail: { message: 'not valid', problems: ['modes[x] names skills that do not exist'] } } } });
    render(<ProjectAgentCard brain="Lib" agent={AGENT} onRevise={vi.fn()} />);
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(screen.getByTestId('pa-error').textContent).toContain('names skills that do not exist'));
  });

  it('a reviewed definition is read-only; revise still goes to chat', () => {
    const onRevise = vi.fn();
    render(<ProjectAgentCard brain="Lib" agent={{ ...AGENT, status: 'reviewed', reviewedAt: '2026-10-29', reviewedBy: 'me' }} onRevise={onRevise} />);
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('reviewed · 2026-10-29 by me');
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();
    expect(screen.queryByTestId('pa-del-intake.handles[0]')).toBeNull();
    expect(screen.queryByTestId('pa-aliases')).toBeNull();
    fireEvent.change(screen.getByTestId('bm-agent-note'), { target: { value: 'add a docs mode' } });
    fireEvent.click(screen.getByTestId('bm-agent-revise'));
    expect(onRevise).toHaveBeenCalledWith('add a docs mode');
  });

  it('the Runner Agent confirms through its own endpoint', async () => {
    confirmProjectAgent.mockResolvedValue({ revision: 'n', reviewedAt: 't', reviewedBy: 'me', aliasesAdded: [], sourceCheck: { ok: true, missing: [] } });
    render(<ProjectAgentCard brain={null} agent={{ ...AGENT, intake: { handles: [], repos: [] } }} onRevise={vi.fn()} />);
    expect(screen.getByTestId('bm-agent-def').textContent).toContain('Runner Agent');
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(confirmProjectAgent).toHaveBeenCalled());
    expect(confirmProjectAgent.mock.calls[0][0]).toBeNull();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['runner-agent'] }));
  });

  it('a failed source check and notes are visible', () => {
    render(<ProjectAgentCard brain="Lib" agent={{ ...AGENT, notes: ['skill s_x is desktop-only'],
      sourceCheck: { ok: false, checked_at: 't', missing: ['modes[code-change].validations: source ci.yml not found'] } }} onRevise={vi.fn()} />);
    expect(screen.getByTestId('pa-notes').textContent).toContain('desktop-only');
    expect(screen.getByTestId('pa-source-check').textContent).toContain('Not sent to a Hive');
  });
});
