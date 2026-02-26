/**
 * GlobalSearchBar — Explorer file/folder search rendered in the TopBar.
 *
 * This component provides a centered search input for fuzzy-matching files
 * and folders within the SwarmWS filesystem. It lives in the TopBar above
 * all three columns and writes to the shared ``ExplorerContext`` search state.
 *
 * NOTE: This is distinct from ``components/search/GlobalSearchBar.tsx`` which
 * handles entity search (chat threads, tasks, etc.). This component is
 * specifically for the workspace explorer file/folder search.
 *
 * Key exports:
 * - ``GlobalSearchBar`` — The search input component
 *
 * Requirements: 9.2, 13.1
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { useSearchSafe } from '../../contexts/ExplorerContext';

const DEBOUNCE_MS = 150;

export default function GlobalSearchBar() {
  const searchCtx = useSearchSafe();
  const searchQuery = searchCtx?.searchQuery ?? '';
  const setSearchQuery = searchCtx?.setSearchQuery;
  const [localValue, setLocalValue] = useState(searchQuery);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Sync local value when context query changes externally (e.g. cleared)
  useEffect(() => {
    setLocalValue(searchQuery);
  }, [searchQuery]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setLocalValue(value);

      // Clear any pending debounce regardless of context availability
      if (debounceTimer.current) {
        clearTimeout(debounceTimer.current);
        debounceTimer.current = null;
      }

      // Only schedule debounced context update when search context is available
      if (setSearchQuery) {
        debounceTimer.current = setTimeout(() => {
          setSearchQuery(value);
        }, DEBOUNCE_MS);
      }
    },
    [setSearchQuery]
  );

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimer.current) {
        clearTimeout(debounceTimer.current);
      }
    };
  }, []);

  const handleClear = useCallback(() => {
    setLocalValue('');
    setSearchQuery?.('');
    if (debounceTimer.current) {
      clearTimeout(debounceTimer.current);
    }
  }, [setSearchQuery]);

  // Stop mouseDown propagation so the TopBar drag handler doesn't fire
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
  }, []);

  return (
    <div
      className="flex-1 flex items-center justify-center px-4"
      style={{ maxWidth: 600 }}
      onMouseDown={handleMouseDown}
      data-testid="global-search-bar"
    >
      <div className="relative w-full">
        <span className="absolute left-2.5 top-1/2 -translate-y-1/2 material-symbols-outlined text-sm text-[var(--color-text-muted)] pointer-events-none">
          search
        </span>
        <input
          type="text"
          value={localValue}
          onChange={handleChange}
          placeholder="Search files and folders..."
          className="w-full pl-8 pr-8 py-1 text-xs rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] transition-colors"
          data-testid="global-search-input"
          aria-label="Search files and folders"
        />
        {localValue && (
          <button
            onClick={handleClear}
            onMouseDown={handleMouseDown}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            title="Clear search"
            aria-label="Clear search"
            data-testid="global-search-clear"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        )}
      </div>
    </div>
  );
}
