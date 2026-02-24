import { useState, useRef, useEffect } from 'react';
import clsx from 'clsx';

export interface MultiSelectOption {
  id: string;
  name: string;
  description?: string;
}

interface MultiSelectProps {
  label: string;
  placeholder?: string;
  options: MultiSelectOption[];
  selectedIds: string[];
  onChange: (selectedIds: string[]) => void;
  loading?: boolean;
  error?: string;
  onOpen?: () => void;
  className?: string;
  disabled?: boolean;
}

export default function MultiSelect({
  label,
  placeholder = 'Select items...',
  options,
  selectedIds,
  onChange,
  loading = false,
  error,
  onOpen,
  className,
  disabled = false,
}: MultiSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setSearchQuery('');
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Filter options based on search
  const filteredOptions = options.filter((option) =>
    option.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleToggle = () => {
    if (disabled) return;
    const newIsOpen = !isOpen;
    setIsOpen(newIsOpen);
    if (newIsOpen && onOpen) {
      onOpen();
    }
  };

  const handleSelect = (optionId: string) => {
    if (disabled) return;
    if (selectedIds.includes(optionId)) {
      onChange(selectedIds.filter((id) => id !== optionId));
    } else {
      onChange([...selectedIds, optionId]);
    }
  };

  const handleRemove = (optionId: string, e: React.MouseEvent) => {
    if (disabled) return;
    e.stopPropagation();
    onChange(selectedIds.filter((id) => id !== optionId));
  };

  const selectedOptions = options.filter((opt) => selectedIds.includes(opt.id));

  return (
    <div className={clsx('relative', className)} ref={dropdownRef}>
      <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{label}</label>

      {/* Selected Items Display / Trigger */}
      <div
        onClick={handleToggle}
        className={clsx(
          'w-full min-h-[42px] px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg transition-colors',
          disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer focus-within:border-primary',
          isOpen && !disabled && 'border-primary'
        )}
      >
        {selectedOptions.length === 0 ? (
          <span className="text-[var(--color-text-muted)]">{placeholder}</span>
        ) : (
          <div className="flex flex-wrap gap-2">
            {selectedOptions.map((option) => (
              <span
                key={option.id}
                className="inline-flex items-center gap-1 px-2 py-1 bg-primary/10 text-primary rounded text-sm"
              >
                {option.name}
                <button
                  onClick={(e) => handleRemove(option.id, e)}
                  className="hover:bg-primary/20 rounded-full p-0.5"
                >
                  <span className="material-symbols-outlined text-sm">close</span>
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Dropdown Icon */}
        <span
          className={clsx(
            'material-symbols-outlined absolute right-3 top-10 text-[var(--color-text-muted)] transition-transform',
            isOpen && 'rotate-180'
          )}
        >
          expand_more
        </span>
      </div>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg max-h-80 overflow-hidden">
          {/* Search Input */}
          <div className="p-3 border-b border-[var(--color-border)]">
            <div className="relative">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] text-lg">
                search
              </span>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search..."
                className="w-full pl-10 pr-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
                onClick={(e) => e.stopPropagation()}
              />
            </div>
          </div>

          {/* Options List */}
          <div className="max-h-60 overflow-y-auto">
            {loading ? (
              <div className="p-4 text-center text-[var(--color-text-muted)]">
                <div className="inline-block w-5 h-5 border-2 border-muted border-t-primary rounded-full animate-spin" />
                <span className="ml-2">Loading...</span>
              </div>
            ) : error ? (
              <div className="p-4 text-center text-status-error">{error}</div>
            ) : filteredOptions.length === 0 ? (
              <div className="p-4 text-center text-[var(--color-text-muted)]">No items found</div>
            ) : (
              filteredOptions.map((option) => {
                const isSelected = selectedIds.includes(option.id);
                return (
                  <div
                    key={option.id}
                    onClick={() => handleSelect(option.id)}
                    className={clsx(
                      'px-4 py-3 cursor-pointer transition-colors hover:bg-[var(--color-hover)]',
                      isSelected && 'bg-[var(--color-hover)]'
                    )}
                  >
                    <div className="flex items-start gap-3">
                      {/* Checkbox */}
                      <div
                        className={clsx(
                          'flex-shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center mt-0.5',
                          isSelected
                            ? 'bg-primary border-primary'
                            : 'border-[var(--color-border)] bg-[var(--color-bg)]'
                        )}
                      >
                        {isSelected && (
                          <span className="material-symbols-outlined text-[var(--color-text)] text-sm">
                            check
                          </span>
                        )}
                      </div>

                      {/* Option Content */}
                      <div className="flex-1 min-w-0">
                        <div className="text-[var(--color-text)] font-medium">{option.name}</div>
                        {option.description && (
                          <div className="text-sm text-[var(--color-text-muted)] mt-0.5 line-clamp-1">
                            {option.description}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer */}
          {!loading && !error && filteredOptions.length > 0 && (
            <div className="p-3 border-t border-[var(--color-border)] bg-[var(--color-bg)]/50">
              <div className="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
                <span>
                  {selectedIds.length} of {options.length} selected
                </span>
                {selectedIds.length > 0 && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onChange([]);
                    }}
                    className="text-primary hover:text-primary/80 transition-colors"
                  >
                    Clear all
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
