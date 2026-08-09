/**
 * NeedYouOverlay — the unified "Need You" channel, fullscreen.
 *
 * Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md
 *
 * The AlertsPill's fullscreen view. Consumes the SINGLE backend
 * AttentionAuthority (GET /api/attention) — no local aggregation, no
 * chat/streaming coupling. Double-axis classification (design §5):
 *   - MAIN axis = tier: 🔴 BLOCKING first (pulse) → 🟡 REVIEW below
 *   - SUB axis  = brain: within each tier, grouped by brain; brain=null → OS-level
 *
 * Action = dispatch the item's message into a chat tab via the overlay ctx's
 * `dispatchPrompt` (the EXISTING inject-to-chat mechanism — no /act, no new
 * channel; design principle 3). Clicking an item closes the overlay and hands
 * the work to the agent in chat.
 *
 * No "see more/less" fold — the overlay has room; the full queue is shown.
 */
import { useEffect, useState, useCallback } from 'react';
import { attentionService, type AttentionEntry } from '../../services/attention';

interface NeedYouContentProps {
  /** Inject the item's message into a chat tab (overlay ctx dispatchPrompt). */
  onDispatch: (prompt: string) => boolean;
  /** Close the overlay (after dispatching). */
  close: () => void;
}

const OS_LEVEL = '⚙ OS-level';

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
  const [items, setItems] = useState<AttentionEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    const res = await attentionService.fetchAttention().catch(() => null);
    if (res) {
      setItems(res.items);
      setError(false);
    } else {
      setError(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
        <h2 className="text-[15px] font-semibold text-[var(--color-text)]">Need You</h2>
        <p className="mt-0.5 text-[11.5px] text-[var(--color-text-dim)]">
          Everything the OS can't finish without you — decisions, reviews, broken jobs.
          Click an item to hand it to chat.
        </p>
      </div>

      {loading && (
        <div className="py-8 text-center text-[12px] text-[var(--color-text-muted)]">Loading…</div>
      )}

      {error && !loading && (
        <div className="py-8 text-center text-[12px] text-red-400">
          Couldn't load the attention queue. Retry shortly.
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="py-10 text-center text-[13px] text-[var(--color-text-muted)]">
          <div className="material-symbols-outlined text-[28px] opacity-40">check_circle</div>
          <div className="mt-1">Nothing needs you right now</div>
        </div>
      )}

      {blocking.length > 0 && (
        <TierSection
          label="🔴 Blocking"
          hint="Stopped — nothing moves without you"
          pulse
          items={blocking}
          onClick={handleClick}
        />
      )}
      {review.length > 0 && (
        <TierSection
          label="🟡 Review"
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
            'text-[12.5px] font-semibold text-[var(--color-text)]',
            pulse ? 'animate-pulse' : '',
          ].join(' ')}
        >
          {label}
        </span>
        <span className="text-[10.5px] text-[var(--color-text-dim)]">· {items.length} · {hint}</span>
      </div>
      {groups.map(([brain, brainItems]) => (
        <div key={brain} className="mb-2">
          <div className="px-1 py-1 text-[10.5px] font-mono uppercase tracking-wide text-[var(--color-text-muted)]">
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
                  <span className="text-[9.5px] font-mono uppercase text-[var(--color-text-muted)] bg-[var(--color-hover)] rounded px-1.5 py-0.5">
                    {SOURCE_LABEL[it.source]}
                  </span>
                  <span className="flex-1 text-[12.5px] text-[var(--color-text)] truncate">{it.title}</span>
                  <span className="material-symbols-outlined text-[16px] text-[var(--color-text-muted)]">
                    arrow_forward
                  </span>
                </div>
                {it.detail && (
                  <div className="mt-0.5 text-[10.5px] leading-snug text-[var(--color-text-dim)] line-clamp-2">
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
