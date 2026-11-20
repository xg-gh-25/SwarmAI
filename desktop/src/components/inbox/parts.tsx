/**
 * Inbox shared parts — the small pieces every Inbox lane draws the same way (design
 * docs/design/2026-11-20-inbox.md §7, mockup docs/design/2026-11-20-inbox-mockup.html).
 *
 * - `Avatar`       initials (or a bot / channel icon) on a tint of one theme colour
 * - `ChannelPill`  icon + channel name in the channel's colour (`--color-channel-*`)
 * - `StatusLine`   the overlay's own short confirmation line ("已发送给 …")
 * - `relTime` / `clockTime` / `dayKey` / `intervalLabel`  time text
 * - `webUrl`       a source's link, only when it is http(s)
 *
 * Colours come only from theme variables; a tint is `color-mix(<var> N%, …)`.
 */
import { useEffect, type ReactNode } from 'react';
import type { TFunction } from 'i18next';
import { channelKey, type ChannelKey } from '../../services/inbox';

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

/** "今天" / "昨天" / the date, for a day group heading. */
export function dayLabel(key: string, t: TFunction, now = new Date()): string {
  const today = dayKey(now.toISOString());
  const y = new Date(now.getTime() - DAY);
  if (key === today) return t('ui.InboxOverlay.time.today');
  if (key === dayKey(y.toISOString())) return t('ui.InboxOverlay.time.yesterday');
  return key;
}

/** "每 15 分钟" / "每 6 小时". */
export function intervalLabel(seconds: number, t: TFunction): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return t('ui.InboxOverlay.channels.everyHours', { count: seconds / 3600 });
  return t('ui.InboxOverlay.channels.everyMinutes', { count: Math.max(1, Math.round(seconds / 60)) });
}
