/**
 * Unit tests for the ExplorerHeader component.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - "SwarmWS" static title renders (Req 9.1)
 * - Old controls are absent: no dropdown, no toggle, no checkbox,
 *   no "New Workspace" button, no add-context area (Req 9.3–9.7)
 *
 * **Validates: Requirements 9.1, 9.3, 9.4, 9.5, 9.6, 9.7**
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ExplorerHeader from './ExplorerHeader';
import { useTreeData } from '../../contexts/ExplorerContext';

// Mock the ExplorerContext hooks
vi.mock('../../contexts/ExplorerContext', () => ({
  useTreeData: vi.fn(),
}));

describe('ExplorerHeader', () => {
  beforeEach(() => {
    (useTreeData as ReturnType<typeof vi.fn>).mockReturnValue({
      refreshTree: vi.fn(),
    });
  });

  it('renders "SwarmWS" title', () => {
    render(<ExplorerHeader />);
    expect(screen.getByText('SwarmWS')).toBeDefined();
  });

  describe('old controls are absent (Req 9.3–9.7)', () => {
    it('does not render a workspace dropdown selector', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('combobox')).toBeNull();
      expect(screen.queryByRole('listbox')).toBeNull();
      expect(screen.queryByText(/select.*workspace/i)).toBeNull();
    });

    it('does not render a Global/SwarmWS toggle switch', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('switch')).toBeNull();
      expect(screen.queryByText(/global/i)).toBeNull();
    });

    it('does not render a "Show Archived Workspaces" checkbox', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('checkbox')).toBeNull();
      expect(screen.queryByText(/archived/i)).toBeNull();
      expect(screen.queryByText(/show archived/i)).toBeNull();
    });

    it('does not render a "New Workspace" button', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByText(/new workspace/i)).toBeNull();
    });

    it('does not render an add-context area', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByText(/add context/i)).toBeNull();
      expect(screen.queryByText(/add-context/i)).toBeNull();
    });

    it('does not render an inline search bar', () => {
      render(<ExplorerHeader />);
      expect(screen.queryByRole('searchbox')).toBeNull();
      expect(screen.queryByPlaceholderText(/search/i)).toBeNull();
    });
  });
});
