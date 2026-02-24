import { useState, useRef, useEffect } from 'react';
import clsx from 'clsx';

export interface DropdownOption {
  id: string;
  name: string;
  description?: string;
}

interface DropdownProps {
  label: string;
  placeholder?: string;
  options: DropdownOption[];
  selectedId: string | null;
  onChange: (selectedId: string) => void;
  loading?: boolean;
  error?: string;
  disabled?: boolean;
  className?: string;
}

export default function Dropdown({
  label,
  placeholder = 'Select an option...',
  options,
  selectedId,
  onChange,
  loading = false,
  error,
  disabled = false,
  className,
}: DropdownProps) {
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
    setIsOpen(!isOpen);
  };

  const handleSelect = (optionId: string) => {
    onChange(optionId);
    setIsOpen(false);
    setSearchQuery('');
  };

  const selectedOption = options.find((opt) => opt.id === selectedId);

  return (
    <div className={clsx('relative', className)} ref={dropdownRef}>
      <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{label}</label>

      {/* Selected Item Display / Trigger */}
      <div
        onClick={handleToggle}
        className={clsx(
          'w-full min-h-[42px] px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg',
          'transition-colors flex items-center justify-between',
          disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
          !disabled && 'focus-within:border-primary',
          isOpen && !disabled && 'border-primary'
        )}
      >
        {selectedOption ? (
          <div className="flex-1 min-w-0">
            <span className="text-[var(--color-text)]">{selectedOption.name}</span>
            {selectedOption.description && (
              <span className="text-[var(--color-text-muted)] text-sm ml-2">- {selectedOption.description}</span>
            )}
          </div>
        ) : (
          <span className="text-[var(--color-text-muted)]">{placeholder}</span>
        )}

        {/* Dropdown Icon */}
        <span
          className={clsx(
            'material-symbols-outlined text-[var(--color-text-muted)] transition-transform ml-2',
            isOpen && 'rotate-180'
          )}
        >
          expand_more
        </span>
      </div>

      {/* Dropdown Menu */}
      {isOpen && !disabled && (
        <div className="absolute z-50 w-full mt-2 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg max-h-80 overflow-hidden">
          {/* Search Input */}
          {options.length > 5 && (
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
          )}

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
              <div className="p-4 text-center text-[var(--color-text-muted)]">No options found</div>
            ) : (
              filteredOptions.map((option) => {
                const isSelected = selectedId === option.id;
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
                      {/* Radio indicator */}
                      <div
                        className={clsx(
                          'flex-shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center mt-0.5',
                          isSelected
                            ? 'border-primary bg-primary'
                            : 'border-[var(--color-border)] bg-[var(--color-bg)]'
                        )}
                      >
                        {isSelected && (
                          <div className="w-2 h-2 rounded-full bg-white" />
                        )}
                      </div>

                      {/* Option Content */}
                      <div className="flex-1 min-w-0">
                        <div className="text-[var(--color-text)] font-medium">{option.name}</div>
                        {option.description && (
                          <div className="text-sm text-[var(--color-text-muted)] mt-0.5 line-clamp-2">
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
        </div>
      )}
    </div>
  );
}
