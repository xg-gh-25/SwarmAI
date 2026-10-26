/**
 * OS Eval overlay — shared pieces: language, labels, tags, the sparkline, section
 * headers, attention rows, the side panel and the two hand-offs to chat ("let Swarm
 * look", "open the session").
 *
 * Visual rules (s_frontend-design design-judgment.md): one verdict stands out per
 * page; sections are separated by whitespace and a hairline, not boxes; tier and
 * evidence layer keep one colour each across every tab.
 *
 * @exports useLang, labels, TierTag, LayerTag, Tag, Spark, Zone, Item, SidePanel,
 *          EvalActionsContext, useEvalActions, when, pct
 */
import { createContext, useContext, useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { ReleaseTier } from './api';

// ─── language ──────────────────────────────────────────────────────────────

export type Lang = 'en' | 'zh';
/** The overlay's language follows the app's; ``t(en, zh)`` picks one string. */
export function useLang() {
  const { i18n } = useTranslation();
  const lang: Lang = i18n?.language?.startsWith('zh') ? 'zh' : 'en';
  return { lang, t: (en: string, zh: string) => (lang === 'zh' ? zh : en) };
}
export const pick = (b: { en?: string; zh?: string } | null | undefined, lang: Lang) =>
  (b?.[lang] || b?.en || b?.zh || '').trim();
/** A purpose is "what it protects. What breaks if it fails." — the head and the rest. */
export function splitPurpose(text: string): [string, string] {
  const m = text.match(/^(.+?[。.!！?？])\s*(.*)$/s);
  return m ? [m[1], m[2]] : [text, ''];
}

// ─── labels ────────────────────────────────────────────────────────────────

export const DIM_LABEL: Record<string, { en: string; zh: string; q: { en: string; zh: string }; small: { en: string; zh: string } }> = {
  cognition: { en: 'Cognition', zh: '认知', q: { en: 'Does it really know your work', zh: '它真的懂你的工作吗' },
    small: { en: 'memory, recall, project docs, knowledge used right', zh: '记忆、召回、项目文档、知识库用对了没有' } },
  quality: { en: 'Quality', zh: '质量', q: { en: 'Is it right, did it deliver', zh: '对不对、有没有交付' },
    small: { en: 'no agreeing with a wrong premise, delivers what you asked', zh: '不附和错误前提、交付了你要的东西' } },
  responsibility: { en: 'Responsibility', zh: '责任', q: { en: 'Does it keep the boundaries and rules', zh: '守不守边界和规矩' },
    small: { en: 'privacy, irreversible actions, isolation, your way of working', zh: '隐私、不可逆操作、隔离、照你定的方式做事' } },
  cost: { en: 'Cost', zh: '成本', q: { en: 'What a turn costs', zh: '每轮花多少' },
    small: { en: 'API price, from each turn’s token use', zh: '按 API 价，从每轮 token 用量算' } },
  performance: { en: 'Performance', zh: '性能', q: { en: 'How long you wait', zh: '要等多久' },
    small: { en: 'from your message to the last reply', zh: '从你发出到最后一条回复' } },
};
export const dimName = (d: string, lang: Lang) => DIM_LABEL[d]?.[lang] ?? d;

export const CATEGORY_LABEL: Record<string, { en: string; zh: string }> = {
  recall: { en: 'Recall', zh: '召回' }, context: { en: 'Context', zh: '上下文' },
  pipeline: { en: 'Pipeline', zh: '流水线' }, memory: { en: 'Memory', zh: '记忆' },
  ingestion: { en: 'Knowledge intake', zh: '知识入库' }, knowledge: { en: 'Knowledge', zh: '知识库' },
  ddd: { en: 'Project brain', zh: '项目大脑' }, eval: { en: 'Eval self-check', zh: '评测自检' },
  safety: { en: 'Safety', zh: '安全' }, judgment: { en: 'Judgment', zh: '判断' },
  decision: { en: 'Decision', zh: '决策' }, compliance: { en: 'Rules', zh: '规则' },
};
export const categoryName = (c: string, lang: Lang) => CATEGORY_LABEL[c]?.[lang] ?? c;

export const TIER_LABEL: Record<ReleaseTier, { en: string; zh: string; what: { en: string; zh: string } }> = {
  red: { en: 'Red line', zh: '红线', what: { en: 'fails the release on its own', zh: '碰到即否决' } },
  gate: { en: 'Gate', zh: '门禁', what: { en: 'must pass to release', zh: '过线才放行' } },
  watch: { en: 'Watch', zh: '观测', what: { en: 'shown, never blocks', zh: '只看不拦' } },
};
const TIER_COLOR: Record<ReleaseTier, string> = {
  red: 'var(--color-status-error)', gate: 'var(--color-primary)', watch: 'var(--color-text-muted)',
};
/** Evidence layer: L1 code-checked, L2 judged by a model. */
const LAYER_COLOR = { L1: 'var(--color-primary)', L2: 'var(--color-brand)' } as const;

export const isQuick = (c: { eval_method?: string }) => c.eval_method !== 'behavior';

// ─── small parts ───────────────────────────────────────────────────────────

const tint = (color: string, pctTint = 12) => `color-mix(in srgb, ${color} ${pctTint}%, transparent)`;

export function Tag({ children, color, title }: { children: ReactNode; color?: string; title?: string }) {
  return (
    <span title={title} className="inline-flex items-center px-1.5 rounded text-meta whitespace-nowrap"
          style={{ color: color ?? 'var(--color-text-muted)', background: tint(color ?? 'var(--color-text-muted)', 10) }}>
      {children}
    </span>
  );
}

export function TierTag({ tier }: { tier?: ReleaseTier }) {
  const { lang } = useLang();
  const t = tier ?? 'watch';
  return <Tag color={TIER_COLOR[t]} title={TIER_LABEL[t].what[lang]}><span data-tier={t}>{TIER_LABEL[t][lang]}</span></Tag>;
}

export function LayerTag({ layer }: { layer: 'L1' | 'L2' }) {
  return <Tag color={LAYER_COLOR[layer]}>{layer}</Tag>;
}

export function StatusDot({ status }: { status?: string }) {
  const color = status === 'passed' ? 'var(--color-status-success)'
    : status === 'failed' ? 'var(--color-status-warning)'
      : status === 'error' || status === 'timeout' ? 'var(--color-status-error)' : 'var(--color-text-dim)';
  return <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: color }} />;
}

/** ``MM-DD HH:MM`` in the user's local time. A timestamp with a zone (the backend
 *  writes UTC) is converted; one without is already local (message times). */
export function when(iso?: string | null): string {
  if (!iso) return '—';
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  if (!zoned) return iso.slice(5, 16).replace('T', ' ');
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(5, 16).replace('T', ' ');
  const p2 = (n: number) => String(n).padStart(2, '0');
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}
export const day = (iso?: string | null) => (iso ? when(iso).slice(0, 5) : '—');
export const pct = (r?: number | null) => (r == null ? '—' : `${Math.round(r * 100)}%`);

// ─── sparkline ─────────────────────────────────────────────────────────────

/** Series colours of a small multiple, from the theme. */
export const SERIES_COLORS = ['var(--color-brand)', 'var(--color-primary)', 'var(--color-zone-cognitive)'];

export interface SparkSeries {
  /** week label → value; a missing week is a gap, not a zero */
  points: Record<string, number | null | undefined>;
  color?: string;
  /** a value with a unit is scaled to its own max; a rate is 0..1 */
  scaled?: boolean;
  kind?: 'line' | 'dot';
}

/** One small multiple: the weeks on the x axis, gaps where there is no data, the
 *  last point marked. ``empty`` is written in when no series has two points. */
export function Spark({ weeks, series, width = 220, height = 34, big = false, empty }: {
  weeks: string[]; series: SparkSeries[]; width?: number; height?: number; big?: boolean; empty?: string;
}) {
  const pad = 4;
  const n = Math.max(weeks.length, 2);
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (n - 1);
  const H = height + (big ? 16 : 0);
  const sparse = series.every((s) => weeks.filter((w) => s.points[w] != null).length < 2);
  return (
    <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label="trend">
      <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} stroke="var(--color-border)" />
      {series.map((s, si) => {
        const vals = weeks.map((w) => s.points[w]).filter((v): v is number => v != null);
        if (!vals.length) return null;
        const hi = s.scaled ? Math.max(...vals) * 1.15 || 1 : 1;
        const y = (v: number) => pad + (1 - v / hi) * (height - 2 * pad);
        const color = s.color ?? SERIES_COLORS[si % SERIES_COLORS.length];
        let d = '';
        let pen = false;
        weeks.forEach((w, i) => {
          const v = s.points[w];
          if (v == null) { pen = false; return; }
          d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        });
        const last = weeks.map((w, i) => [w, i] as const).filter(([w]) => s.points[w] != null).pop();
        return (
          <g key={si}>
            {s.kind !== 'dot' && <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />}
            {weeks.map((w, i) => {
              const v = s.points[w];
              if (v == null || (s.kind !== 'dot' && last?.[0] !== w)) return null;
              return <circle key={w} cx={x(i)} cy={y(v)} r={s.kind === 'dot' ? 3 : 2.4} fill={color}><title>{`${w} ${v}`}</title></circle>;
            })}
          </g>
        );
      })}
      {sparse && empty && (
        <text x={pad + 2} y={height / 2 + 4} fill="var(--color-text-dim)" fontSize={big ? 11 : 10}>{empty}</text>
      )}
      {big && weeks.length > 1 && [0, Math.floor((weeks.length - 1) / 2), weeks.length - 1].map((i) => (
        <text key={i} x={x(i)} y={height + 12} fill="var(--color-text-dim)" fontSize={10}
              textAnchor={i === 0 ? 'start' : i === weeks.length - 1 ? 'end' : 'middle'}>{weeks[i]?.slice(5)}</text>
      ))}
    </svg>
  );
}

// ─── layout ────────────────────────────────────────────────────────────────

/** A section: a header line (title, what it is, a count on the right), then content.
 *  Sections are told apart by space, not by boxes. */
export function Zone({ id, title, sub, right, children, first }: {
  id?: string; title: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; first?: boolean;
}) {
  return (
    <section id={id} className={first ? 'mt-8' : 'mt-14'}>
      <div className="flex items-baseline gap-3 mb-3">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{title}</h2>
        {sub && <span className="text-small text-[var(--color-text-dim)]">{sub}</span>}
        {right && <span className="ml-auto text-small text-[var(--color-text-muted)]">{right}</span>}
      </div>
      {children}
    </section>
  );
}

/** One thing to look at: a coloured dot, what, why, optional tags, actions on the right. */
export function Item({ tone = 'warning', what, why, extra, tags, actions, testId }: {
  tone?: 'warning' | 'evolution' | 'error'; what: ReactNode; why?: ReactNode; extra?: ReactNode;
  tags?: ReactNode; actions?: ReactNode; testId?: string;
}) {
  const color = tone === 'evolution' ? 'var(--color-zone-cognitive)' : tone === 'error' ? 'var(--color-status-error)' : 'var(--color-status-warning)';
  return (
    <div data-testid={testId} className="grid grid-cols-[10px_1fr_auto] gap-4 py-4 border-t border-[var(--color-border)] last:border-b items-start">
      <span className="w-2 h-2 rounded-full mt-1.5" style={{ background: color }} />
      <div className="min-w-0">
        <div className="text-body font-semibold text-[var(--color-text)] line-clamp-2" title={typeof what === 'string' ? what : undefined}>{what}</div>
        {why && <div className="text-small text-[var(--color-text-muted)] mt-0.5 break-words line-clamp-2" title={typeof why === 'string' ? why : undefined}>{why}</div>}
        {extra}
        {tags && <div className="flex gap-1.5 flex-wrap mt-2">{tags}</div>}
      </div>
      {actions && <div className="flex gap-2 items-center shrink-0">{actions}</div>}
    </div>
  );
}

export function Btn({ children, onClick, kind = 'normal', disabled, testId, title }: {
  children: ReactNode; onClick?: () => void; kind?: 'normal' | 'primary' | 'ghost' | 'danger';
  disabled?: boolean; testId?: string; title?: string;
}) {
  const cls = kind === 'primary' ? 'btn-primary' : kind === 'ghost' ? 'btn-ghost' : kind === 'danger' ? 'btn-danger' : 'btn-secondary';
  return (
    <button type="button" data-testid={testId} title={title} disabled={disabled} onClick={onClick}
            className={`btn-sm ${cls} transition-colors disabled:opacity-40 disabled:cursor-not-allowed`}>
      {children}
    </button>
  );
}

/** The right-hand panel (case detail, the case form). ESC or ✕ closes it. */
export function SidePanel({ open, onClose, children, testId }: {
  open: boolean; onClose: () => void; children: ReactNode; testId?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" onClick={onClose}>
      <aside data-testid={testId} role="dialog"
             className="absolute right-0 top-0 h-full w-[560px] max-w-[92vw] overflow-y-auto bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-xl p-7"
             onClick={(e) => e.stopPropagation()}>
        <button type="button" aria-label="close" onClick={onClose}
                className="absolute right-5 top-4 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">✕</button>
        {children}
      </aside>
    </div>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 text-small">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <span className="text-[var(--color-text-muted)]">{k}</span>
          <span className="text-[var(--color-text)] min-w-0 break-words">{v}</span>
        </div>
      ))}
    </div>
  );
}

export const Code = ({ children }: { children: ReactNode }) => (
  <code className="px-1 py-0.5 rounded text-meta font-mono bg-[var(--color-code-bg,var(--color-hover))] break-all">{children}</code>
);
export const H4 = ({ children }: { children: ReactNode }) => (
  <h4 className="text-small font-semibold text-[var(--color-text-muted)] mt-6 mb-2">{children}</h4>
);

// ─── hand-offs to chat ─────────────────────────────────────────────────────

/** The overlay host's bridges (OverlayRenderCtx): put a prompt in a chat tab, or
 *  open a session. Both close the overlay when they land. */
export interface EvalActions {
  askSwarm: (prompt: string) => void;
  openSession: (sessionId: string) => void;
}
export const EvalActionsContext = createContext<EvalActions>({ askSwarm: () => {}, openSession: () => {} });
export const useEvalActions = () => useContext(EvalActionsContext);

/** The tab bar's "go to" (tab + optional anchor), shared by every cross-link. */
export type GoTo = (tab: TabId, anchor?: string) => void;
export type TabId = 'overview' | 'cases' | 'online' | 'offline' | 'judges' | 'guide';
export const GoContext = createContext<GoTo>(() => {});
export const useGo = () => useContext(GoContext);

export function Loading() {
  const { t } = useLang();
  return <div className="p-10 text-body text-[var(--color-text-muted)]">{t('Loading…', '加载中…')}</div>;
}
export function ErrorState() {
  const { t } = useLang();
  return <div className="p-10 text-body text-[var(--color-status-error)]">{t('Could not load. Is the backend running?', '加载失败。后端在运行吗？')}</div>;
}
