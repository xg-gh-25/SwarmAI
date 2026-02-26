/**
 * Unit tests for the layout GlobalSearchBar component.
 *
 * Testing methodology: Unit tests using Vitest + React Testing Library.
 * Verifies:
 * - Renders with correct placeholder "Search files and folders..."
 * - Debounce behavior: 150ms delay before context update
 *
 * **Validates: Requirements 9.2, 13.1**
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import GlobalSearchBar from './GlobalSearchBar';
import { useSearchSafe } from '../../contexts/ExplorerContext';

vi.mock('../../contexts/ExplorerContext', () => ({
  useSearchSafe: vi.fn(),
}));

describe('GlobalSearchBar', () => {
  let mockSetSearchQuery: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    mockSetSearchQuery = vi.fn();
    (useSearchSafe as ReturnType<typeof vi.fn>).mockReturnValue({
      searchQuery: '',
      setSearchQuery: mockSetSearchQuery,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders with correct placeholder "Search files and folders..."', () => {
    render(<GlobalSearchBar />);
    const input = screen.getByTestId('global-search-input');
    expect(input).toBeDefined();
    expect(input.getAttribute('placeholder')).toBe('Search files and folders...');
  });

  it('debounces input by 150ms before calling setSearchQuery', () => {
    render(<GlobalSearchBar />);
    const input = screen.getByTestId('global-search-input');

    fireEvent.change(input, { target: { value: 'test' } });
    // Should NOT have been called yet (debounce pending)
    expect(mockSetSearchQuery).not.toHaveBeenCalled();

    // Advance just under the debounce threshold
    act(() => { vi.advanceTimersByTime(100); });
    expect(mockSetSearchQuery).not.toHaveBeenCalled();

    // Advance past the 150ms debounce
    act(() => { vi.advanceTimersByTime(50); });
    expect(mockSetSearchQuery).toHaveBeenCalledTimes(1);
    expect(mockSetSearchQuery).toHaveBeenCalledWith('test');
  });

  it('resets debounce timer on rapid successive inputs', () => {
    render(<GlobalSearchBar />);
    const input = screen.getByTestId('global-search-input');

    fireEvent.change(input, { target: { value: 'a' } });
    act(() => { vi.advanceTimersByTime(100); });
    // Type again before debounce fires — timer should reset
    fireEvent.change(input, { target: { value: 'ab' } });
    act(() => { vi.advanceTimersByTime(100); });
    // Still not fired (only 100ms since last keystroke)
    expect(mockSetSearchQuery).not.toHaveBeenCalled();

    act(() => { vi.advanceTimersByTime(50); });
    // Now 150ms since last keystroke — should fire with final value
    expect(mockSetSearchQuery).toHaveBeenCalledTimes(1);
    expect(mockSetSearchQuery).toHaveBeenCalledWith('ab');
  });

  it('renders gracefully when outside ExplorerProvider (useSearchSafe returns null)', () => {
    (useSearchSafe as ReturnType<typeof vi.fn>).mockReturnValue(null);
    render(<GlobalSearchBar />);
    const input = screen.getByTestId('global-search-input');
    expect(input).toBeDefined();
    expect(input.getAttribute('placeholder')).toBe('Search files and folders...');
  });
});
