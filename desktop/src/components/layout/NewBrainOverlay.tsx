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
  type PreviewSection,
  type StarterItem,
  type StarterKind,
  type StarterRole,
} from '../../utils/newBrainDispatch';

export interface NewBrainContentProps {
  /** Lands a prompt into a chat tab (new or reused). Returns true on success;
   *  false (all tabs busy / unsent draft) must keep the launcher open. From the
   *  OverlayHost ctx bridge = ChatPage's handleDispatchJobPrompt. */
  onDispatch: (prompt: string) => boolean;
  /** Close the surface (host's closeOverlay). */
  close: () => void;
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
  { n: 1, title: 'Set it up together in chat', body: 'Confirm assets → write the knowledge → bind → the agent proposes the Project Agent from it, you confirm → verify. Unreachable links and conflicts are asked there.' },
  { n: 2, title: 'Watch it grow in Brain Hub', body: 'It appears on the Brain map as soon as the skeleton exists, and its petals fill as each section is written.' },
  { n: 3, title: 'Ready', body: 'When the check passes, open its Brain map or describe a first change for a Run.' },
];

const ROLE_STYLE: Record<StarterRole, { label: string; cls: string }> = {
  GOVERN: { label: 'GOVERN', cls: 'text-[var(--color-primary)] border-[var(--color-primary)]/40 bg-[var(--color-primary)]/12' },
  DISTILL: { label: 'DISTILL', cls: 'text-[var(--color-status-warning)] border-[color-mix(in_srgb,var(--color-status-warning)_40%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_12%,transparent)]' },
  SHELF: { label: 'SHELF', cls: 'text-[var(--color-primary)] border-[color-mix(in_srgb,var(--color-primary)_40%,transparent)] bg-[color-mix(in_srgb,var(--color-primary)_12%,transparent)]' },
};

const ROLE_CYCLE: StarterRole[] = ['GOVERN', 'DISTILL', 'SHELF'];

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
export function NewBrainContent({ onDispatch, close }: NewBrainContentProps) {
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

  const cycleRole = useCallback((id: number) => {
    setItems((prev) =>
      prev.map((it) =>
        it.id === id
          ? { ...it, role: ROLE_CYCLE[(ROLE_CYCLE.indexOf(it.role) + 1) % ROLE_CYCLE.length] }
          : it,
      ),
    );
  }, [setItems]);

  // Create: build ONE manifest → dispatch → close ONLY if it landed (F4: the
  // boolean guards the deferred close; a refusal keeps the launcher open).
  const handleCreate = useCallback(() => {
    const manifest = buildBrainManifest(
      name,
      items.map(({ value, kind, role }) => ({ value, kind, role })),
    );
    // Gate-2 #13: a THROWING onDispatch (addTab/CustomEvent could raise) must be
    // treated as a failed dispatch — keep the launcher open so work isn't lost,
    // never let the exception dead-end the click with no feedback + no close.
    let landed = false;
    try {
      landed = onDispatch(manifest);
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
  }, [name, items, onDispatch, close, clearForm]);

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
      // Path 2 (fallback): native OS file drop. NOTE: `.path` is undefined in the
      // Tauri/browser webview → we can only get the basename, which the agent
      // can't resolve. Kept as best-effort; the reliable routes are Path 1 and
      // pasting a full path/URL into the input.
      let added = false;
      for (const f of Array.from(e.dataTransfer.files ?? [])) {
        const path = (f as File & { path?: string }).path || f.name;
        addItem(path);
        added = true;
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

  return (
      <div className="flex-1 min-h-0 flex flex-col" data-testid="new-brain-overlay">
        {/* Declaration */}
        <div className="px-6 pt-4 pb-3 border-b border-[var(--color-border)]">
          <p className="text-body text-[var(--color-text-muted)] max-w-[680px]">
            A brain is <span className="text-[var(--color-text)] font-semibold">one project&apos;s domain that gets smarter every time you work in it</span>: what it knows, how it delivers, and what it learns. Name it and give me what you already have. I set it up with you in chat.
          </p>
        </div>

        {/* Body: left (name + live Brain preview) · right (starter material) */}
        <div className="flex-1 min-h-0 grid grid-cols-[300px_1fr]">
          <div className="p-5 border-r border-[var(--color-border)] flex flex-col gap-4 overflow-y-auto">
            <div>
              <label htmlFor="new-brain-name-input" className="block text-meta font-semibold tracking-wide text-[var(--color-text-muted)] mb-1.5">NAME</label>
              <input
                id="new-brain-name-input"
                data-testid="new-brain-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Acme Payments"
                className="w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-body text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <div>
              <div className="text-meta font-semibold tracking-wide text-[var(--color-text-muted)]">BRAIN PREVIEW</div>
              <BrainPreview name={name} filled={filled} />
              <div className="text-small" data-testid="new-brain-assets">
                {assetCount > 0
                  ? <>It governs <span className="font-semibold">{assetCount}</span> asset{assetCount === 1 ? '' : 's'}. A repo gets code-intel, and the agent proposes the Project Agent from its CI.</>
                  : <><span className="font-semibold">A pure-knowledge brain.</span> No asset to bind, and that is complete.</>}
              </div>
              <div className="text-meta text-[var(--color-text-muted)] mt-1">Solid: filled by this material. Dashed: grows later as you work in it.</div>
            </div>
          </div>

          <div className="p-5 flex flex-col min-w-0">
            <div className="flex items-baseline justify-between mb-2">
              <span className="text-meta font-semibold tracking-wide text-[var(--color-text-muted)]">STARTER MATERIAL</span>
              <span className="text-meta text-[var(--color-text-faint)]">optional</span>
            </div>

            {/* Acquisition zone — two DISTINCT input methods, not one box (a local
                file and a URL are different acquisition modes). Local: native OS
                file browser → absolute paths. Link: a free-text field (paste a
                URL / repo / path, or type). */}
            <div className="flex flex-col gap-2 mb-3">
              <div className="flex items-center gap-2">
                <span className="text-meta font-semibold tracking-wide uppercase text-[var(--color-text-faint)] w-[42px] shrink-0">Local</span>
                <button
                  type="button"
                  data-testid="new-brain-add-files"
                  onClick={() => pickAndAdd('file')}
                  disabled={picking}
                  className="btn-md btn-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="material-symbols-outlined icon-sm">description</span> Add files…
                </button>
                <button
                  type="button"
                  data-testid="new-brain-add-folder"
                  onClick={() => pickAndAdd('folder')}
                  disabled={picking}
                  className="btn-md btn-secondary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="material-symbols-outlined icon-sm">folder</span> Add folder…
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-meta font-semibold tracking-wide uppercase text-[var(--color-text-faint)] w-[42px] shrink-0">Link</span>
                <input
                  data-testid="new-brain-material-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitDraft(); } }}
                  onBlur={commitDraft}
                  placeholder="https://…  ·  git@…  ·  or paste a path"
                  className="flex-1 min-w-0 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg px-3 py-1.5 text-small text-[var(--color-text)] outline-none focus:border-[var(--color-primary)] placeholder:text-[var(--color-text-faint)]"
                />
                <button
                  type="button"
                  data-testid="new-brain-material-add"
                  onClick={commitDraft}
                  className="btn-md btn-secondary transition-colors shrink-0"
                >
                  Add
                </button>
              </div>
            </div>

            {/* Collected list — also a drop target (internal Explorer drag = the
                reliable path; OS drag is best-effort). The drag cue lives in the
                empty state, where it's contextual, not a permanent shout. */}
            <div
              data-testid="new-brain-dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDropZoneDrop}
              className="flex-1 min-h-[160px] flex flex-col"
            >
              <div className="flex-1 flex flex-col gap-1.5 overflow-y-auto">
                {items.length === 0 && (
                  <div className="text-small text-[var(--color-text-muted)] py-2">
                    No material yet. That is fine: it becomes a pure-knowledge brain you grow in chat.
                  </div>
                )}
                {items.map((it) => (
                  <div key={it.id} className="flex items-center gap-2.5 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg px-3 py-2">
                    <span className="w-4 text-center material-symbols-outlined icon-sm text-[var(--color-text-muted)]">{KIND_ICON[it.displayKind] ?? 'description'}</span>
                    <span className="flex-1 min-w-0 truncate text-small" title={it.value}>{it.value}</span>
                    <button
                      data-testid={`new-brain-role-${it.id}`}
                      onClick={() => cycleRole(it.id)}
                      className={`btn-sm btn-secondary tracking-wide ${ROLE_STYLE[it.role].cls}`}
                      title="Click to change the role"
                    >
                      {ROLE_STYLE[it.role].label} ▾
                    </button>
                    <button
                      aria-label="remove"
                      data-testid={`new-brain-remove-${it.id}`}
                      onClick={() => removeItem(it.id)}
                      className="text-[var(--color-text-faint)] hover:text-[var(--color-text)] text-body"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
              <div className="mt-2 border border-dashed border-[var(--color-border)] rounded-lg py-2 text-center text-meta text-[var(--color-text-muted)]">
                Drag files or folders here. Each is sorted by type; click its role to change it.
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-meta text-[var(--color-text-muted)]">
                <span><span className={`px-1.5 rounded ${ROLE_STYLE.GOVERN.cls}`}>GOVERN</span> an asset it manages, bound</span>
                <span><span className={`px-1.5 rounded ${ROLE_STYLE.DISTILL.cls}`}>DISTILL</span> read once, distilled into knowledge</span>
                <span><span className={`px-1.5 rounded ${ROLE_STYLE.SHELF.cls}`}>SHELF</span> kept for reference</span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer: what happens next + dispatch */}
        <div className="px-6 py-3.5 border-t border-[var(--color-border)] bg-[var(--color-bg-chrome)]">
          <div className="text-meta font-semibold tracking-wide text-[var(--color-text-muted)] mb-1.5">WHAT HAPPENS ON CREATE</div>
          <div className="grid grid-cols-3 gap-2" data-testid="new-brain-next-steps">
            {NEXT_STEPS.map((s) => (
              <div key={s.n} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="inline-flex w-[18px] h-[18px] items-center justify-center rounded-full text-meta font-semibold bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] text-[var(--color-primary)]">{s.n}</span>
                  <span className="text-small font-semibold">{s.title}</span>
                </div>
                <div className="text-meta text-[var(--color-text-muted)] mt-1">{s.body}</div>
              </div>
            ))}
          </div>
          <div className="flex justify-end mt-3">
            <button
              data-testid="new-brain-create"
              onClick={handleCreate}
              disabled={!name.trim()}
              className="btn-lg btn-primary disabled:opacity-40 disabled:cursor-not-allowed hover:brightness-110 transition"
            >
              Create in chat →
            </button>
          </div>
        </div>
      </div>
  );
}
