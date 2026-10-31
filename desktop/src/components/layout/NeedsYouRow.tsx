/**
 * NeedsYouRow — one Need You item as a two-action row, shared by the Welcome card
 * and the Need You overlay (design welcome-screen-v2.md, "Needs you rows").
 *
 * Same two actions as a reply's Next steps chips (NextSteps.tsx):
 *   - click the text → `onFill(prompt)`: the prompt goes into the chat input,
 *     the user can edit it, nothing is sent;
 *   - click ↑        → `onSend(prompt)`: the prompt is sent now.
 * `prompt` is the backend's `dispatch.message`, written in the user's voice with
 * the item's context and ids; the row shows the backend's plain `title` (what is
 * needed) and `summary` (which thing it is) as-is, never rewritten here.
 *
 * A Swarm Out item (`dispatch.context.kind` = "swarm_out": a summary of the runs and
 * questions waiting) is not a prompt: either action opens the Swarm Out tab, where each
 * run is handled (design 2026-10-31-swarm-out-run-review §3.7), then `onOpened`.
 *
 * @exports NeedsYouRow, needsYouPrompt
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { AttentionEntry } from '../../services/attention';
import { getOverlayCtxBridge } from './overlayRegistry';

/** True for an item that opens the Swarm Out tab instead of filling the chat. */
export function opensSwarmOut(it: AttentionEntry): boolean {
  return it.dispatch?.context?.kind === 'swarm_out';
}

/** The text a row fills or sends: the backend prompt, else its title. */
export function needsYouPrompt(it: AttentionEntry): string {
  return it.dispatch?.message || it.title;
}

export interface NeedsYouRowProps {
  item: AttentionEntry;
  onFill: (prompt: string) => void;
  onSend: (prompt: string) => void;
  /** A leading mark (the Welcome card's hexagon); none in the overlay. */
  lead?: ReactNode;
  /** Draw the divider above this row (every row but the first in a card). */
  divider?: boolean;
  /** After a Swarm Out item opened the Swarm Out tab (the overlay closes itself). */
  onOpened?: () => void;
  testid?: string;
}

export function NeedsYouRow({ item, onFill, onSend, lead, divider = false, testid = 'needs-you-row', onOpened }: NeedsYouRowProps) {
  const { t } = useTranslation();
  const prompt = needsYouPrompt(item);
  // a Swarm Out item opens the Swarm Out tab; where that tab cannot be reached (no
  // chat page published it) it is an ordinary prompt
  const show = opensSwarmOut(item) ? getOverlayCtxBridge().showSwarmOut : undefined;
  const swarmOut = !!show;
  const openSwarmOut = () => { show?.(); onOpened?.(); };
  return (
    <div
      data-testid={testid}
      className={`group flex items-center gap-1 pr-2 ${divider ? 'border-t border-[var(--color-border)]' : ''}`}
    >
      <button
        type="button"
        data-testid={`${testid}-fill`}
        onClick={() => (swarmOut ? openSwarmOut() : onFill(prompt))}
        title={swarmOut ? t('needYou.openSwarmOut') : t('needYou.fillHint')}
        className="flex-1 min-w-0 w-full text-left flex items-start gap-2.5 pl-4 pr-2 py-2 hover:bg-[var(--color-hover)] transition-colors cursor-pointer"
      >
        {lead && <span className="mt-[4px] shrink-0">{lead}</span>}
        <span className="flex-1 min-w-0">
          <span className="block truncate text-body font-semibold text-[var(--color-text)]">{item.title}</span>
          {item.summary && (
            <span className="block truncate text-small text-[var(--color-text-secondary)]">{item.summary}</span>
          )}
        </span>
        <span className="shrink-0 mt-[2px] text-meta text-[var(--color-text-muted)] px-[7px] py-px rounded-md bg-[var(--color-hover)]">
          {t(`needYou.source.${item.source}`, { defaultValue: item.source })}
        </span>
      </button>
      {!swarmOut && <button
        type="button"
        data-testid={`${testid}-send`}
        onClick={() => onSend(prompt)}
        title={t('needYou.sendNow')}
        aria-label={t('needYou.sendNowLabel', { title: item.title })}
        className="icon-btn-lg shrink-0 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-brand-strong)] hover:bg-[color-mix(in_srgb,var(--color-brand)_16%,transparent)] transition-colors"
      >
        <span className="material-symbols-outlined icon-md">arrow_upward</span>
      </button>}
    </div>
  );
}
