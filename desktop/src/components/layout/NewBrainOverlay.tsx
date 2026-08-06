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
import { useCallback, useState } from 'react';
import {
  classifyStarterItem,
  buildBrainManifest,
  detectKind,
  type GovernsKind,
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

interface GovernsOption {
  kind: GovernsKind;
  icon: string;
  label: string;
}

const GOVERNS_OPTIONS: GovernsOption[] = [
  { kind: 'codebase', icon: '📦', label: 'A codebase' },
  { kind: 'data', icon: '📊', label: 'A data source' },
  { kind: 'documents', icon: '📚', label: 'Documents' },
  { kind: 'service', icon: '🔌', label: 'A service / process' },
  { kind: 'idea', icon: '💭', label: 'Just an idea — nothing yet' },
];

const ROLE_STYLE: Record<StarterRole, { label: string; cls: string }> = {
  GOVERN: { label: 'GOVERN', cls: 'text-[var(--color-primary)] border-[var(--color-primary)]/40 bg-[var(--color-primary)]/12' },
  DISTILL: { label: 'DISTILL', cls: 'text-[#c9a15f] border-[#c9a15f]/40 bg-[#c9a15f]/12' },
  SHELF: { label: 'SHELF', cls: 'text-[#7c9bd6] border-[#7c9bd6]/40 bg-[#7c9bd6]/12' },
};

const ROLE_CYCLE: StarterRole[] = ['GOVERN', 'DISTILL', 'SHELF'];

const KIND_ICON: Record<string, string> = {
  repo: '📦', folder: '📁', file: '📄', link: '🔗', text: '📝',
};

let itemSeq = 0;

interface RowItem extends StarterItem {
  id: number;
  displayKind: string;
}

/**
 * NewBrainContent — the "grow a new brain" launcher content (M3: migrated to the
 * OverlayHost registry). Fresh, empty birth every open is now automatic: the host
 * MOUNTS this component fresh on each open and UNMOUNTS it on close (renderedId→null
 * after the exit transition), so component-local state starts empty every time — the
 * former reset-on-raw-event hack (which existed because the old overlay stayed mounted
 * and `open` didn't observably transition on rapid reopen) is no longer needed.
 */
export function NewBrainContent({ onDispatch, close }: NewBrainContentProps) {
  const [name, setName] = useState('');
  const [governs, setGoverns] = useState<GovernsKind>('codebase');
  const [items, setItems] = useState<RowItem[]>([]);
  const [draft, setDraft] = useState('');
  // Guards against a second native dialog opening while one is already pending
  // (rapid double-click). Mirrors LibraryOverlay.AddFolderButton's `busy` flag.
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
  }, []);

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
  }, []);

  const cycleRole = useCallback((id: number) => {
    setItems((prev) =>
      prev.map((it) =>
        it.id === id
          ? { ...it, role: ROLE_CYCLE[(ROLE_CYCLE.indexOf(it.role) + 1) % ROLE_CYCLE.length] }
          : it,
      ),
    );
  }, []);

  // Create: build ONE manifest → dispatch → close ONLY if it landed (F4: the
  // boolean guards the deferred close; a refusal keeps the launcher open).
  const handleCreate = useCallback(() => {
    const manifest = buildBrainManifest(
      name,
      governs,
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
    if (landed) requestAnimationFrame(() => requestAnimationFrame(() => close()));
  }, [name, governs, items, onDispatch, close]);

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
        {/* Declaration + lifecycle ribbon */}
        <div className="px-6 pt-4 pb-3 border-b border-[var(--color-border)]">
          <p className="text-[13px] text-[var(--color-text-muted)] max-w-[640px]">
            A brain is a <span className="text-[var(--color-text)] font-semibold">domain that gets smarter every time you work in it</span> — its knowledge, skills &amp; memory compound. Not a folder. Name it, give me what you already have; I set it up with you in chat.
          </p>
          <div className="mt-2.5 inline-flex items-center gap-2 text-[11px] text-[var(--color-text-muted)] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-full px-3 py-1">
            <span><span className="text-[var(--color-primary)] font-mono">1</span> tell me</span>
            <span className="text-[var(--color-text-faint)]">→</span>
            <span><span className="text-[var(--color-primary)] font-mono">2</span> Create — I take it to chat</span>
            <span className="text-[var(--color-text-faint)]">→</span>
            <span><span className="text-[var(--color-primary)] font-mono">3</span> it lives in Brain Hub</span>
          </div>
        </div>

        {/* Body: left (name + governs) · right (starter material) */}
        <div className="flex-1 min-h-0 grid grid-cols-[300px_1fr]">
          <div className="p-5 border-r border-[var(--color-border)] flex flex-col gap-5 overflow-y-auto">
            <div>
              <label htmlFor="new-brain-name-input" className="block text-[11px] font-semibold tracking-wide text-[var(--color-text-muted)] mb-1.5">NAME</label>
              <input
                id="new-brain-name-input"
                data-testid="new-brain-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Acme Payments"
                className="w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <fieldset className="border-0 p-0 m-0">
              <legend className="block text-[11px] font-semibold tracking-wide text-[var(--color-text-muted)] mb-1.5 p-0">WHAT IT GOVERNS</legend>
              <div className="flex flex-col gap-1.5">
                {GOVERNS_OPTIONS.map((opt) => (
                  <button
                    key={opt.kind}
                    type="button"
                    data-testid={`new-brain-governs-${opt.kind}`}
                    aria-pressed={governs === opt.kind}
                    onClick={() => setGoverns(opt.kind)}
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border text-[12.5px] transition-colors text-left ${
                      governs === opt.kind
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-text)]'
                        : 'border-[var(--color-border)] bg-[var(--color-card)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                    } ${opt.kind === 'idea' ? 'border-dashed' : ''}`}
                  >
                    <span className="w-5 text-center">{opt.icon}</span> {opt.label}
                  </button>
                ))}
              </div>
            </fieldset>
          </div>

          <div className="p-5 flex flex-col min-w-0">
            <div className="flex items-baseline justify-between mb-2">
              <span className="text-[11px] font-semibold tracking-wide text-[var(--color-text-muted)]">STARTER MATERIAL</span>
              <span className="text-[10px] text-[var(--color-text-faint)]">optional</span>
            </div>

            {/* Acquisition zone — two DISTINCT input methods, not one box (a local
                file and a URL are different acquisition modes). Local: native OS
                file browser → absolute paths. Link: a free-text field (paste a
                URL / repo / path, or type). */}
            <div className="flex flex-col gap-2 mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold tracking-wide uppercase text-[var(--color-text-faint)] w-[42px] shrink-0">Local</span>
                <button
                  type="button"
                  data-testid="new-brain-add-files"
                  onClick={() => pickAndAdd('file')}
                  disabled={picking}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-card)] text-[12.5px] font-medium text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="text-sm leading-none">📄</span> Add files…
                </button>
                <button
                  type="button"
                  data-testid="new-brain-add-folder"
                  onClick={() => pickAndAdd('folder')}
                  disabled={picking}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-card)] text-[12.5px] font-medium text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="text-sm leading-none">📁</span> Add folder…
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold tracking-wide uppercase text-[var(--color-text-faint)] w-[42px] shrink-0">Link</span>
                <input
                  data-testid="new-brain-material-input"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitDraft(); } }}
                  onBlur={commitDraft}
                  placeholder="https://…  ·  git@…  ·  or paste a path"
                  className="flex-1 min-w-0 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg px-3 py-1.5 text-[12px] text-[var(--color-text)] outline-none focus:border-[var(--color-primary)] placeholder:text-[var(--color-text-faint)]"
                />
                <button
                  type="button"
                  data-testid="new-brain-material-add"
                  onClick={commitDraft}
                  className="px-3 py-1.5 rounded-lg border border-[var(--color-border-strong)] bg-[var(--color-card)] text-[12px] font-semibold text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors shrink-0"
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
              className="flex-1 border border-dashed border-[var(--color-border-strong)] rounded-xl bg-[var(--color-bg)] p-3 flex flex-col min-h-[220px]"
            >
              <div className="flex-1 flex flex-col gap-1.5 overflow-y-auto">
                {items.length === 0 && (
                  <div className="flex-1 grid place-items-center text-center text-[var(--color-text-faint)]">
                    <div>
                      <div className="text-2xl opacity-60 mb-1.5">⤵</div>
                      <div className="text-[12px] text-[var(--color-text-muted)] font-medium">Add files, a folder, or a link above</div>
                      <div className="text-[10.5px] mt-1">…or drag files &amp; folders here · sorted by type, click a pill to change the role</div>
                    </div>
                  </div>
                )}
                {items.map((it) => (
                  <div key={it.id} className="flex items-center gap-2.5 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg px-3 py-2">
                    <span className="w-4 text-center text-sm">{KIND_ICON[it.displayKind] ?? '📄'}</span>
                    <span className="flex-1 min-w-0 truncate text-[12px]" title={it.value}>{it.value}</span>
                    <button
                      data-testid={`new-brain-role-${it.id}`}
                      onClick={() => cycleRole(it.id)}
                      className={`text-[10px] font-bold tracking-wide px-2 py-1 rounded border ${ROLE_STYLE[it.role].cls}`}
                      title="Click to change the role"
                    >
                      {ROLE_STYLE[it.role].label} ▾
                    </button>
                    <button
                      aria-label="remove"
                      data-testid={`new-brain-remove-${it.id}`}
                      onClick={() => removeItem(it.id)}
                      className="text-[var(--color-text-faint)] hover:text-[var(--color-text)] text-sm"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Footer: dispatch */}
        <div className="px-6 py-3.5 border-t border-[var(--color-border)] flex items-center justify-between gap-4 bg-[var(--color-bg-chrome)]">
          <span className="text-[11px] text-[var(--color-text-muted)]">
            On Create, I open a chat tab and set it up live — this window closes. Reading, unreachable links &amp; conflicts surface there.
          </span>
          <button
            data-testid="new-brain-create"
            onClick={handleCreate}
            disabled={!name.trim()}
            className="bg-[var(--color-primary)] text-[#0b1712] font-bold text-[13px] rounded-lg px-4 py-2 disabled:opacity-40 disabled:cursor-not-allowed hover:brightness-110 transition"
          >
            Create Brain →
          </button>
        </div>
      </div>
  );
}
