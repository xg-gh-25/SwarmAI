/**
 * StarterMaterial — the material picker shared by New Brain and "Add knowledge":
 * one input (repo URL / link / local path) + Add, Choose files / Choose folder (the
 * Tauri dialog, which returns ABSOLUTE paths), a drop target, and one row per item
 * with a Govern / Distill / Shelf segmented role. Items are classified by TYPE
 * (classifyStarterItem); the person can change any role.
 *
 * State is owned by the caller (New Brain persists it as an overlay draft); this
 * component only edits it. `testPrefix` keeps each surface's test ids distinct.
 *
 * Exports: MaterialPicker, MaterialRow, ROLES
 */
import { useCallback, useState } from 'react';
import {
  classifyStarterItem, detectKind, isLocalPath,
  type StarterItem, type StarterKind, type StarterRole,
} from '../../utils/newBrainDispatch';
import { getStarterKind } from '../../services/ddd';

export interface MaterialRow extends StarterItem {
  id: number;
  displayKind: string;
  /** The backend is reading what this local folder is (a repo?). */
  checking?: boolean;
  /** The person chose the role: a late backend answer does not change it. */
  touched?: boolean;
}

/** The three roles, as a segmented choice on each material row. */
export const ROLES: { role: StarterRole; label: string; hint: string; tone: string }[] = [
  { role: 'GOVERN', label: 'Govern', hint: 'An asset it manages: bound, and a repo gets code-intel', tone: 'var(--color-primary)' },
  { role: 'DISTILL', label: 'Distill', hint: 'Read once and distilled into its knowledge', tone: 'var(--color-status-warning)' },
  { role: 'SHELF', label: 'Shelf', hint: 'Kept for reference, read when needed', tone: 'var(--color-text-muted)' },
];

const KIND_ICON: Record<string, string> = {
  repo: 'code', folder: 'folder', file: 'description', link: 'link', text: 'notes',
};

let itemSeq = 0;

export function MaterialPicker({
  items, setItems, draft, setDraft, testPrefix = 'new-brain', emptyText,
}: {
  items: MaterialRow[];
  setItems: (up: MaterialRow[] | ((prev: MaterialRow[]) => MaterialRow[])) => void;
  draft: string;
  setDraft: (v: string) => void;
  testPrefix?: string;
  /** The second line of the empty drop target. */
  emptyText: string;
}) {
  // Guards against a second native dialog while one is pending (rapid double-click).
  const [picking, setPicking] = useState(false);

  // A local folder (or local .git path) is asked of the backend: a git work tree, or a
  // folder of them, is GOVERN with one row per repository (design
  // 2026-10-29-code-intel-auto D2). Until it answers the row shows "checking".
  const refine = useCallback((id: number, value: string) => {
    const done = (up: (prev: MaterialRow[], at: number) => MaterialRow[]) =>
      setItems((prev) => {
        const at = prev.findIndex((it) => it.id === id);
        if (at < 0) return prev;
        if (prev[at].touched) return prev.map((it) => (it.id === id ? { ...it, checking: false } : it));
        return up(prev, at);
      });
    getStarterKind(value)
      .then((k) => done((prev, at) => {
        if (k.role === 'GOVERN' && k.roots.length > 0) {
          const rows = k.roots
            .filter((r) => !prev.some((it, i) => i !== at && it.value === r))
            .map((r) => ({ id: ++itemSeq, value: r, kind: 'repo' as StarterKind, role: 'GOVERN' as StarterRole,
              displayKind: 'repo' }));
          return [...prev.slice(0, at), ...rows, ...prev.slice(at + 1)];
        }
        return prev.map((it) => (it.id === id ? { ...it, role: k.role, checking: false } : it));
      }))
      .catch(() => done((prev) => prev.map((it) => (it.id === id ? { ...it, checking: false } : it))));
  }, [setItems]);

  const addItem = useCallback((raw: string, kindOverride?: StarterKind) => {
    const value = raw.trim();
    if (!value) return;
    // decided outside the updater: React may run an updater later, during render
    const id = ++itemSeq;
    const kind = kindOverride ?? detectKind(value);
    const role = classifyStarterItem({ value, kind });
    const ask = isLocalPath(value) && (kind === 'folder' || kind === 'repo');
    setItems((prev) => {
      if (prev.some((it) => it.value === value)) return prev; // dedupe
      return [...prev, { id, value, kind, role, displayKind: kind, checking: ask }];
    });
    // a deduped value has no row with this id: refine then changes nothing
    if (ask) refine(id, value);
  }, [setItems, refine]);

  const commitDraft = useCallback(() => {
    // Several lines at once: one item per line.
    draft.split('\n').forEach((line) => addItem(line));
    setDraft('');
  }, [draft, addItem, setDraft]);

  // Native file browser (Tauri dialog) → absolute paths. Dynamic import + toast
  // fallback so a non-Tauri / dev webview degrades instead of dead-ending.
  const pickAndAdd = useCallback(async (mode: 'file' | 'folder') => {
    if (picking) return;
    setPicking(true);
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const picked = await open({
        multiple: mode === 'file',
        directory: mode === 'folder',
        title: mode === 'file' ? 'Add files to this brain' : 'Add a folder to this brain',
      });
      const paths = Array.isArray(picked) ? picked : picked ? [picked] : [];
      paths.forEach((p) => addItem(p, mode));
    } catch {
      document.dispatchEvent(new CustomEvent('swarm:toast', {
        detail: { message: 'File browser unavailable here — paste a full path into the field instead.' },
      }));
    } finally {
      setPicking(false);
    }
  }, [addItem, picking]);

  const removeItem = useCallback((id: number) => setItems((prev) => prev.filter((it) => it.id !== id)), [setItems]);
  const setRole = useCallback((id: number, role: StarterRole) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, role, touched: true } : it)));
  }, [setItems]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // 1. An internal Workspace Explorer drag carries a real path (application/json).
    const jsonData = e.dataTransfer.getData('application/json');
    if (jsonData) {
      try {
        const f = JSON.parse(jsonData) as { path?: string; name?: string; type?: string };
        const val = f.path || f.name;
        if (val) addItem(val, f.type === 'directory' ? 'folder' : undefined);
      } catch { /* malformed payload — nothing to add */ }
      return;
    }
    // 2. A Finder drop: the webview gives only a basename, a path the agent cannot
    //    open. Never add it as if it were a path; say so and point at the picker.
    let added = false;
    let lostPath = false;
    for (const f of Array.from(e.dataTransfer.files ?? [])) {
      const path = (f as File & { path?: string }).path;
      if (path) { addItem(path); added = true; } else { lostPath = true; }
    }
    if (lostPath) {
      document.dispatchEvent(new CustomEvent('swarm:toast', {
        detail: { message: 'Files dropped from Finder arrive without their full path here. Use Choose files or Choose folder instead.' },
      }));
      return;
    }
    // 3. Links / text, only when no files came with the drop (no double add).
    if (!added) {
      const text = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
      if (text) text.split('\n').forEach((l) => addItem(l));
    }
  }, [addItem]);

  const p = testPrefix;
  return (
    <div className="flex flex-col min-w-0 min-h-0 flex-1" data-testid={`${p}-dropzone`}
      onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <div className="flex items-center gap-2">
        <input
          data-testid={`${p}-material-input`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitDraft(); } }}
          onBlur={commitDraft}
          placeholder="Paste a repo URL, a link or a local path"
          className="flex-1 min-w-0 h-10 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg px-3 text-body text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
        />
        <button type="button" data-testid={`${p}-material-add`} onClick={commitDraft}
          disabled={!draft.trim()} className="btn-md btn-secondary shrink-0 disabled:opacity-40">
          Add
        </button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button type="button" data-testid={`${p}-add-files`} onClick={() => pickAndAdd('file')}
          disabled={picking} className="btn-md btn-ghost disabled:opacity-50">
          <span className="material-symbols-outlined icon-sm">upload_file</span> Choose files
        </button>
        <button type="button" data-testid={`${p}-add-folder`} onClick={() => pickAndAdd('folder')}
          disabled={picking} className="btn-md btn-ghost disabled:opacity-50">
          <span className="material-symbols-outlined icon-sm">folder_open</span> Choose folder
        </button>
      </div>

      <div className="mt-4 flex-1 min-h-0 flex flex-col">
        {items.length === 0 ? (
          <div className="flex-1 min-h-[180px] rounded-xl border border-dashed border-[var(--color-border)] grid place-items-center text-center px-6"
            data-testid={`${p}-empty`}>
            <div>
              <span className="material-symbols-outlined icon-xl text-[var(--color-text-muted)]" aria-hidden>move_to_inbox</span>
              <div className="text-body text-[var(--color-text)] mt-1">Drop files or folders here</div>
              <div className="text-small text-[var(--color-text-muted)] mt-1 max-w-[420px]">{emptyText}</div>
            </div>
          </div>
        ) : (
          <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2">
            {items.map((it) => (
              <div key={it.id} data-testid={`${p}-item-${it.id}`} data-role={it.role}
                className="flex items-center gap-3 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg pl-3 pr-1.5 py-1.5">
                <span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">{KIND_ICON[it.displayKind] ?? 'description'}</span>
                <span className="flex-1 min-w-0 text-small break-all" title={it.value}>{it.value}</span>
                {it.checking && (
                  <span className="text-meta text-[var(--color-text-muted)] shrink-0" data-testid={`${p}-checking-${it.id}`}>checking…</span>
                )}
                <div role="radiogroup" aria-label="Role" className="flex shrink-0 rounded-md border border-[var(--color-border)] overflow-hidden">
                  {ROLES.map((r) => {
                    const on = it.role === r.role;
                    return (
                      <button key={r.role} type="button" role="radio" aria-checked={on} title={r.hint}
                        data-testid={`${p}-role-${it.id}-${r.role}`}
                        onClick={() => setRole(it.id, r.role)}
                        className={`h-7 px-2.5 text-meta ${on ? 'font-semibold' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
                        style={on ? { color: r.tone, background: `color-mix(in srgb, ${r.tone} 14%, transparent)` } : undefined}>
                        {r.label}
                      </button>
                    );
                  })}
                </div>
                <button aria-label={`Remove ${it.value}`} data-testid={`${p}-remove-${it.id}`}
                  onClick={() => removeItem(it.id)} className="icon-btn shrink-0">
                  <span className="material-symbols-outlined icon-sm">close</span>
                </button>
              </div>
            ))}
            <div className="text-meta text-[var(--color-text-muted)] mt-1">
              Each item was sorted by type. Hover a role to see what it means; drop more files here any time.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
