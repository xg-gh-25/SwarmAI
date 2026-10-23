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
 * Action = dispatch the item's message into a chat tab via the overlay ctx's
 * `dispatchPrompt` (the EXISTING inject-to-chat mechanism — no /act, no new
 * channel; design principle 3). Clicking an item closes the overlay and hands
 * the work to the agent in chat.
 *
 * No "see more/less" fold — the overlay has room; the full queue is shown.
 *
 * Data: `overlayQuery.needsYou()` (TanStack Query, cached + prefetched on the
 * AlertsPill's hover); first-load states via OverlayState
 * (docs/design/left-nav-overlay-finish.md §1.3).
 */
import { useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { AttentionEntry } from '../../services/attention';
import { overlayQuery } from './overlayQueries';
import { OverlayLoading, OverlayError, OverlayEmpty } from './OverlayState';

interface NeedYouContentProps {
  /** Inject the item's message into a chat tab (overlay ctx dispatchPrompt). */
  onDispatch: (prompt: string) => boolean;
  /** Close the overlay (after dispatching). */
  close: () => void;
}

const OS_LEVEL = 'OS-level';

const SOURCE_LABEL: Record<AttentionEntry['source'], string> = {
  escalation: 'escalation',
  paused_run: 'paused pipeline',
  cultivation: 'proposal',
  governance: 'governance',
  job: 'job',
  community_digest: 'digest',
};

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

export function NeedYouContent({ onDispatch, close }: NeedYouContentProps) {
  const q = useQuery(overlayQuery.needsYou());
  const items: AttentionEntry[] = q.data?.items ?? [];
  const loading = q.isPending;
  const error = q.isError && !q.data;

  const handleClick = useCallback(
    (it: AttentionEntry) => {
      const landed = onDispatch(it.dispatch?.message || it.title);
      if (landed) close();
    },
    [onDispatch, close],
  );

  const blocking = items.filter((i) => i.tier === 'blocking');
  const review = items.filter((i) => i.tier === 'review');

  return (
    <div className="flex-1 overflow-y-auto p-5" data-testid="needs-you-overlay">
      <div className="mb-4">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">Need You</h2>
        <p className="mt-0.5 text-meta text-[var(--color-text-dim)]">
          Everything the OS can't finish without you — decisions, reviews, broken jobs.
          Click an item to hand it to chat.
        </p>
      </div>

      {loading && <OverlayLoading />}

      {error && (
        <OverlayError
          testid="needyou-load-error"
          retryTestid="needyou-load-retry"
          message="Couldn't load the attention queue."
          onRetry={() => { void q.refetch(); }}
        />
      )}

      {!loading && !error && items.length === 0 && (
        <OverlayEmpty icon="check_circle">Nothing needs you right now</OverlayEmpty>
      )}

      {blocking.length > 0 && (
        <TierSection
          label="Blocking"
          hint="Stopped — nothing moves without you"
          pulse
          items={blocking}
          onClick={handleClick}
        />
      )}
      {review.length > 0 && (
        <TierSection
          label="Review"
          hint="Self-advancing — your input confirms or overrides"
          items={review}
          onClick={handleClick}
        />
      )}
    </div>
  );
}

function TierSection({
  label,
  hint,
  items,
  onClick,
  pulse = false,
}: {
  label: string;
  hint: string;
  items: AttentionEntry[];
  onClick: (it: AttentionEntry) => void;
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
          <div className="flex flex-col gap-1">
            {brainItems.map((it) => (
              <button
                key={it.id}
                type="button"
                onClick={() => onClick(it)}
                className="w-full text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 hover:bg-[var(--color-hover)] transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span className="text-meta uppercase text-[var(--color-text-muted)] bg-[var(--color-hover)] rounded px-1.5 py-0.5">
                    {SOURCE_LABEL[it.source]}
                  </span>
                  <span className="flex-1 text-small text-[var(--color-text)] truncate">{it.title}</span>
                  <span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">
                    arrow_forward
                  </span>
                </div>
                {it.detail && (
                  <div className="mt-0.5 text-meta leading-snug text-[var(--color-text-dim)] line-clamp-2">
                    {it.detail}
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
