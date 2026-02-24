import { useState, useRef, useCallback, type ReactNode } from 'react';

interface Column {
  key: string;
  header: string;
  minWidth?: number;
  initialWidth?: number;
  align?: 'left' | 'right' | 'center';
}

interface ResizableTableProps {
  columns: Column[];
  children: ReactNode;
  className?: string;
  headerContent?: Record<string, ReactNode>;  // Custom header content for specific columns
}

export function ResizableTable({ columns, children, className = '', headerContent = {} }: ResizableTableProps) {
  // Initialize column widths from initialWidth or default
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(() => {
    const widths: Record<string, number> = {};
    columns.forEach((col) => {
      widths[col.key] = col.initialWidth || 150;
    });
    return widths;
  });

  const tableRef = useRef<HTMLTableElement>(null);
  const resizingRef = useRef<{
    columnKey: string;
    startX: number;
    startWidth: number;
  } | null>(null);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent, columnKey: string) => {
      e.preventDefault();
      resizingRef.current = {
        columnKey,
        startX: e.clientX,
        startWidth: columnWidths[columnKey],
      };

      const handleMouseMove = (e: MouseEvent) => {
        if (!resizingRef.current) return;

        const { columnKey, startX, startWidth } = resizingRef.current;
        const column = columns.find((c) => c.key === columnKey);
        const minWidth = column?.minWidth || 80;
        const newWidth = Math.max(minWidth, startWidth + (e.clientX - startX));

        setColumnWidths((prev) => ({
          ...prev,
          [columnKey]: newWidth,
        }));
      };

      const handleMouseUp = () => {
        resizingRef.current = null;
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [columnWidths, columns]
  );

  return (
    <div className={`overflow-x-auto ${className}`}>
      <table ref={tableRef} className="w-full" style={{ tableLayout: 'fixed' }}>
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            {columns.map((column, index) => (
              <th
                key={column.key}
                className={`px-6 py-4 text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider relative ${
                  column.align === 'right' ? 'text-right' : column.align === 'center' ? 'text-center' : 'text-left'
                }`}
                style={{ width: columnWidths[column.key] }}
              >
                {headerContent[column.key] !== undefined ? headerContent[column.key] : column.header}
                {/* Resize handle - not on last column */}
                {index < columns.length - 1 && (
                  <div
                    className="absolute right-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-primary/50 active:bg-primary group"
                    onMouseDown={(e) => handleMouseDown(e, column.key)}
                  >
                    <div className="absolute right-0 top-1/2 -translate-y-1/2 w-[3px] h-6 rounded bg-[var(--color-border)] group-hover:bg-primary/70" />
                  </div>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

interface ResizableTableCellProps {
  children: ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
}

export function ResizableTableCell({ children, className = '', align = 'left' }: ResizableTableCellProps) {
  const alignClass = align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';
  return (
    <td className={`px-6 py-4 overflow-hidden ${alignClass} ${className}`}>
      <div className="truncate">{children}</div>
    </td>
  );
}
