/**
 * GlobalSearchBar component - debounced search across all entity types.
 * Displays results in a dropdown below the search bar.
 * Requirements: 38.1, 38.3, 38.9
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { searchService } from '../../services/search';
import type { SearchResultItem, SearchResults } from '../../services/search';
import SearchResultsList from './SearchResults';

interface GlobalSearchBarProps {
  workspaceId?: string;
  onSelect?: (item: SearchResultItem) => void;
}

export default function GlobalSearchBar({ workspaceId, onSelect }: GlobalSearchBarProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResultItem[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Debounced search - 300ms
  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    setIsLoading(true);
    let aborted = false;
    const timer = setTimeout(async () => {
      try {
        const scope = workspaceId || 'all';
        const searchResults: SearchResults = await searchService.search(query, scope);
        if (!aborted) {
          setResults(searchResults.results);
          setIsOpen(true);
        }
      } catch (err) {
        console.error('Search failed:', err);
        if (!aborted) {
          setResults([]);
        }
      } finally {
        if (!aborted) {
          setIsLoading(false);
        }
      }
    }, 300);

    return () => {
      aborted = true;
      clearTimeout(timer);
    };
  }, [query, workspaceId]);

  // Click outside to close dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = useCallback((item: SearchResultItem) => {
    setIsOpen(false);
    setQuery('');
    setResults([]);
    onSelect?.(item);
  }, [onSelect]);

  // Keyboard navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!isOpen || results.length === 0) return;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setSelectedIndex((prev) => (prev < results.length - 1 ? prev + 1 : 0));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : results.length - 1));
        break;
      case 'Enter':
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < results.length) {
          handleSelect(results[selectedIndex]);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        setSelectedIndex(-1);
        inputRef.current?.blur();
        break;
    }
  }, [isOpen, results, selectedIndex, handleSelect]);

  // Reset selected index when results change
  useEffect(() => {
    setSelectedIndex(-1);
  }, [results]);

  return (
    <div ref={containerRef} className="relative" data-testid="global-search-bar">
      <div className="relative">
        <span className="material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] text-lg">
          search
        </span>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => { if (results.length > 0) setIsOpen(true); }}
          placeholder={t('search.placeholder')}
          className="w-full pl-9 pr-3 py-1.5 text-sm bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]/50"
          data-testid="search-input"
          role="combobox"
          aria-expanded={isOpen}
          aria-haspopup="listbox"
          aria-activedescendant={selectedIndex >= 0 ? `search-result-${selectedIndex}` : undefined}
        />
        {isLoading && (
          <span className="material-symbols-outlined absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] text-sm animate-spin">
            sync
          </span>
        )}
      </div>

      {/* Results dropdown */}
      {isOpen && results.length > 0 && (
        <div
          className="absolute z-50 top-full left-0 right-0 mt-1 max-h-80 overflow-y-auto bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg"
          role="listbox"
          data-testid="search-dropdown"
        >
          <SearchResultsList
            results={results}
            selectedIndex={selectedIndex}
            onSelect={handleSelect}
          />
        </div>
      )}

      {/* No results message */}
      {isOpen && query.trim() && !isLoading && results.length === 0 && (
        <div
          className="absolute z-50 top-full left-0 right-0 mt-1 p-3 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg text-center text-sm text-[var(--color-text-muted)]"
          data-testid="search-no-results"
        >
          {t('search.noResults')}
        </div>
      )}
    </div>
  );
}
