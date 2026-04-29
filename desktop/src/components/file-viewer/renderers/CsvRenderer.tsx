/**
 * CsvRenderer — Renders CSV/TSV files within the FileViewer.
 *
 * Uses papaparse to parse delimited text into a sortable, virtually-scrolled
 * table view. Supports column sorting (asc/desc/none cycle), a raw-text
 * toggle, and reports row/column counts via onStatusInfo.
 *
 * Virtual scrolling renders only visible rows (~50 at a time) for performance
 * with large datasets. Files exceeding 10,000 rows are truncated with a warning.
 */

import React, { useState, useMemo, useCallback, useRef, useEffect, memo } from 'react';
import Papa from 'papaparse';

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

/** Maximum rows to display before truncating. */
const MAX_DISPLAY_ROWS = 10_000;

/** Number of rows to render above and below the visible area. */
const ROW_BUFFER = 15;

/** Estimated row height in pixels for virtual scroll calculations. */
const ROW_HEIGHT = 32;

/** Height of the sticky header row. */
const HEADER_HEIGHT = 36;

type SortDirection = 'asc' | 'desc' | 'none';

interface SortState {
  column: string;
  direction: SortDirection;
}

/**
 * CsvRenderer parses CSV/TSV content and displays it as an interactive table
 * with column sorting and virtual scrolling, or as raw text with line numbers.
 */
const CsvRenderer = memo(function CsvRenderer({
  content,
  fileName,
  onStatusInfo,
}: RendererProps) {
  const [showRaw, setShowRaw] = useState(false);
  const [sort, setSort] = useState<SortState>({ column: '', direction: 'none' });
  const [scrollTop, setScrollTop] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Parse CSV/TSV content
  const parseResult = useMemo(() => {
    if (!content) return null;
    try {
      const isTsv = fileName.toLowerCase().endsWith('.tsv');
      const result = Papa.parse<Record<string, unknown>>(content, {
        header: true,
        dynamicTyping: true,
        delimiter: isTsv ? '\t' : ',',
        skipEmptyLines: true,
      });
      return result;
    } catch (err) {
      console.error('CSV parse error:', err);
      return null;
    }
  }, [content, fileName]);

  const headers = useMemo(() => parseResult?.meta?.fields ?? [], [parseResult]);
  const totalRows = parseResult?.data?.length ?? 0;
  const isTruncated = totalRows > MAX_DISPLAY_ROWS;

  // Clamp to MAX_DISPLAY_ROWS
  const allRows = useMemo(() => {
    if (!parseResult?.data) return [];
    return isTruncated ? parseResult.data.slice(0, MAX_DISPLAY_ROWS) : parseResult.data;
  }, [parseResult, isTruncated]);

  // Apply sorting
  const sortedRows = useMemo(() => {
    if (sort.direction === 'none' || !sort.column) return allRows;

    return [...allRows].sort((a, b) => {
      const aVal = a[sort.column];
      const bVal = b[sort.column];

      // Nulls last
      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      // Numeric comparison
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sort.direction === 'asc' ? aVal - bVal : bVal - aVal;
      }

      // String comparison
      const aStr = String(aVal);
      const bStr = String(bVal);
      const cmp = aStr.localeCompare(bStr, undefined, { numeric: true, sensitivity: 'base' });
      return sort.direction === 'asc' ? cmp : -cmp;
    });
  }, [allRows, sort]);

  // Report status on parse
  useEffect(() => {
    if (parseResult && headers.length > 0) {
      const rowLabel = isTruncated
        ? `${MAX_DISPLAY_ROWS.toLocaleString()} of ${totalRows.toLocaleString()} rows`
        : `${totalRows.toLocaleString()} rows`;
      onStatusInfo?.({ rowColCount: `${rowLabel} × ${headers.length} cols` });
    } else if (parseResult && totalRows === 0) {
      onStatusInfo?.({ rowColCount: '0 rows' });
    }
  }, [parseResult, headers, totalRows, isTruncated, onStatusInfo]);

  // Track container height for virtual scroll
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerHeight(entry.contentRect.height);
      }
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (container) {
      setScrollTop(container.scrollTop);
    }
  }, []);

  // Virtual scroll calculations
  const virtualRange = useMemo(() => {
    const visibleHeight = containerHeight - HEADER_HEIGHT;
    const startIdx = Math.max(0, Math.floor((scrollTop - HEADER_HEIGHT) / ROW_HEIGHT) - ROW_BUFFER);
    const visibleCount = Math.ceil(visibleHeight / ROW_HEIGHT) + ROW_BUFFER * 2;
    const endIdx = Math.min(sortedRows.length, startIdx + visibleCount);
    return { startIdx, endIdx };
  }, [scrollTop, containerHeight, sortedRows.length]);

  const totalTableHeight = sortedRows.length * ROW_HEIGHT + HEADER_HEIGHT;

  // Sort handler: cycles asc -> desc -> none
  const handleSort = useCallback((column: string) => {
    setSort((prev) => {
      if (prev.column !== column) return { column, direction: 'asc' };
      if (prev.direction === 'asc') return { column, direction: 'desc' };
      if (prev.direction === 'desc') return { column: '', direction: 'none' };
      return { column, direction: 'asc' };
    });
  }, []);

  /** Sort indicator icon for a column header. */
  const sortIcon = useCallback(
    (column: string) => {
      if (sort.column !== column || sort.direction === 'none') {
        return 'unfold_more';
      }
      return sort.direction === 'asc' ? 'arrow_upward' : 'arrow_downward';
    },
    [sort],
  );

  const toggleRaw = useCallback(() => setShowRaw((v) => !v), []);

  // --- No content ---
  if (!content) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)]">
        <span className="material-symbols-outlined text-4xl mb-2">table_chart</span>
        <p className="text-sm">No CSV content available</p>
      </div>
    );
  }

  // --- Parse error ---
  if (parseResult && parseResult.errors.length > 0 && headers.length === 0) {
    return (
      <div className="flex flex-col h-full">
        <div className="px-3 py-2 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <div className="flex items-center gap-2 text-red-400">
            <span className="material-symbols-outlined text-base">error</span>
            <span className="text-xs font-medium">
              Failed to parse {fileName.endsWith('.tsv') ? 'TSV' : 'CSV'}
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            {parseResult.errors[0]?.message ?? 'Unknown parse error'}
          </p>
        </div>
        {/* Fallback: raw content */}
        <div className="flex-1 overflow-auto font-mono text-xs p-4 bg-[var(--color-bg)]">
          {renderRawLines(content)}
        </div>
      </div>
    );
  }

  // --- Empty CSV ---
  if (headers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)]">
        <span className="material-symbols-outlined text-4xl mb-2">table_chart</span>
        <p className="text-sm">Empty file</p>
        <p className="text-xs mt-1">{fileName}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-bg)] shrink-0">
        <div className="flex items-center gap-2">
          {isTruncated && (
            <span className="flex items-center gap-1 text-xs text-amber-500">
              <span className="material-symbols-outlined text-sm">warning</span>
              Showing {MAX_DISPLAY_ROWS.toLocaleString()} of {totalRows.toLocaleString()} rows
            </span>
          )}
          {parseResult && parseResult.errors.length > 0 && headers.length > 0 && (
            <span className="flex items-center gap-1 text-xs text-amber-500" title={parseResult.errors.map(e => e.message).join('; ')}>
              <span className="material-symbols-outlined text-sm">warning</span>
              {parseResult.errors.length} parse warning{parseResult.errors.length > 1 ? 's' : ''}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* Raw mode toggle */}
          <button
            onClick={toggleRaw}
            className={`flex items-center gap-1 px-2 py-0.5 text-xs rounded transition-colors ${
              showRaw
                ? 'bg-[var(--color-border)] text-[var(--color-text)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)]'
            }`}
            title={showRaw ? 'Switch to table view' : 'Switch to raw text'}
          >
            <span className="material-symbols-outlined text-base">
              {showRaw ? 'table_chart' : 'raw_on'}
            </span>
            {showRaw ? 'Table' : 'Raw'}
          </button>
        </div>
      </div>

      {/* Content area */}
      {showRaw ? (
        <div className="flex-1 overflow-auto font-mono text-xs p-0 bg-[var(--color-bg)]">
          {renderRawLines(content)}
        </div>
      ) : (
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-auto bg-[var(--color-bg)]"
          onScroll={handleScroll}
        >
          <div style={{ height: totalTableHeight, position: 'relative' }}>
            <table className="w-full border-collapse text-xs">
              {/* Sticky header */}
              <thead className="sticky top-0 z-10">
                <tr className="bg-[var(--color-bg)] border-b border-[var(--color-border)]">
                  {/* Row number column */}
                  <th
                    className="px-2 py-2 text-right text-[var(--color-text-muted)] font-medium border-b border-r border-[var(--color-border)] bg-[var(--color-bg)] select-none"
                    style={{ width: 52, minWidth: 52 }}
                  >
                    #
                  </th>
                  {headers.map((header) => (
                    <th
                      key={header}
                      onClick={() => handleSort(header)}
                      className="px-3 py-2 text-left font-semibold text-[var(--color-text)] border-b border-[var(--color-border)] bg-[var(--color-bg)] cursor-pointer select-none hover:bg-[var(--color-border)] transition-colors whitespace-nowrap"
                    >
                      <span className="inline-flex items-center gap-1">
                        {header}
                        <span
                          className={`material-symbols-outlined text-sm ${
                            sort.column === header && sort.direction !== 'none'
                              ? 'text-[var(--color-text)]'
                              : 'text-[var(--color-text-muted)] opacity-40'
                          }`}
                        >
                          {sortIcon(header)}
                        </span>
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>

              {/* Virtual rows */}
              <tbody>
                {/* Top spacer */}
                {virtualRange.startIdx > 0 && (
                  <tr style={{ height: virtualRange.startIdx * ROW_HEIGHT }}>
                    <td colSpan={headers.length + 1} />
                  </tr>
                )}

                {sortedRows.slice(virtualRange.startIdx, virtualRange.endIdx).map((row, i) => {
                  const rowIdx = virtualRange.startIdx + i;
                  const isEven = rowIdx % 2 === 0;
                  return (
                    <tr
                      key={rowIdx}
                      className={`${isEven ? '' : 'bg-[var(--color-bg)]'} hover:bg-[var(--color-border)] transition-colors`}
                      style={{
                        height: ROW_HEIGHT,
                        backgroundColor: isEven ? undefined : 'color-mix(in srgb, var(--color-border) 20%, transparent)',
                      }}
                    >
                      {/* Row number */}
                      <td
                        className="px-2 text-right text-[var(--color-text-muted)] font-mono border-r border-[var(--color-border)] select-none"
                        style={{ width: 52, minWidth: 52 }}
                      >
                        {rowIdx + 1}
                      </td>
                      {headers.map((header) => (
                        <td
                          key={header}
                          className="px-3 text-[var(--color-text)] truncate max-w-[300px]"
                          title={row[header] != null ? String(row[header]) : ''}
                        >
                          {formatCellValue(row[header])}
                        </td>
                      ))}
                    </tr>
                  );
                })}

                {/* Bottom spacer */}
                {virtualRange.endIdx < sortedRows.length && (
                  <tr style={{ height: (sortedRows.length - virtualRange.endIdx) * ROW_HEIGHT }}>
                    <td colSpan={headers.length + 1} />
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
});

/** Format a cell value for display. Nulls render as a muted dash. */
function formatCellValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

/** Render raw text content with line numbers. */
function renderRawLines(text: string): React.JSX.Element {
  const lines = text.split('\n');
  const gutterWidth = String(lines.length).length;
  return (
    <div className="flex">
      {/* Line numbers gutter */}
      <div className="shrink-0 pr-3 pl-3 py-3 text-right select-none border-r border-[var(--color-border)]">
        {lines.map((_, idx) => (
          <div key={idx} className="text-[var(--color-text-muted)] leading-5" style={{ minWidth: gutterWidth + 'ch' }}>
            {idx + 1}
          </div>
        ))}
      </div>
      {/* Content */}
      <pre className="flex-1 py-3 px-3 overflow-x-auto">
        {lines.map((line, idx) => (
          <div key={idx} className="text-[var(--color-text)] leading-5 whitespace-pre">
            {line}
          </div>
        ))}
      </pre>
    </div>
  );
}

export default CsvRenderer;
