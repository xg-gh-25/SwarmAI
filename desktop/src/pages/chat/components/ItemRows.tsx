/**
 * ItemRows — the ONE row and box every turn card lists things with (design
 * docs/design/2026-10-26-turn-cards-unified.md §2): products, changed files and
 * commits look the same.
 *
 * - `ItemBox`: a bordered box that hugs its widest row (`w-fit max-w-full`), an
 *   optional title line inside it, rows separated by a hairline. When one name alone
 *   is wider than the column the box scrolls sideways; the name is never cut.
 * - `ItemRow`: [lead] · badge · name · grey folder · [tail] · [actions], then an
 *   optional panel below (a diff). The name never wraps and never shrinks
 *   (`whitespace-nowrap shrink-0`) unless `nameCanCut` (a commit subject, which is not
 *   a file name); only the grey folder truncates, with the full path on hover.
 * - `ItemSection`: a title line between groups of rows in one box (COMMITS).
 *
 * - `shortFolder`: a folder cut in the middle ("~/.swarm-ai/…/2-understanding").
 *
 * @exports ItemBox, ItemRow, ItemSection, ITEM_ACTION, shortFolder
 */
import type { MouseEvent, ReactNode } from 'react';

const BOX = 'w-fit max-w-full overflow-x-auto rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)]';
const LIST = 'flex flex-col divide-y divide-[var(--color-border)]';
const TITLE = 'flex items-baseline gap-2 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--color-text-dim)]';
/** An icon button that sits right after the folder, dim until the row is hovered. */
export const ITEM_ACTION = 'icon-btn shrink-0 -ml-1 text-[var(--color-text-dim)] group-hover:text-[var(--color-text-secondary)]';

export function ItemBox({ title, tail, children, footer, testId }: {
  title?: ReactNode; tail?: ReactNode; children: ReactNode; footer?: ReactNode; testId?: string;
}) {
  return (
    <div data-testid={testId} data-item-box="" className={BOX}>
      {title != null && (
        <div data-testid="item-box-title" className={`${TITLE} border-b border-[var(--color-border)]`}>
          <span className="whitespace-nowrap">{title}</span>
          {tail && <span className="ml-auto pl-3 whitespace-nowrap normal-case tracking-normal font-normal">{tail}</span>}
        </div>
      )}
      <ul className={LIST}>{children}</ul>
      {footer}
    </div>
  );
}

export function ItemSection({ title, testId }: { title: ReactNode; testId?: string }) {
  return <li data-testid={testId} className={`${TITLE} bg-[var(--color-hover)]/40`}>{title}</li>;
}

export interface ItemRowProps {
  testId: string;
  data?: Record<string, string>;
  /** Before the badge (the ▸ that opens a diff), or a spacer of its width. */
  lead?: ReactNode;
  badge: ReactNode;
  name: string;
  nameTestId?: string;
  /** The name is a link (primary colour). */
  link?: boolean;
  nameCanCut?: boolean;
  folder?: string;
  folderTitle?: string;
  folderTestId?: string;
  tail?: ReactNode;
  actions?: ReactNode;
  /** Opens the item: the name is a button; with `rowOpens` the whole row is too. */
  onOpen?: () => void;
  rowOpens?: boolean;
  openTitle?: string;
  below?: ReactNode;
}

export function ItemRow(p: ItemRowProps) {
  const open = p.onOpen;
  const rowClick = open && p.rowOpens ? open : undefined;
  const nameCls = `${p.nameCanCut ? 'min-w-0 truncate' : 'whitespace-nowrap shrink-0'} text-[13px] ${
    p.link ? `text-primary ${open ? 'group-hover:underline' : ''}` : 'text-[var(--color-text)]'}`;
  const inner = (
    <>
      {p.badge}
      <span className="min-w-0 flex items-baseline gap-2">
        <span data-testid={p.nameTestId} data-item-name="" className={nameCls}>{p.name}</span>
        {p.folder && (
          <span data-testid={p.folderTestId} title={p.folderTitle}
            className="min-w-[3ch] shrink-[100] truncate font-mono text-[11.5px] text-[var(--color-text-dim)]">{p.folder}</span>
        )}
      </span>
    </>
  );
  const headCls = 'min-w-0 flex items-center gap-2.5 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50';
  return (
    <li data-testid={p.testId} data-item-row="" onClick={rowClick}
      {...Object.fromEntries(Object.entries(p.data ?? {}).map(([k, v]) => [`data-${k}`, v]))}>
      <div
        className={`group flex items-center gap-2.5 min-w-0 px-3 py-1.5 transition-colors ${rowClick ? 'cursor-pointer hover:bg-[var(--color-hover)]' : ''}`}>
        {p.lead}
        {open ? (
          <button type="button" title={p.openTitle} className={headCls}
            onClick={(e: MouseEvent) => { e.stopPropagation(); open(); }}>{inner}</button>
        ) : <span title={p.openTitle} className={headCls}>{inner}</span>}
        {p.tail}
        {p.actions}
      </div>
      {p.below && <div onClick={(e) => e.stopPropagation()}>{p.below}</div>}
    </li>
  );
}

/** A path's folder, home shown as ~. */
function folderOf(abs: string): string {
  const dir = abs.replace(/\/[^/]*$/, '') || '/';
  return dir.replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, '~');
}

/** A folder cut in the MIDDLE, keeping its root and where it ends
 *  ("~/.swarm-ai/…/2-understanding"); the end says which folder it is. */
export function shortFolder(dirPath: string): string {
  const dir = folderOf(`${dirPath.replace(/\/+$/, '')}/x`);
  const segs = dir.split('/').filter(Boolean);
  const keep = segs[0] === '~' ? 2 : 1;          // "~/<first>" or "/<first>"
  if (segs.length - keep - 1 < 2) return dir;    // nothing worth cutting
  const head = `${dir.startsWith('/') ? '/' : ''}${segs.slice(0, keep).join('/')}`;
  return `${head}/…/${segs[segs.length - 1]}`;
}
