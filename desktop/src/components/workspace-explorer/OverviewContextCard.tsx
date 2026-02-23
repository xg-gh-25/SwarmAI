import { useState, useCallback } from 'react';

export interface ContextData {
  goal?: string;
  focus?: string;
  context?: string;
  priorities?: string[];
}

export interface OverviewContextCardProps {
  contextData: ContextData;
  isEditing?: boolean;
  onEditToggle?: () => void;
  onSave?: (data: ContextData) => void;
}

/**
 * Parse a context.md string into structured ContextData.
 */
export function parseContextMd(content: string): ContextData {
  const data: ContextData = {};
  const lines = content.split('\n');
  let currentField: string | null = null;
  const priorities: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith('## Goal') || trimmed.startsWith('**Goal**')) {
      currentField = 'goal';
    } else if (trimmed.startsWith('## Focus') || trimmed.startsWith('**Focus**')) {
      currentField = 'focus';
    } else if (trimmed.startsWith('## Context') || trimmed.startsWith('**Context**')) {
      currentField = 'context';
    } else if (trimmed.startsWith('## Priorities') || trimmed.startsWith('**Priorities**')) {
      currentField = 'priorities';
    } else if (trimmed.startsWith('## ') || trimmed.startsWith('**')) {
      currentField = null;
    } else if (currentField && trimmed) {
      if (currentField === 'priorities' && (trimmed.startsWith('- ') || trimmed.startsWith('* '))) {
        priorities.push(trimmed.slice(2));
      } else if (currentField === 'goal') {
        data.goal = (data.goal ? data.goal + ' ' : '') + trimmed;
      } else if (currentField === 'focus') {
        data.focus = (data.focus ? data.focus + ' ' : '') + trimmed;
      } else if (currentField === 'context') {
        data.context = (data.context ? data.context + ' ' : '') + trimmed;
      }
    }
  }

  if (priorities.length > 0) data.priorities = priorities;
  return data;
}

/**
 * Serialize ContextData back to context.md format.
 */
export function serializeContextMd(data: ContextData): string {
  const parts: string[] = [];
  if (data.goal) parts.push(`## Goal\n${data.goal}`);
  if (data.focus) parts.push(`## Focus\n${data.focus}`);
  if (data.context) parts.push(`## Context\n${data.context}`);
  if (data.priorities && data.priorities.length > 0) {
    parts.push(`## Priorities\n${data.priorities.map((p) => `- ${p}`).join('\n')}`);
  }
  return parts.join('\n\n') + '\n';
}

/**
 * OverviewContextCard - Display Goal, Focus, Context, Priorities with inline editing.
 * Requirements: 3.3, 9.4
 */
export default function OverviewContextCard({
  contextData,
  isEditing: controlledEditing,
  onEditToggle,
  onSave,
}: OverviewContextCardProps) {
  const [internalEditing, setInternalEditing] = useState(false);
  const isEditing = controlledEditing ?? internalEditing;

  const [editData, setEditData] = useState<ContextData>(contextData);

  const handleEditToggle = useCallback(() => {
    if (isEditing) {
      // Save
      onSave?.(editData);
    } else {
      setEditData(contextData);
    }
    if (onEditToggle) {
      onEditToggle();
    } else {
      setInternalEditing(!isEditing);
    }
  }, [isEditing, editData, contextData, onEditToggle, onSave]);

  const handleCancel = useCallback(() => {
    setEditData(contextData);
    if (onEditToggle) {
      onEditToggle();
    } else {
      setInternalEditing(false);
    }
  }, [contextData, onEditToggle]);

  const { goal, focus, context, priorities } = contextData;
  const hasContent = goal || focus || context || (priorities && priorities.length > 0);

  if (!hasContent && !isEditing) {
    return (
      <div
        className="mx-3 my-2 p-3 rounded border border-dashed border-[var(--color-border)] text-center"
        data-testid="overview-context-card"
      >
        <p className="text-xs text-[var(--color-text-muted)] mb-2">No workspace context set</p>
        <button
          onClick={handleEditToggle}
          className="text-xs text-[var(--color-primary)] hover:underline"
          data-testid="edit-context-button"
        >
          + Add Context
        </button>
      </div>
    );
  }

  if (isEditing) {
    return (
      <div
        className="mx-3 my-2 p-3 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-2"
        data-testid="overview-context-card"
      >
        <EditField
          label="Goal"
          value={editData.goal ?? ''}
          onChange={(v) => setEditData((d) => ({ ...d, goal: v }))}
        />
        <EditField
          label="Focus"
          value={editData.focus ?? ''}
          onChange={(v) => setEditData((d) => ({ ...d, focus: v }))}
        />
        <EditField
          label="Context"
          value={editData.context ?? ''}
          onChange={(v) => setEditData((d) => ({ ...d, context: v }))}
          multiline
        />
        <EditField
          label="Priorities (one per line)"
          value={(editData.priorities ?? []).join('\n')}
          onChange={(v) =>
            setEditData((d) => ({
              ...d,
              priorities: v
                .split('\n')
                .map((l) => l.trim())
                .filter(Boolean),
            }))
          }
          multiline
        />
        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={handleCancel}
            className="px-2 py-1 text-xs rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)]"
            data-testid="cancel-context-button"
          >
            Cancel
          </button>
          <button
            onClick={handleEditToggle}
            className="px-2 py-1 text-xs rounded bg-[var(--color-primary)] text-white hover:opacity-90"
            data-testid="save-context-button"
          >
            Save
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="mx-3 my-2 p-3 rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] space-y-1.5"
      data-testid="overview-context-card"
    >
      {goal && <ContextField label="Goal" value={goal} />}
      {focus && <ContextField label="Focus" value={focus} />}
      {context && <ContextField label="Context" value={context} />}
      {priorities && priorities.length > 0 && (
        <div>
          <span className="text-xs font-medium text-[var(--color-text-muted)]">Priorities</span>
          <ul className="mt-0.5 pl-4 text-xs text-[var(--color-text)] list-disc">
            {priorities.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      <button
        onClick={handleEditToggle}
        className="text-xs text-[var(--color-primary)] hover:underline mt-1"
        data-testid="edit-context-button"
      >
        Edit Context
      </button>
    </div>
  );
}

function ContextField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-xs font-medium text-[var(--color-text-muted)]">{label}</span>
      <p className="text-xs text-[var(--color-text)] truncate" title={value}>
        {value}
      </p>
    </div>
  );
}

function EditField({
  label,
  value,
  onChange,
  multiline = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  multiline?: boolean;
}) {
  const cls =
    'w-full px-2 py-1 text-xs rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]';
  return (
    <div>
      <label className="text-xs font-medium text-[var(--color-text-muted)] block mb-0.5">
        {label}
      </label>
      {multiline ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          className={cls + ' resize-none'}
          data-testid={`edit-${label.toLowerCase().replace(/\s+/g, '-')}`}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cls}
          data-testid={`edit-${label.toLowerCase()}`}
        />
      )}
    </div>
  );
}
