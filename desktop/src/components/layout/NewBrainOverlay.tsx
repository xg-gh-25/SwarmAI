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
import { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useOverlayDraft } from '../../hooks/useOverlayDraft';
import { MaterialPicker, type MaterialRow } from './StarterMaterial';
import {
  buildBrainManifest,
  previewSections,
  validateBrainName,
  type PreviewSection,
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
const PREVIEW: { key: PreviewSection; labelKey: string; deg: number }[] = [
  { key: 'identity', labelKey: 'ui.NewBrainOverlay.section_identity', deg: -120 },
  { key: 'knowledge', labelKey: 'ui.NewBrainOverlay.section_knowledge', deg: -60 },
  { key: 'gates', labelKey: 'ui.NewBrainOverlay.section_gates', deg: 0 },
  { key: 'capabilities', labelKey: 'ui.NewBrainOverlay.section_capabilities', deg: 60 },
  { key: 'refresher', labelKey: 'ui.NewBrainOverlay.section_refresher', deg: 120 },
  { key: 'delivery', labelKey: 'ui.NewBrainOverlay.section_delivery', deg: 180 },
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
  const { t } = useTranslation();
  const R = 44, D = Math.sqrt(3) * R + 4, cx = 150, cy = 130;
  const trimmed = name.trim();
  const label = trimmed ? (trimmed.length > 9 ? trimmed.slice(0, 8) + '…' : trimmed) : t('ui.NewBrainOverlay.brainFallback');
  return (
    <svg viewBox="0 0 300 260" width="100%" role="img" aria-label={t('ui.NewBrainOverlay.previewLabel')} data-testid="new-brain-preview">
      {PREVIEW.map(({ key, labelKey, deg }) => {
        const a = (deg * Math.PI) / 180, x = cx + D * Math.cos(a), y = cy + D * Math.sin(a), on = filled[key];
        return (
          <g key={key} data-testid={`new-brain-preview-${key}`} data-on={on ? 'yes' : 'no'}>
            <polygon points={hexPoints(x, y, R)} strokeWidth={1.3} strokeDasharray={on ? undefined : '3 3'}
              className={on ? 'fill-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] stroke-[var(--color-primary)]'
                : 'fill-transparent stroke-[var(--color-text-muted)]'} />
            <text x={x} y={y + 4} textAnchor="middle"
              className={`text-meta ${on ? 'font-semibold fill-[var(--color-text)]' : 'fill-[var(--color-text-muted)]'}`}>{t(labelKey)}</text>
          </g>
        );
      })}
      <polygon points={hexPoints(cx, cy, R)} className="fill-[var(--color-primary)] stroke-[var(--color-primary)]" />
      <text x={cx} y={cy + 4} textAnchor="middle" className="text-small font-semibold fill-[var(--color-on-accent)]">{label}</text>
    </svg>
  );
}

/** The three steps after Create, as i18n keys (ui.NewBrainOverlay.step<n>Title / step<n>Body). */
const NEXT_STEPS: { n: number; titleKey: string; bodyKey: string }[] = [
  { n: 1, titleKey: 'ui.NewBrainOverlay.step1Title', bodyKey: 'ui.NewBrainOverlay.step1Body' },
  { n: 2, titleKey: 'ui.NewBrainOverlay.step2Title', bodyKey: 'ui.NewBrainOverlay.step2Body' },
  { n: 3, titleKey: 'ui.NewBrainOverlay.step3Title', bodyKey: 'ui.NewBrainOverlay.step3Body' },
];

type RowItem = MaterialRow;

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
  const { t } = useTranslation();
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

  const canCreate = !!name.trim() && !nameError;

  return (
    <div className="flex-1 min-h-0 flex flex-col" data-testid="new-brain-overlay">
      <div className="flex-1 min-h-0 grid grid-cols-[340px_1fr]">
        {/* ── Left: who it is ─────────────────────────────────────────────── */}
        <div className="p-6 border-r border-[var(--color-border)] flex flex-col gap-5 overflow-y-auto">
          <div>
            <label htmlFor="new-brain-name-input" className="block text-small font-semibold text-[var(--color-text)] mb-1.5">{t('common.label.name')}</label>
            <input
              id="new-brain-name-input"
              data-testid="new-brain-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('ui.NewBrainOverlay.namePlaceholder')}
              aria-invalid={!!nameError}
              aria-describedby="new-brain-name-help"
              className={`w-full h-10 bg-[var(--color-input-bg)] border rounded-lg px-3 text-body text-[var(--color-text)] outline-none focus:border-[var(--color-primary)] ${
                nameError ? 'border-[var(--color-status-error)]' : 'border-[var(--color-border)]'}`}
            />
            <div id="new-brain-name-help" data-testid="new-brain-name-help"
              className={`mt-1.5 text-meta ${nameError ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}`}>
              {nameError ?? t('ui.NewBrainOverlay.nameHelp')}
            </div>
          </div>

          <div>
            <div className="text-small font-semibold text-[var(--color-text)]">{t('ui.NewBrainOverlay.preview')}</div>
            <BrainPreview name={name} filled={filled} />
            <div className="text-small" data-testid="new-brain-assets">
              {assetCount > 0
                ? <>{t('ui.NewBrainOverlay.governsBefore')}<span className="font-semibold">{assetCount}</span>{t('ui.NewBrainOverlay.governsAfter', { count: assetCount })}</>
                : <><span className="font-semibold">{t('ui.NewBrainOverlay.pureKnowledge')}</span> {t('ui.NewBrainOverlay.nothingToBind')}</>}
            </div>
            <div className="text-meta text-[var(--color-text-muted)] mt-1">{t('ui.NewBrainOverlay.solidDashed')}</div>
          </div>
        </div>

        {/* ── Right: what it starts from ──────────────────────────────────── */}
        <div className="p-6 flex flex-col min-w-0 min-h-0">
          <div className="flex items-baseline justify-between mb-2">
            <span className="text-small font-semibold text-[var(--color-text)]">{t('ui.NewBrainOverlay.starterMaterial')}</span>
            <span className="text-meta text-[var(--color-text-muted)]">{t('common.label.optional')}</span>
          </div>
          <MaterialPicker items={items} setItems={setItems} draft={draft} setDraft={setDraft}
            emptyText={t('ui.NewBrainOverlay.materialEmpty')} />
        </div>
      </div>

      {/* ── Footer: what happens on Create + the one action ─────────────────── */}
      <div className="px-6 py-4 border-t border-[var(--color-border)] bg-[var(--color-bg-chrome)] flex items-center gap-6">
        <ol className="flex-1 grid grid-cols-3 gap-4" data-testid="new-brain-next-steps">
          {NEXT_STEPS.map((s) => (
            <li key={s.n} className="flex gap-2.5 min-w-0">
              <span className="shrink-0 inline-flex w-5 h-5 items-center justify-center rounded-full text-meta font-semibold bg-[color-mix(in_srgb,var(--color-primary)_16%,transparent)] text-[var(--color-primary)]">{s.n}</span>
              <span className="min-w-0">
                <span className="block text-small font-semibold">{t(s.titleKey)}</span>
                <span className="block text-meta text-[var(--color-text-muted)]">{t(s.bodyKey)}</span>
              </span>
            </li>
          ))}
        </ol>
        <button
          data-testid="new-brain-create"
          onClick={handleCreate}
          disabled={!canCreate}
          title={canCreate ? t('ui.NewBrainOverlay.createTitle') : t('ui.NewBrainOverlay.createNeedsName')}
          className="btn-lg btn-primary shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {t('ui.NewBrainOverlay.createBrain')}
        </button>
      </div>
    </div>
  );
}
