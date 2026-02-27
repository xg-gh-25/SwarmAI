/**
 * Inline quick-add input for creating ToDos without leaving the Radar.
 *
 * Renders a single-line ``<form>`` with text input and submit button.
 * Submits on Enter or button click, trims whitespace, rejects empty
 * strings. Shows an inline error on failure that auto-dismisses after 5s.
 *
 * Exports:
 * - ``QuickAddTodo``      — Single-line input with Enter/button submit
 * - ``QuickAddTodoProps``  — Props interface
 */

import { useState, useRef, useEffect } from 'react';

export interface QuickAddTodoProps {
  onAdd: (title: string) => Promise<void>;
}

export function QuickAddTodo({ onAdd }: QuickAddTodoProps) {
  const [value, setValue] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Clean up error auto-dismiss timer on unmount
  useEffect(() => {
    return () => {
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || isSubmitting) return;

    // Clear any existing error
    if (errorTimerRef.current) {
      clearTimeout(errorTimerRef.current);
      errorTimerRef.current = null;
    }
    setError(null);
    setIsSubmitting(true);

    try {
      await onAdd(trimmed);
      setValue('');
      inputRef.current?.focus();
    } catch {
      setError('Failed to add ToDo. Try again.');
      errorTimerRef.current = setTimeout(() => {
        setError(null);
        errorTimerRef.current = null;
      }, 5000);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="radar-quick-add" onSubmit={handleSubmit}>
      <div className="radar-quick-add-row">
        <input
          ref={inputRef}
          type="text"
          className="radar-quick-add-input"
          placeholder="Add a ToDo..."
          aria-label="Add a new ToDo"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={isSubmitting}
        />
        <button
          type="submit"
          className="radar-quick-add-btn"
          disabled={isSubmitting}
          aria-label="Add ToDo"
        >
          <span className="material-symbols-outlined">add</span>
        </button>
      </div>
      {error && (
        <span className="radar-quick-add-error">{error}</span>
      )}
    </form>
  );
}
