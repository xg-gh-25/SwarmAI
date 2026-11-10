/**
 * ProjectAgentView — the person's rules on a Brain's Project Agent page (design
 * docs/design/2026-11-09-project-agent-contract.md §4.4).
 *
 * Questions, one per test:
 * - are the rules listed first, as the person's words with their date, in file order,
 *   with an unverified one marked as reaching no task?
 * - when the Brain has none, is there no list but a line saying how a rule is added?
 * - does the gates line say the gates run only in sessions sent to a Hive?
 */
import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import i18n from '../../i18n';
import type { ProjectAgent } from '../../services/ddd';
import { ProjectAgentView } from './ProjectAgentView';

const base: ProjectAgent = { path: 'project-agent.yaml', summary: 'A Brain', problems: [], modes: [] };

describe('ProjectAgentView rules', () => {
  beforeEach(async () => { await i18n.changeLanguage('en'); });

  it('lists the rules first, in order, and marks an unverified one', () => {
    render(<ProjectAgentView brain="SwarmAI" onRevise={() => {}} agent={{
      ...base,
      rules: [
        { said: '发布说明要放仓库里 不要有内部信息', added: '2026-11-09', verified: true },
        { said: 'written into the file by hand', added: '2026-11-10', verified: false },
      ],
    }} />);
    const list = screen.getByTestId('pa-rules');
    expect(list).toHaveTextContent('Your rules for this Brain (2)');
    const first = screen.getByTestId('pa-rule-0');
    expect(first).toHaveTextContent('发布说明要放仓库里 不要有内部信息');
    expect(first).toHaveTextContent('2026-11-09');
    expect(first).not.toHaveTextContent('not verified');
    expect(within(screen.getByTestId('pa-rule-1')).getByText(/not verified/)).toBeInTheDocument();
    // first on the page: before the summary
    expect(list.compareDocumentPosition(screen.getByTestId('pa-summary')) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it('says how a rule is added when the Brain has none', () => {
    render(<ProjectAgentView brain="SwarmAI" onRevise={() => {}} agent={{ ...base, rules: [] }} />);
    expect(screen.queryByTestId('pa-rules')).toBeNull();
    expect(screen.getByTestId('pa-rules-empty')).toHaveTextContent('from now on');
  });

  it('shows no hint once there is a rule, and says gates run only on a Hive', () => {
    render(<ProjectAgentView brain="SwarmAI" onRevise={() => {}} agent={{
      ...base, gates: ['no_git_push'],
      rules: [{ said: 'x', added: '2026-11-10', verified: true }],
    }} />);
    expect(screen.queryByTestId('pa-rules-empty')).toBeNull();
    expect(screen.getByTestId('pa-gates')).toHaveTextContent('sent to a Hive');
    expect(screen.getByTestId('pa-gates')).toHaveTextContent('no_git_push');
  });
});
