/**
 * Inbox shared parts — the small pieces every Inbox lane draws the same way (design
 * docs/design/2026-11-20-inbox.md §7, mockup docs/design/2026-11-20-inbox-mockup.html).
 *
 * - `Avatar`       initials (or a bot / channel icon) on a tint of one theme colour
 * - `ChannelPill`  icon + channel name in the channel's colour (`--color-channel-*`)
 * - `ThreadMore`   the muted "+N" of a row standing for a thread with N more open items
 * - `MenuButton`   a small text button that opens a menu of choices (稍后 ▾, 不用回 ▾):
 *                  aria-haspopup / aria-expanded, a `role="menu"` of `role="menuitem"`
 *                  buttons; Enter / Space / arrow keys open it, arrow keys, Home and End
 *                  move, a choice / Escape (the layer stack, hooks/useLayer) / a click
 *                  outside / Tab close it; a letter or Cmd/Ctrl+Enter typed in it is
 *                  marked handled, so the page's shortcuts never act behind it
 * - `StatusLine`   the overlay's own short confirmation line ("已发送给 …")
 * - `relTime` / `clockTime` / `dayKey` / `dayLabel` / `intervalLabel`  time text
 * - `daysUntil`    whole local days from today to a YYYY-MM-DD due date
 * - `webUrl`       a source's link, only when it is http(s)
 *
 * Colours come only from theme variables; a tint is `color-mix(<var> N%, …)`.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { channelKey, type ChannelKey } from '../../services/inbox';
import { useEscape } from '../../hooks/useLayer';

/** The avatar colours: theme roles, picked by a stable hash of the name. */
const HUES = ['--color-primary', '--color-zone-cognitive', '--color-zone-work', '--color-status-warning', '--color-zone-system'];

export function hueOf(name: string): string {
  let n = 0;
  for (const ch of name) n += ch.codePointAt(0) ?? 0;
  return HUES[n % HUES.length];
}

const CJK = /^[㐀-鿿]/;

/** Two letters fit the 36 px avatar, one the 28 px one. A CJK name shows its last
 *  characters (the given name); a Latin one the first letter of each word. */
export function initials(name: string, n = 2): string {
  const s = name.trim();
  if (!s) return '?';
  if (CJK.test(s)) return [...s].slice(-n).join('');
  const words = s.split(/[\s/#._-]+/).filter(Boolean);
  return words.slice(0, n).map((w) => [...w][0].toUpperCase()).join('') || '?';
}

const CHANNEL_ICON: Record<ChannelKey, string> = {
  slack: 'tag',
  mail: 'mail',
  github: 'commit',
  feed: 'rss_feed',
  other: 'forum',
};

export function channelVar(kind: string): string {
  const k = channelKey(kind);
  return k === 'other' ? '--color-text-muted' : `--color-channel-${k}`;
}

export function channelIcon(kind: string): string {
  return CHANNEL_ICON[channelKey(kind)];
}

const tint = (v: string, pct: number, base = 'transparent') => `color-mix(in srgb, var(${v}) ${pct}%, ${base})`;

export function Avatar({ name, size = 'md', icon, colorVar, testid }: {
  name: string; size?: 'md' | 'sm'; icon?: string; colorVar?: string; testid?: string;
}) {
  const v = colorVar ?? (icon ? '--color-zone-system' : hueOf(name));
  return (
    <span aria-hidden data-testid={testid}
      className={`relative flex shrink-0 items-center justify-center rounded-full font-semibold ${size === 'sm' ? 'h-7 w-7 text-meta' : 'h-9 w-9 text-small'}`}
      style={{ color: `var(${v})`, background: tint(v, 15, 'var(--color-card)') }}>
      {icon
        ? <span className="material-symbols-outlined icon-sm">{icon}</span>
        : initials(name, size === 'sm' ? 1 : 2)}
    </span>
  );
}

export function ChannelPill({ kind, label, large = false }: { kind: string; label: string; large?: boolean }) {
  const v = channelVar(kind);
  return (
    <span data-testid="inbox-channel-pill" data-channel={channelKey(kind)}
      className={`inline-flex shrink-0 items-center gap-1 rounded-md font-semibold ${large ? 'h-6 pl-[7px] pr-[9px] text-small' : 'h-5 pl-1.5 pr-[7px] text-meta'}`}
      style={{ color: `var(${v})`, background: tint(v, 13) }}>
      <span className="material-symbols-outlined icon-xs" aria-hidden>{channelIcon(kind)}</span>
      {label}
    </span>
  );
}

/** "+N" after a row's time when the row stands for a thread with more open items
 *  (`thread_count` counts the row itself); nothing for a single item. */
export function ThreadMore({ count }: { count: number }) {
  const { t } = useTranslation();
  const more = Math.max(0, (count || 1) - 1);
  if (!more) return null;
  const title = t('ui.InboxOverlay.threadMore', { count: more });
  return (
    <span className="shrink-0 text-meta text-[var(--color-text-dim)]" title={title} aria-label={title} data-testid="inbox-thread-more">
      +{more}
    </span>
  );
}

export interface MenuEntry {
  key: string;
  label: ReactNode;
  onSelect: () => void;
  testid?: string;
}

/** A small ghost button (icon + text + ▾) that opens a menu of choices below it, aligned
 *  to its right edge. Opening focuses the first choice (ArrowUp: the last). */
export function MenuButton({ label, icon, items, disabled, testid, title }: {
  label: string; icon?: string; items: MenuEntry[]; disabled?: boolean; testid?: string; title?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const startAt = useRef<'first' | 'last'>('first');
  const menuId = useId();

  const entries = () => Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) btnRef.current?.focus();
  };
  useEscape(open, () => close(true), wrapRef);

  useEffect(() => {
    if (!open) return;
    const all = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
    if (all?.length) all[startAt.current === 'last' ? all.length - 1 : 0].focus();
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && e.target instanceof Node && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const onButtonKey = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    startAt.current = e.key === 'ArrowUp' ? 'last' : 'first';
    setOpen(true);
  };

  const onMenuKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Tab') { setOpen(false); return; }
    // a letter (the lane's J / K / E / H) or Cmd/Ctrl+Enter (send) typed in the menu is
    // the menu's: marked handled so no shortcut of the page behind it acts. Space and
    // Enter alone still choose the focused item.
    const printable = e.key.length === 1 && e.key !== ' ' && !e.metaKey && !e.ctrlKey && !e.altKey;
    if (printable || ((e.metaKey || e.ctrlKey) && e.key === 'Enter')) { e.preventDefault(); return; }
    const all = entries();
    const i = all.indexOf(document.activeElement as HTMLButtonElement);
    const n = all.length;
    const next = e.key === 'ArrowDown' ? (i + 1) % n : e.key === 'ArrowUp' ? (i - 1 + n) % n
      : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
    if (next < 0 || !n) return;
    e.preventDefault();
    all[next]?.focus();
  };

  return (
    <span ref={wrapRef} className="relative inline-flex">
      <button ref={btnRef} type="button" className="btn-sm btn-ghost disabled:opacity-40" disabled={disabled}
        aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} title={title}
        onClick={() => { startAt.current = 'first'; setOpen((o) => !o); }} onKeyDown={onButtonKey} data-testid={testid}>
        {icon && <span className="material-symbols-outlined icon-xs" aria-hidden>{icon}</span>}
        {label}
        <span className="material-symbols-outlined icon-xs -ml-0.5" aria-hidden>expand_more</span>
      </button>
      {open && (
        <div ref={listRef} id={menuId} role="menu" aria-label={label} onKeyDown={onMenuKey}
          data-testid={testid ? `${testid}-menu` : undefined}
          className="absolute right-0 top-full z-20 mt-1 flex min-w-[180px] flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-1 shadow-[var(--shadow-overlay)]">
          {items.map((it) => (
            <button key={it.key} type="button" role="menuitem" tabIndex={-1} data-testid={it.testid}
              onClick={() => { close(true); it.onSelect(); }}
              className="flex w-full items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-left text-small text-[var(--color-text)] outline-none hover:bg-[var(--color-hover)] focus:bg-[var(--color-hover)]">
              {it.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

/** The overlay's own confirmation line at the bottom of the panel; hides itself. */
export function StatusLine({ message, onDone }: { message: ReactNode | null; onDone: () => void }) {
  useEffect(() => {
    if (message == null) return;
    const id = setTimeout(onDone, 3500);
    return () => clearTimeout(id);
  }, [message, onDone]);
  if (message == null) return null;
  return (
    <div role="status" data-testid="inbox-status"
      className="pointer-events-none absolute bottom-5 left-1/2 z-10 -translate-x-1/2 rounded-[10px] px-3.5 py-2 text-small shadow-[var(--shadow-overlay)]"
      style={{ background: 'var(--color-text)', color: 'var(--color-card)' }}>
      {message}
    </div>
  );
}

/** A link from a source is untrusted: only an http(s) URL is opened (a `javascript:`,
 *  `file:` or app-scheme link is not offered at all). Returns the URL or null. */
export function webUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch {
    return null;
  }
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** "刚刚" / "25 分钟前" / "3 小时前" / "昨天" / "2 天前". */
export function relTime(iso: string | null | undefined, t: TFunction, now = Date.now()): string {
  if (!iso) return '';
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return '';
  const d = Math.max(0, now - at);
  if (d < MIN) return t('ui.InboxOverlay.time.justNow');
  if (d < HOUR) return t('ui.InboxOverlay.time.minutes', { count: Math.floor(d / MIN) });
  if (d < DAY) return t('ui.InboxOverlay.time.hours', { count: Math.floor(d / HOUR) });
  if (d < 2 * DAY) return t('ui.InboxOverlay.time.yesterday');
  return t('ui.InboxOverlay.time.days', { count: Math.floor(d / DAY) });
}

/** "09:12" in the person's own time zone. */
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** The local calendar day of a time, for grouping ("2026-11-20"). */
export function dayKey(iso: string | null | undefined): string {
  const d = new Date(iso ?? '');
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const YMD = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "今天" / "昨天" / the date in the UI language ("11月21日", "Nov 21"), for a day group
 *  heading. `key` is a `dayKey`; `lang` the i18n language. */
export function dayLabel(key: string, t: TFunction, lang = 'en', now = new Date()): string {
  const today = dayKey(now.toISOString());
  const y = new Date(now.getTime() - DAY);
  if (key === today) return t('ui.InboxOverlay.time.today');
  if (key === dayKey(y.toISOString())) return t('ui.InboxOverlay.time.yesterday');
  const m = YMD.exec(key);
  if (!m) return key;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) };
  return new Intl.DateTimeFormat(lang.startsWith('zh') ? 'zh-CN' : 'en-US', opts).format(d);
}

/** Whole local days from today to a YYYY-MM-DD date (0 today, negative when past), and
 *  its short "12/24" form; null when it is not a date. */
export function daysUntil(ymd: string | null | undefined, now = new Date()): { days: number; short: string } | null {
  const m = YMD.exec(ymd ?? '');
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return { days: Math.round((d.getTime() - today.getTime()) / DAY), short: `${Number(m[2])}/${Number(m[3])}` };
}

/** "每 15 分钟" / "每 6 小时". */
export function intervalLabel(seconds: number, t: TFunction): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return t('ui.InboxOverlay.channels.everyHours', { count: seconds / 3600 });
  return t('ui.InboxOverlay.channels.everyMinutes', { count: Math.max(1, Math.round(seconds / 60)) });
}
