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
import { useCallback, useLayoutEffect, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import {
  classifyStarterItem,
  buildBrainManifest,
  detectKind,
  type GovernsKind,
  type StarterItem,
  type StarterKind,
  type StarterRole,
} from '../../utils/newBrainDispatch';

export interface NewBrainOverlayProps {
  /** Lands a prompt into a chat tab (new or reused). Returns true on success;
   *  false (all tabs busy / unsent draft) must keep the launcher open. Wired to
   *  ChatPage's handleDispatchJobPrompt — the same dispatcher Jobs/Pipeline use. */
  onDispatch: (prompt: string) => boolean;
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

export function NewBrainOverlay({ onDispatch }: NewBrainOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-new-brain');
  const [name, setName] = useState('');
  const [governs, setGoverns] = useState<GovernsKind>('codebase');
  const [items, setItems] = useState<RowItem[]>([]);
  const [draft, setDraft] = useState('');

  // Fresh, empty birth every time the launcher OPENS (one-shot gate — no state
  // carried between brains). Reset is driven by the RAW show-event, NOT by the
  // `open` boolean's transition, because a rapid close→reopen batches
  // setOpen(false)+setOpen(true) into one React commit — `open` never observably
  // leaves `true`, so an effect keyed on `[open]` would NOT re-run and the prior
  // brain's state would leak (the bug the deferred-close-reset also had, #4b-regression).
  // The DOM event fires synchronously on EVERY dispatch, before React re-renders,
  // so the reset is queued in the same commit as the open → no stale frame, and it
  // fires even when `open` doesn't transition. Resetting on OPEN (not on close)
  // also means closing never blanks the fields mid-fade (the original #4b flash).
  const resetFields = useCallback(() => {
    setName('');
    setGoverns('codebase');
    setItems([]);
    setDraft('');
  }, []);
  useLayoutEffect(() => {
    window.addEventListener('swarm:show-new-brain', resetFields);
    return () => window.removeEventListener('swarm:show-new-brain', resetFields);
  }, [resetFields]);

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
    <Modal isOpen={open} onClose={close} title="Grow a new brain" size="fullscreen" mode="BRAIN" fullscreenWidth="l">
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
              <span className="text-[10px] text-[var(--color-text-faint)]">files · links · local paths · repos — all mixed, optional</span>
            </div>
            <div
              data-testid="new-brain-dropzone"
              onDragOver={(e) => e.preventDefault()}
              onDrop={onDropZoneDrop}
              className="flex-1 border border-dashed border-[var(--color-border-strong)] rounded-xl bg-[var(--color-bg)] p-3 flex flex-col min-h-[240px]"
            >
              <div className="flex-1 flex flex-col gap-1.5 overflow-y-auto">
                {items.length === 0 && (
                  <div className="flex-1 grid place-items-center text-center text-[var(--color-text-faint)]">
                    <div>
                      <div className="text-2xl opacity-60 mb-1.5">⤵</div>
                      <div className="text-[12px] text-[var(--color-text-muted)] font-medium">Drop or paste anything here</div>
                      <div className="text-[10.5px] mt-1">sorted by type — click a pill to change the role</div>
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
              <input
                data-testid="new-brain-material-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitDraft(); } }}
                onBlur={commitDraft}
                placeholder="paste a link / path / repo, or type — Enter to add"
                className="mt-2 w-full bg-transparent border-t border-[var(--color-border)] pt-2 text-[12px] text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-faint)]"
              />
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
    </Modal>
  );
}

export default NewBrainOverlay;
