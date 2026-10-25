/**
 * NewBrainOverlay — the "New Brain" launcher (a one-shot birth gate).
 *
 * Opens on `swarm:show-new-brain` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). It COLLECTS three things — brain name, what-it-governs, and a
 * starter-material list where each item is rule-classified BY TYPE into
 * GOVERN/DISTILL/SHELF (a correctable pill) — then, on "Create Brain", builds ONE
 * categorized-manifest prompt and DISPATCHES it into a chat tab via `onDispatch`,
 * and closes. That is its entire life: it never reads/fetches the material, never
 * calls create_project, and never tracks progress (that runs in chat via
 * s_project-manager; the brain's status lives in Brain Hub). Re-opening starts a
 * fresh, empty birth — collection state resets whenever the overlay closes.
 *
 * Structurally isomorphic to JobsRunsOverlay: useExclusiveOverlay + shared
 * common/Modal size="fullscreen" + an `onDispatch: (prompt)=>boolean` prop whose
 * boolean gates the deferred (double-rAF) close (so an all-tabs-busy / unsent-draft
 * refusal keeps the launcher open with its toast visible).
 *
 * @exports NewBrainOverlay, NewBrainOverlayProps
 */
import { useCallback, useMemo, useState } from 'react';
import { useOverlayDraft } from '../../hooks/useOverlayDraft';
import {
  classifyStarterItem,
  buildBrainManifest,
  detectKind,
  previewSections,
  validateBrainName,
  type PreviewSection,
  type StarterItem,
  type StarterKind,
  type StarterRole,
} from '../../utils/newBrainDispatch';

export interface NewBrainContentProps {
  /** Lands a prompt into a chat tab (new or reused). Returns true on success;
   *  false (all tabs busy / unsent draft) must keep the launcher open. From the
   *  OverlayHost ctx bridge = ChatPage's handleDispatchJobPrompt. */
  onDispatch: (prompt: string, opts?: { autoSend?: boolean }) => boolean;
  /** Close the surface (host's closeOverlay). */
  close: () => void;
  /** Names of the Brains that already exist (case-insensitive collision check). */
  existingNames?: string[];
}

/** The six sections in Brain-map order, with the hex angle used by the preview. */
const PREVIEW: { key: PreviewSection; label: string; deg: number }[] = [
  { key: 'identity', label: 'Identity', deg: -120 },
  { key: 'knowledge', label: 'Knowledge', deg: -60 },
  { key: 'gates', label: 'Gates', deg: 0 },
  { key: 'capabilities', label: 'Capabilities', deg: 60 },
  { key: 'refresher', label: 'Refresher', deg: 120 },
  { key: 'delivery', label: 'Delivery', deg: 180 },
];

function hexPoints(x: number, y: number, r: number): string {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    p.push(`${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`);
  }
  return p.join(' ');
}

/** The Brain preview: the six-petal flower of the Brain map. A solid petal is a
 *  section this material fills at creation; a dashed one grows later in use. */
function BrainPreview({ name, filled }: { name: string; filled: Record<PreviewSection, boolean> }) {
  const R = 44, D = Math.sqrt(3) * R + 4, cx = 150, cy = 130;
  const t = name.trim();
  const label = t ? (t.length > 9 ? t.slice(0, 8) + '…' : t) : 'Brain';
  return (
    <svg viewBox="0 0 300 260" width="100%" role="img" aria-label="Brain preview" data-testid="new-brain-preview">
      {PREVIEW.map(({ key, label: l, deg }) => {
        const a = (deg * Math.PI) / 180, x = cx + D * Math.cos(a), y = cy + D * Math.sin(a), on = filled[key];
        return (
          <g key={key} data-testid={`new-brain-preview-${key}`} data-on={on ? 'yes' : 'no'}>
            <polygon points={hexPoints(x, y, R)} strokeWidth={1.3} strokeDasharray={on ? undefined : '3 3'}
              className={on ? 'fill-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] stroke-[var(--color-primary)]'
                : 'fill-transparent stroke-[var(--color-text-muted)]'} />
            <text x={x} y={y + 4} textAnchor="middle"
              className={`text-meta ${on ? 'font-semibold fill-[var(--color-text)]' : 'fill-[var(--color-text-muted)]'}`}>{l}</text>
          </g>
        );
      })}
      <polygon points={hexPoints(cx, cy, R)} className="fill-[var(--color-primary)] stroke-[var(--color-primary)]" />
      <text x={cx} y={cy + 4} textAnchor="middle" className="text-small font-semibold fill-[var(--color-on-accent)]">{label}</text>
    </svg>
  );
}

const NEXT_STEPS: { n: number; title: string; body: string }[] = [
  { n: 1, title: 'Set up together in chat', body: 'Assets, knowledge, binding, then the Project Agent the agent proposes for you to confirm.' },
  { n: 2, title: 'Grows in Brain Hub', body: 'It shows on the Brain map as being born; each petal fills as its section is written.' },
  { n: 3, title: 'Ready', body: 'When the check passes, open its map or describe a first change for a Run.' },
];

/** The three roles, as a segmented choice on each material row. */
const ROLES: { role: StarterRole; label: string; hint: string; tone: string }[] = [
  { role: 'GOVERN', label: 'Govern', hint: 'An asset it manages: bound, and a repo gets code-intel', tone: 'var(--color-primary)' },
  { role: 'DISTILL', label: 'Distill', hint: 'Read once and distilled into its knowledge', tone: 'var(--color-status-warning)' },
  { role: 'SHELF', label: 'Shelf', hint: 'Kept for reference, read when needed', tone: 'var(--color-text-muted)' },
];

const KIND_ICON: Record<string, string> = {
  repo: 'code', folder: 'folder', file: 'description', link: 'link', text: 'notes',
};

let itemSeq = 0;

interface RowItem extends StarterItem {
  id: number;
  displayKind: string;
}

/** The persisted draft shape for the New Brain form (useOverlayDraft). */
interface BrainForm {
  name: string;
  items: RowItem[];
  draft: string;
}
const EMPTY_FORM: BrainForm = { name: '', items: [], draft: '' };

/**
 * NewBrainContent — the "grow a new brain" launcher content (M3: migrated to the
 * OverlayHost registry). Fresh, empty birth every open is now automatic: the host
 * MOUNTS this component fresh on each open and UNMOUNTS it on close (renderedId→null
 * after the exit transition), so component-local state starts empty every time — the
 * former reset-on-raw-event hack (which existed because the old overlay stayed mounted
 * and `open` didn't observably transition on rapid reopen) is no longer needed.
 */
export function NewBrainContent({ onDispatch, close, existingNames = [] }: NewBrainContentProps) {
  // Draft memory (run_754fe3e8): the overlay UNMOUNTS on close (OverlayHost
  // renderedId→null), so component-local useState is destroyed — an accidental
  // Esc used to discard everything. useOverlayDraft parks the form in a
  // module-level in-memory store (NOT localStorage — items are local abs paths),
  // restored on reopen, cleared ONLY on a successful dispatch (Esc/backdrop keep).
  const [form, setForm, clearForm] = useOverlayDraft<BrainForm>('new-brain', EMPTY_FORM);
  const { name, items, draft } = form;
  const setName = useCallback((v: string) => setForm((f) => ({ ...f, name: v })), [setForm]);
  const setItems = useCallback(
    (up: RowItem[] | ((prev: RowItem[]) => RowItem[])) =>
      setForm((f) => ({ ...f, items: typeof up === 'function' ? up(f.items) : up })),
    [setForm],
  );
  const setDraft = useCallback((v: string) => setForm((f) => ({ ...f, draft: v })), [setForm]);
  // Guards against a second native dialog opening while one is already pending
  // (rapid double-click). Mirrors LibraryOverlay.AddFolderButton's `busy` flag.
  // NOT part of the persisted draft — transient in-flight UI state.
  const [picking, setPicking] = useState(false);

  const addItem = useCallback((raw: string, kindOverride?: StarterKind) => {
    const value = raw.trim();
    if (!value) return;
    setItems((prev) => {
      if (prev.some((it) => it.value === value)) return prev; // dedupe
      const kind = kindOverride ?? detectKind(value);
      const role = classifyStarterItem({ value, kind });
      return [...prev, { id: ++itemSeq, value, kind, role, displayKind: kind }];
    });
  }, [setItems]);

  const commitDraft = useCallback(() => {
    // Allow pasting several lines at once — one item per line.
    draft.split('\n').forEach((line) => addItem(line));
    setDraft('');
  }, [draft, addItem]);

  // The REAL upload affordance: open the native OS file browser via the Tauri
  // dialog plugin, which returns ABSOLUTE paths (unlike an OS drag, whose `.path`
  // is undefined in the webview → only a basename the agent can't resolve). This
  // clones LibraryOverlay.AddFolderButton: dynamic import + try/catch → toast
  // fallback so a non-Tauri/dev environment (where the import rejects) degrades
  // gracefully instead of dead-ending the click.
  //   - mode 'file'   → open({ multiple:true })            → string[]
  //   - mode 'folder' → open({ directory:true })           → string
  //   - cancel        → null (nothing added)
  // Each picked path feeds the SAME addItem() with a kind hint, so classification
  // (file→DISTILL, folder→SHELF) and dispatch are identical to a pasted path.
  const pickAndAdd = useCallback(
    async (mode: 'file' | 'folder') => {
      if (picking) return; // a dialog is already open — ignore the double-click
      setPicking(true);
      try {
        const { open } = await import('@tauri-apps/plugin-dialog');
        const picked = await open({
          multiple: mode === 'file',
          directory: mode === 'folder',
          title: mode === 'file' ? 'Add files to this brain' : 'Add a folder to this brain',
        });
        // Normalize string | string[] | null → a path array (cancel = []).
        const paths = Array.isArray(picked) ? picked : picked ? [picked] : [];
        paths.forEach((p) => addItem(p, mode));
      } catch {
        // dialog unavailable (non-Tauri/dev) — fall back to the type/paste route.
        document.dispatchEvent(
          new CustomEvent('swarm:toast', {
            detail: { message: 'File browser unavailable here — paste a full path into the field instead.' },
          }),
        );
      } finally {
        setPicking(false);
      }
    },
    [addItem, picking],
  );

  const removeItem = useCallback((id: number) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, [setItems]);

  const setRole = useCallback((id: number, role: StarterRole) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, role } : it)));
  }, [setItems]);

  const nameError = useMemo(() => validateBrainName(name, existingNames), [name, existingNames]);

  // Create: build ONE manifest → dispatch → close ONLY if it landed (F4: the
  // boolean guards the deferred close; a refusal keeps the launcher open).
  // autoSend: clicking Create IS the send — the person already reviewed the form;
  // making them press Send again on a generated prompt was a second, empty step.
  const handleCreate = useCallback(() => {
    if (validateBrainName(name, existingNames)) return;
    const manifest = buildBrainManifest(
      name,
      items.map(({ value, kind, role }) => ({ value, kind, role })),
    );
    // Gate-2 #13: a THROWING onDispatch (addTab/CustomEvent could raise) must be
    // treated as a failed dispatch — keep the launcher open so work isn't lost,
    // never let the exception dead-end the click with no feedback + no close.
    let landed = false;
    try {
      landed = onDispatch(manifest, { autoSend: true });
    } catch {
      landed = false;
    }
    if (landed) {
      // Work handed off → discard the parked draft so the NEXT open is a fresh
      // birth. A refusal (all tabs busy / draft guard) or a THROW keeps the draft
      // so nothing is lost — only a successful landing clears (Esc/backdrop too keep).
      // ORDER: close FIRST, clear AFTER (inside the same rAF chain). clearForm()
      // runs setValueState(EMPTY_FORM) on the still-mounted surface — the host keeps
      // it mounted ~300ms for the exit transition — so clearing BEFORE close would
      // render the form empty and animate THAT blank state out (flash of empty form
      // on a successful Create). Clearing after close() wipes the parked draft before
      // any reopen without the visible form ever rendering empty during the fade.
      requestAnimationFrame(() => requestAnimationFrame(() => { close(); clearForm(); }));
    }
  }, [name, items, existingNames, onDispatch, close, clearForm]);

  const filled = useMemo(() => previewSections(items), [items]);
  const assetCount = items.filter((it) => it.role === 'GOVERN').length;

  const onDropZoneDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      // Path 1 (PRIMARY — the reliable one): a drag from the app's own Workspace
      // Explorer carries `application/json` = a FileTreeItem with a REAL workspace
      // path (this is the ChatDropZone precedent). Unlike ChatDropZone we accept
      // BOTH file AND directory — a directory is valid starter material (→ SHELF).
      // The OS `.path` route below is unreliable in the Tauri webview (`.path` is
      // non-standard + dragDropEnabled isn't configured), so this internal-drag
      // path is how "material already in the workspace" actually gets in.
      const jsonData = e.dataTransfer.getData('application/json');
      if (jsonData) {
        try {
          const fileData = JSON.parse(jsonData) as { path?: string; name?: string; type?: string };
          const val = fileData.path || fileData.name;
          if (val) {
            // A directory drag → force SHELF via the folder kind (its path has no
            // trailing slash, so detectKind alone could mis-read it).
            const kind = fileData.type === 'directory' ? 'folder' : undefined;
            addItem(val, kind);
          }
        } catch {
          /* malformed payload — ignore, nothing to add */
        }
        return; // internal drag handled; don't double-add from files/text
      }
      // Path 2 (fallback): native OS file drop. In the Tauri/browser webview `.path`
      // is undefined, so only the basename arrives — a path the agent cannot open.
      // Never add a basename as if it were a path: say so and point at the picker.
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
      // Path 3: text/URI drop (links, pasted text) — only if no files came with it,
      // so a single drop carrying BOTH files and a uri-list can't double-add (#4c).
      if (!added) {
        const text = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
        if (text) text.split('\n').forEach((l) => addItem(l));
      }
    },
    [addItem],
  );

  const canCreate = !!name.trim() && !nameError;

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="new-brain-overlay">
      <div className="flex-1 min-h-0 grid grid-cols-[340px_1fr]">
        {/* ── Left: who it is ─────────────────────────────────────────────── */}
        <div className="p-6 border-r border-[var(--color-border)] flex flex-col gap-5 overflow-y-auto">
          <div>
            <label htmlFor="new-brain-name-input" className="block text-small font-semibold text-[var(--color-text)] mb-1.5">Name</label>
            <input
              id="new-brain-name-input"
              data-testid="new-brain-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Payments"
              aria-invalid={!!nameError}
              aria-describedby="new-brain-name-help"
              className={`w-full h-10 bg-[var(--color-input-bg)] border rounded-lg px-3 text-body text-[var(--color-text)] outline-none focus:border-[var(--color-primary)] ${
                nameError ? 'border-[var(--color-status-error)]' : 'border-[var(--color-border)]'}`}
            />
            <div id="new-brain-name-help" data-testid="new-brain-name-help"
              className={`mt-1.5 text-meta ${nameError ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}>
              {nameError ?? 'Also its folder name in your workspace.'}
            </div>
          </div>

          <div>
            <div className="text-small font-semibold text-[var(--color-text)]">Preview</div>
            <BrainPreview name={name} filled={filled} />
            <div className="text-small" data-testid="new-brain-assets">
              {assetCount > 0
                ? <>Governs <span className="font-semibold">{assetCount}</span> asset{assetCount === 1 ? '' : 's'}. The agent proposes its Project Agent from the repo and CI.</>
                : <><span className="font-semibold">Pure-knowledge Brain.</span> Nothing to bind, and that is complete.</>}
            </div>
            <div className="text-meta text-[var(--color-text-muted)] mt-1">Solid sections come from this material. Dashed ones grow as you work in it.</div>
          </div>
        </div>

        {/* ── Right: what it starts from ──────────────────────────────────── */}
        <div
          className="p-6 flex flex-col min-w-0 min-h-0"
          data-testid="new-brain-dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDropZoneDrop}
        >
          <div className="flex items-baseline justify-between">
            <span className="text-small font-semibold text-[var(--color-text)]">Starter material</span>
            <span className="text-meta text-[var(--color-text-muted)]">Optional</span>
          </div>

          <div className="mt-2 flex items-center gap-2">
            <input
              data-testid="new-brain-material-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitDraft(); } }}
              onBlur={commitDraft}
              placeholder="Paste a repo URL, a link or a local path"
              className="flex-1 min-w-0 h-10 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg px-3 text-body text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
            />
            <button type="button" data-testid="new-brain-material-add" onClick={commitDraft}
              disabled={!draft.trim()} className="btn-md btn-secondary shrink-0 disabled:opacity-40">
              Add
            </button>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <button type="button" data-testid="new-brain-add-files" onClick={() => pickAndAdd('file')}
              disabled={picking} className="btn-md btn-ghost disabled:opacity-50">
              <span className="material-symbols-outlined icon-sm">upload_file</span> Choose files
            </button>
            <button type="button" data-testid="new-brain-add-folder" onClick={() => pickAndAdd('folder')}
              disabled={picking} className="btn-md btn-ghost disabled:opacity-50">
              <span className="material-symbols-outlined icon-sm">folder_open</span> Choose folder
            </button>
          </div>

          <div className="mt-4 flex-1 min-h-0 flex flex-col">
            {items.length === 0 ? (
              <div className="flex-1 min-h-[220px] rounded-xl border border-dashed border-[var(--color-border)] grid place-items-center text-center px-6"
                data-testid="new-brain-empty">
                <div>
                  <span className="material-symbols-outlined text-[var(--color-text-muted)]" aria-hidden>move_to_inbox</span>
                  <div className="text-body text-[var(--color-text)] mt-1">Drop files or folders here</div>
                  <div className="text-small text-[var(--color-text-muted)] mt-1 max-w-[420px]">
                    A repo becomes an asset it governs, documents are distilled into knowledge, folders are kept for reference.
                    With nothing at all it starts as a pure-knowledge Brain.
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2">
                {items.map((it) => (
                  <div key={it.id} data-testid={`new-brain-item-${it.id}`} data-role={it.role}
                    className="flex items-center gap-3 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg pl-3 pr-1.5 py-1.5">
                    <span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">{KIND_ICON[it.displayKind] ?? 'description'}</span>
                    <span className="flex-1 min-w-0 text-small break-all" title={it.value}>{it.value}</span>
                    <div role="radiogroup" aria-label="Role" className="flex shrink-0 rounded-md border border-[var(--color-border)] overflow-hidden">
                      {ROLES.map((r) => {
                        const on = it.role === r.role;
                        return (
                          <button key={r.role} type="button" role="radio" aria-checked={on} title={r.hint}
                            data-testid={`new-brain-role-${it.id}-${r.role}`}
                            onClick={() => setRole(it.id, r.role)}
                            className={`h-7 px-2.5 text-meta ${on ? 'font-semibold' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
                            style={on ? { color: r.tone, background: `color-mix(in srgb, ${r.tone} 14%, transparent)` } : undefined}>
                            {r.label}
                          </button>
                        );
                      })}
                    </div>
                    <button aria-label={`Remove ${it.value}`} data-testid={`new-brain-remove-${it.id}`}
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
      </div>

      {/* ── Footer: what happens on Create + the one action ─────────────────── */}
      <div className="px-6 py-4 border-t border-[var(--color-border)] bg-[var(--color-bg-chrome)] flex items-center gap-6">
        <ol className="flex-1 grid grid-cols-3 gap-4" data-testid="new-brain-next-steps">
          {NEXT_STEPS.map((s) => (
            <li key={s.n} className="flex gap-2.5 min-w-0">
              <span className="shrink-0 inline-flex w-5 h-5 items-center justify-center rounded-full text-meta font-semibold bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] text-[var(--color-primary)]">{s.n}</span>
              <span className="min-w-0">
                <span className="block text-small font-semibold">{s.title}</span>
                <span className="block text-meta text-[var(--color-text-muted)]">{s.body}</span>
              </span>
            </li>
          ))}
        </ol>
        <button
          data-testid="new-brain-create"
          onClick={handleCreate}
          disabled={!canCreate}
          title={canCreate ? 'Starts the setup in a chat tab' : 'Give it a valid name first'}
          className="btn-lg btn-primary shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Create Brain
        </button>
      </div>
    </div>
  );
}
