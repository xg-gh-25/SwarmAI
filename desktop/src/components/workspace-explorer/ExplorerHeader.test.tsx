/**
 * Unit tests for the ExplorerHeader component.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - "SwarmWS" static title renders (Req 9.1)
 * - Old controls are absent: no dropdown, no toggle, no checkbox,
 *   no "New Workspace" button, no add-context area (Req 9.3–9.7)
 * - Focus Mode toggle is disabled when no project is selected (Req 12.4)
 *
 * **Validates: Requirements 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 12.4**
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ExplorerHeader from './ExplorerHeader';
import { useSelection, useTreeData } from '../../contexts/ExplorerContext';

// Mock the ExplorerContext hooks
vi.mock('../../contexts/ExplorerContext', () => ({
  useSelection: vi.fn(),
  useTreeData: vi.fn(),
}));

describe('ExplorerHeader', () => {
  beforeEach(() => {
    (useSelection as ReturnType<typeof vi.fn>).mockReturnValue({
      focusMode: false,
      toggleFocusMode: vi.fn(),
      activeProjectId: null,
    });
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
      // No select/combobox role elements
      expect(screen.queryByRole('combobox')).toBeNull();
      expect(screen.queryByRole('listbox')).toBeNull();
      // No text referencing workspace selection
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

  describe('Focus Mode toggle (Req 12.4)', () => {
    it('is disabled when no project is selected', () => {
      (useSelection as ReturnType<typeof vi.fn>).mockReturnValue({
        focusMode: false,
        toggleFocusMode: vi.fn(),
        activeProjectId: null,
      });

      render(<ExplorerHeader />);
      const toggle = screen.getByTestId('focus-mode-toggle');
      expect(toggle).toBeDefined();
      expect((toggle as HTMLButtonElement).disabled).toBe(true);
    });

    it('is enabled when a project is selected', () => {
      (useSelection as ReturnType<typeof vi.fn>).mockReturnValue({
        focusMode: false,
        toggleFocusMode: vi.fn(),
        activeProjectId: 'my-project',
      });

      render(<ExplorerHeader />);
      const toggle = screen.getByTestId('focus-mode-toggle');
      expect((toggle as HTMLButtonElement).disabled).toBe(false);
    });

    it('shows "Select a project first" tooltip when disabled', () => {
      render(<ExplorerHeader />);
      const toggle = screen.getByTestId('focus-mode-toggle');
      expect(toggle.getAttribute('title')).toBe('Select a project first');
    });
  });
});
