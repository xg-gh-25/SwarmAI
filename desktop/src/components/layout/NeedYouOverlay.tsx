/**
 * NeedYouOverlay — the unified "Need You" channel, fullscreen.
 *
 * Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md
 *
 * The AlertsPill's fullscreen view. Consumes the SINGLE backend
 * AttentionAuthority (GET /api/attention) — no local aggregation, no
 * chat/streaming coupling. Double-axis classification (design §5):
 *   - MAIN axis = tier: BLOCKING first (pulse) → REVIEW below
 *   - SUB axis  = brain: within each tier, grouped by brain; brain=null → OS-level
 *
 * Action = each row (NeedsYouRow, shared with the Welcome card) fills the chat
 * input with the item's prompt, or ↑ sends it now — both through the overlay
 * ctx's `dispatchPrompt` (the EXISTING inject-to-chat mechanism — no /act, no new
 * channel; design principle 3). The overlay closes once the prompt landed.
 *
 * No "see more/less" fold — the overlay has room; the full queue is shown.
 *
 * Data: `overlayQuery.needsYou()` (TanStack Query, cached + prefetched on the
 * AlertsPill's hover); first-load states via OverlayState
 * (docs/design/left-nav-overlay-finish.md §1.3).
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import type { AttentionEntry } from '../../services/attention';
import { Tabs } from '../overlay';
import { CognitiveDistillationFlow } from './CognitiveDistillationFlow';
import { overlayQuery } from './overlayQueries';
import { OverlayLoading, OverlayError, OverlayEmpty } from './OverlayState';
import { NeedsYouRow } from './NeedsYouRow';
import type { DispatchPrompt } from './overlayRegistry';

interface NeedYouContentProps {
  /** Land the item's prompt in a chat tab (overlay ctx dispatchPrompt); `autoSend`
   *  for the ↑ button. Returns whether it landed. */
  onDispatch: DispatchPrompt;
  /** Close the overlay (after dispatching). */
  close: () => void;
}

const OS_LEVEL = 'OS-level';

/** Group items by brain (null → OS-level), preserving insertion order. */
function groupByBrain(items: AttentionEntry[]): Array<[string, AttentionEntry[]]> {
  const groups = new Map<string, AttentionEntry[]>();
  for (const it of items) {
    const key = it.brain ?? OS_LEVEL;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(it);
  }
  return [...groups.entries()];
}

type NeedYouTab = 'attention' | 'flow';

export function NeedYouContent(props: NeedYouContentProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<NeedYouTab>('attention');
  return (
    <div className="flex min-h-0 flex-1 flex-col p-5" data-testid="needs-you-overlay">
      <Tabs
        items={[
          { key: 'attention', label: t('needYou.tabAttention'), testid: 'need-you-tab' },
          { key: 'flow', label: t('needYou.tabFlow'), testid: 'cdf-tab' },
        ]}
        value={tab}
        onChange={setTab}
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'attention'
          ? <AttentionQueueContent {...props} />
          : <CognitiveDistillationFlow onDispatch={props.onDispatch} close={props.close} />}
      </div>
    </div>
  );
}

function AttentionQueueContent({ onDispatch, close }: NeedYouContentProps) {
  const { t } = useTranslation();
  const q = useQuery(overlayQuery.needsYou());
  const items: AttentionEntry[] = q.data?.items ?? [];
  const loading = q.isPending;
  const error = q.isError && !q.data;

  // Same two actions as the Welcome card and a reply's Next steps: fill the input
  // (the user edits, then sends) or send now. The overlay closes once it landed.
  const onFill = useCallback((prompt: string) => { if (onDispatch(prompt, { autoSend: false })) close(); }, [onDispatch, close]);
  const onSend = useCallback((prompt: string) => { if (onDispatch(prompt, { autoSend: true })) close(); }, [onDispatch, close]);

  const blocking = items.filter((i) => i.tier === 'blocking');
  const review = items.filter((i) => i.tier === 'review');

  return (
    <div className="py-4" data-testid="needs-you-attention">
      <div className="mb-4">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('needYou.title')}</h2>
        <p className="mt-0.5 text-meta text-[var(--color-text-dim)]">
          {t('needYou.description')}
        </p>
      </div>

      {loading && <OverlayLoading />}

      {error && (
        <OverlayError
          testid="needyou-load-error"
          retryTestid="needyou-load-retry"
          message={t('needYou.loadFailed')}
          onRetry={() => { void q.refetch(); }}
        />
      )}

      {!loading && !error && items.length === 0 && (
        <OverlayEmpty icon="check_circle">{t('needYou.empty')}</OverlayEmpty>
      )}

      {blocking.length > 0 && (
        <TierSection
          label={t('needYou.blocking')}
          hint={t('needYou.blockingHint')}
          pulse
          items={blocking}
          onFill={onFill}
          onSend={onSend}
        />
      )}
      {review.length > 0 && (
        <TierSection
          label={t('needYou.review')}
          hint={t('needYou.reviewHint')}
          items={review}
          onFill={onFill}
          onSend={onSend}
        />
      )}
    </div>
  );
}

function TierSection({
  label,
  hint,
  items,
  onFill,
  onSend,
  pulse = false,
}: {
  label: string;
  hint: string;
  items: AttentionEntry[];
  onFill: (prompt: string) => void;
  onSend: (prompt: string) => void;
  pulse?: boolean;
}) {
  const groups = groupByBrain(items);
  return (
    <section className="mb-5">
      <div className="flex items-baseline gap-2 mb-2">
        <span
          className={[
            'text-small font-semibold text-[var(--color-text)]',
            pulse ? 'animate-pulse' : '',
          ].join(' ')}
        >
          {label}
        </span>
        <span className="text-meta text-[var(--color-text-dim)]">· {items.length} · {hint}</span>
      </div>
      {groups.map(([brain, brainItems]) => (
        <div key={brain} className="mb-2">
          <div className="px-1 py-1 text-meta uppercase tracking-wide text-[var(--color-text-muted)]">
            {brain}
          </div>
          <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]">
            {brainItems.map((it, i) => (
              <NeedsYouRow key={it.id} item={it} divider={i > 0} onFill={onFill} onSend={onSend} />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
