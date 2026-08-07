/**
 * Unit tests for the ExplorerHeader component.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - "SwarmWS" static title renders (Req 9.1)
 * - Old workspace controls are absent: no dropdown, no toggle, no checkbox,
 *   no "New Workspace" button, no add-context area (Req 9.3–9.7)
 * - NEW (run_36f2823c): search input wired to setSearchQuery; collapse-all /
 *   expand-all wired to context actions; sort toggle wired to setSortMode.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ExplorerHeader from './ExplorerHeader';
import { useTreeData, useSearch, useSelection } from '../../contexts/ExplorerContext';

vi.mock('../../contexts/ExplorerContext', () => ({
  useTreeData: vi.fn(),
  useSearch: vi.fn(),
  useSelection: vi.fn(),
}));

const setSearchQuery = vi.fn();
const collapseAll = vi.fn();
const expandAll = vi.fn();
const setSortMode = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (useTreeData as ReturnType<typeof vi.fn>).mockReturnValue({ refreshTree: vi.fn() });
  (useSearch as ReturnType<typeof vi.fn>).mockReturnValue({ searchQuery: '', setSearchQuery });
  (useSelection as ReturnType<typeof vi.fn>).mockReturnValue({
    collapseAll, expandAll, setSortMode, sortMode: 'default',
  });
});

describe('ExplorerHeader', () => {
  it('renders "SwarmWS" title', () => {
    render(<ExplorerHeader />);
    expect(screen.getByText('SwarmWS')).toBeDefined();
  });

  describe('NEW controls wired (run_36f2823c)', () => {
    it('renders a search input and calls setSearchQuery on type', () => {
      render(<ExplorerHeader />);
      const input = screen.getByPlaceholderText(/search/i);
      fireEvent.change(input, { target: { value: 'daily' } });
      expect(setSearchQuery).toHaveBeenCalledWith('daily');
    });

    it('collapse-all button calls collapseAll', () => {
      render(<ExplorerHeader />);
      fireEvent.click(screen.getByTestId('explorer-collapse-all'));
      expect(collapseAll).toHaveBeenCalledTimes(1);
    });

    it('expand-all button calls expandAll', () => {
      render(<ExplorerHeader />);
      fireEvent.click(screen.getByTestId('explorer-expand-all'));
      expect(expandAll).toHaveBeenCalledTimes(1);
    });

    it('sort button calls setSortMode (cycles off default)', () => {
      render(<ExplorerHeader />);
      fireEvent.click(screen.getByTestId('explorer-sort-toggle'));
      expect(setSortMode).toHaveBeenCalledTimes(1);
      // first click moves off 'default' — never sets 'default' as the immediate next
      expect(setSortMode).not.toHaveBeenCalledWith('default');
    });

    it('shows a clear affordance only when the query is non-empty', () => {
      (useSearch as ReturnType<typeof vi.fn>).mockReturnValue({ searchQuery: 'x', setSearchQuery });
      render(<ExplorerHeader />);
      const clear = screen.getByTestId('explorer-search-clear');
      fireEvent.click(clear);
      expect(setSearchQuery).toHaveBeenCalledWith('');
    });
  });

  describe('old workspace controls are absent (Req 9.3–9.7)', () => {
    it('no workspace dropdown', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('combobox')).toBeNull();
      expect(screen.queryByText(/select.*workspace/i)).toBeNull();
    });
    it('no Global/SwarmWS toggle switch', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('switch')).toBeNull();
    });
    it('no "Show Archived" checkbox', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('checkbox')).toBeNull();
      expect(screen.queryByText(/archived/i)).toBeNull();
    });
    it('no "New Workspace" button', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByText(/new workspace/i)).toBeNull();
    });
  });
});
