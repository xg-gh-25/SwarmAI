/**
 * OS Eval overlay — shared pieces: language, labels, tags, the sparkline, section
 * headers, attention rows, the side panel and the two hand-offs to chat ("let Swarm
 * look", "open the session").
 *
 * Visual rules (s_frontend-design design-judgment.md): one verdict stands out per
 * page; sections are separated by whitespace and a hairline, not boxes; tier and
 * evidence layer keep one colour each across every tab.
 *
 * @exports useLang, labels, caseName, judgeReason, rowReason, statusLabel, triggerLabel, LinkBtn, TierTag, TierText, LayerTag,
 *          Tag, Spark, Zone, Row, Seg, rovingKey, SidePanel, InlineConfirm, QueryNote, EvalActionsContext,
 *          useEvalActions, when, pct
 *
 * Layers (the side panel, a popover) all behave the same: focus moves in when one
 * opens, Tab stays inside, Escape or a click outside asks to close, focus goes back
 * to what opened it, and a layer holding unsaved input asks before it is dropped.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { useEscape, useLayerFocus as baseLayerFocus } from '../../hooks/useLayer';
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
  // the backend's rule (core/eval_followups._label): a CJK stop ends a sentence; a
  // Latin one only before a space or the end, so "MEMORY.md" is not cut
  const m = text.match(/^(.+?(?:[。！？]|[.!?](?=\s|$)))\s*(.*)$/s);
  return m ? [m[1], m[2]] : [text, ''];
}
/** A case's short name in a list: the title (written in English), else the first
 *  sentence of its purpose in the user's language, else the id. The full purpose
 *  is for the case's own page. */
export function caseName(c: { id: string; title?: string; purpose?: { en?: string; zh?: string } | null }, lang: Lang): string {
  if (lang === 'en' && c.title) return c.title;
  const head = splitPurpose(pick(c.purpose, lang))[0];
  return head || c.title || c.id;
}
/** The reason in a run note, without the evaluator's framing: the decision judge
 *  writes ``… — [decision-judge conf=0.85] <reason>``; anything else keeps its
 *  first line. The full note stays on the case's page. */
export function judgeReason(notes?: string | null, lang: Lang = 'en'): string {
  const text = (notes ?? '').trim();
  const m = text.match(/\[decision-judge conf=[\d.]+\]\s*(.+)$/s);
  const body = (m ? m[1] : text.split('\n')[0]).trim();
  // the trajectory check writes a Python list: "Missing steps: ['Read A', 'Read B']"
  const miss = body.match(/^Missing steps:\s*\[(.*)\]\s*$/s);
  if (miss) {
    const steps = miss[1].split(/,\s*/).map((x) => x.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
    return lang === 'zh' ? `期望的步骤没出现：${steps.join('、')}` : `Expected steps not seen: ${steps.join(', ')}`;
  }
  return body.replace(/\.{1,2}\s*All \d+ (expected )?steps found\.?$/, '.').trim();
}

/** The one-line reason in a list row. A failed case shows the check's own reason; a
 *  case that was not measured or timed out shows what that means, because its note is
 *  the evaluator's error (the case's page keeps the full note). */
export function rowReason(status: string | null | undefined, notes: string | null | undefined, lang: Lang): string {
  if (status === 'error') return lang === 'zh' ? '检查本身没跑成，不是 agent 的问题（原因在详情里）' : 'the check itself could not run, not the agent (reason on the case)';
  if (status === 'timeout') return lang === 'zh' ? '没在时限内跑完' : 'did not finish in time';
  return judgeReason(notes, lang);
}

const STATUS_LABEL: Record<string, { en: string; zh: string }> = {
  passed: { en: 'Passed', zh: '通过' }, failed: { en: 'Failed', zh: '没通过' },
  timeout: { en: 'Timed out', zh: '超时' }, error: { en: 'Not measured', zh: '没测成' },
  skipped: { en: 'Skipped', zh: '跳过' },
};
/** A run status in words (the backend writes ``passed`` / ``failed`` / ...). */
export const statusLabel = (s: string | null | undefined, lang: Lang) =>
  (s && STATUS_LABEL[s]?.[lang]) || s || '—';

const TRIGGER_LABEL: Record<string, { en: string; zh: string }> = {
  manual: { en: 'run by you', zh: '你手动跑的' }, canary: { en: 'quick check, run by you', zh: '你手动跑的快速检查' },
  session_canary: { en: 'after a session', zh: '会话结束后' }, deploy: { en: 'after a deploy', zh: '部署后' },
  scheduled: { en: 'scheduled', zh: '定时' },
  steering_edit: { en: 'after a rule change', zh: '改规则后' }, model_change: { en: 'after a model change', zh: '换模型后' },
  unknown: { en: '', zh: '' },
};
/** Who started a run (the backend's ``triggered_by``), in words. */
export const triggerLabel = (s: string | null | undefined, lang: Lang) =>
  !s ? '' : TRIGGER_LABEL[s] ? TRIGGER_LABEL[s][lang] : s;

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

/** The tier as plain text in a list column: only a red line is coloured. */
export function TierText({ tier }: { tier?: ReleaseTier }) {
  const { lang } = useLang();
  const t = tier ?? 'watch';
  return (
    <span data-tier={t} title={TIER_LABEL[t].what[lang]} className="text-small whitespace-nowrap"
          style={{ color: t === 'red' ? 'var(--color-status-error)' : 'var(--color-text-dim)' }}>
      {TIER_LABEL[t][lang]}
    </span>
  );
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
  const { t } = useLang();
  const pad = 4;
  const n = Math.max(weeks.length, 2);
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (n - 1);
  const H = height + (big ? 16 : 0);
  const sparse = series.every((s) => weeks.filter((w) => s.points[w] != null).length < 2);
  return (
    <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={t('trend', '趋势')}>
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

/** One row of a list: what (one line), a plain sub-line, actions on the right. When
 *  the row opens something, its text is the button and the actions sit beside it,
 *  never inside it (a control inside a control is unreachable to a screen reader). */
export function Row({ what, sub, right, onOpen, testId, dot }: {
  what: ReactNode; sub?: ReactNode; right?: ReactNode; onOpen?: () => void; testId?: string;
  /** a status colour shown before the text (only where status is the point) */
  dot?: string;
}) {
  const text = (
    <>
      {dot && <span className="w-1.5 h-1.5 rounded-full mt-2 shrink-0" style={{ background: dot }} />}
      <span className="block min-w-0 flex-1">
        <span className="block text-body font-semibold text-[var(--color-text)] line-clamp-2">{what}</span>
        {sub && <span className="block text-small text-[var(--color-text-muted)] mt-0.5 line-clamp-2 break-words">{sub}</span>}
      </span>
    </>
  );
  // the padding is on the button, so the whole height of the row opens it
  const inner = 'flex gap-3 items-start min-w-0 flex-1 text-left py-3.5';
  return (
    <div data-testid={testId} className={`w-full flex gap-3 items-stretch border-t border-[var(--color-border)] ${onOpen ? 'hover:bg-[var(--color-hover)]/40' : ''}`}>
      {onOpen ? <button type="button" onClick={onOpen} className={`${inner} cursor-pointer`}>{text}</button> : <div className={inner}>{text}</div>}
      {right && <div className="flex gap-2 items-start shrink-0 py-3.5">{right}</div>}
    </div>
  );
}

/** Arrow keys (and Home / End) move the choice within a group of buttons that are
 *  siblings in one parent: the tab bar, a segmented choice. Only the chosen button
 *  is in the Tab order, so Tab enters and leaves the group in one step. */
export function rovingKey<T>(e: ReactKeyboardEvent<HTMLElement>, values: readonly T[], current: T, choose: (v: T) => void) {
  const i = Math.max(0, values.indexOf(current));
  const n = values.length;
  const next = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? (i + 1) % n
    : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? (i - 1 + n) % n
      : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
  if (next < 0) return;
  e.preventDefault();
  choose(values[next]);
  (e.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
}

/** A segmented choice (one of a few views or filters). */
export function Seg<T extends string>({ value, options, onChange, testId, label }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; testId?: string; label?: string;
}) {
  const values = options.map(([v]) => v);
  return (
    <div className="inline-flex rounded-md border border-[var(--color-border)] overflow-hidden" data-testid={testId} role="radiogroup" aria-label={label}>
      {options.map(([v, label]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
                tabIndex={value === v ? 0 : -1} onKeyDown={(e) => rovingKey(e, values, value, onChange)}
                data-testid={testId ? `${testId}-${v}` : undefined}
                className={`px-3 py-1 text-small transition-colors ${value === v
                  ? 'bg-[var(--color-hover)] text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** A text link that moves somewhere in the overlay: a real button, so Tab and
 *  Enter reach it (an ``<a>`` without ``href`` is skipped by the keyboard). */
export function LinkBtn({ children, onClick, className = '' }: { children: ReactNode; onClick: () => void; className?: string }) {
  return <button type="button" onClick={onClick} className={`cursor-pointer text-left hover:underline ${className}`}>{children}</button>;
}

export function Btn({ children, onClick, kind = 'normal', disabled, testId, title, tabIndex, ariaExpanded }: {
  children: ReactNode; onClick?: () => void; kind?: 'normal' | 'primary' | 'ghost' | 'danger';
  disabled?: boolean; testId?: string; title?: string;
  /** -1 for a mouse shortcut that repeats a control already in the Tab order */
  tabIndex?: number;
  ariaExpanded?: boolean;
}) {
  const cls = kind === 'primary' ? 'btn-primary' : kind === 'ghost' ? 'btn-ghost' : kind === 'danger' ? 'btn-danger' : 'btn-secondary';
  return (
    <button type="button" data-testid={testId} title={title} disabled={disabled} onClick={onClick} tabIndex={tabIndex} aria-expanded={ariaExpanded}
            className={`btn-sm ${cls} transition-colors disabled:opacity-40 disabled:cursor-not-allowed`}>
      {children}
    </button>
  );
}

/** Where focus goes when the element that opened a layer is gone (a deep link from
 *  another tab, a deleted case's row): the overlay's tab panel. */
const fallbackFocus = () => document.getElementById('eval-scroll');

/** The shared layer focus (hooks/useLayer), falling back to the overlay's tab panel. */
export function useLayerFocus(open: boolean, ref: RefObject<HTMLElement | null>, opts: { resetKey?: string; first?: boolean } = {}) {
  baseLayerFocus(open, ref, { ...opts, fallback: fallbackFocus });
}
export { useEscape };

/** A question asked in place before something is lost: the safe answer has focus,
 *  Escape is the safe answer, and when the question goes focus returns to what had it
 *  before (or to the layer it sits in). */
export function InlineConfirm({ message, confirm, cancel, onConfirm, onCancel, testId, busy = false }: {
  message: ReactNode; confirm: string; cancel: string; onConfirm: () => void; onCancel: () => void; testId?: string;
  /** the confirmed action is running: it cannot be sent twice */
  busy?: boolean;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  useEscape(true, onCancel);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const layer = keep.current?.closest<HTMLElement>('[role="dialog"]') ?? null;
    keep.current?.focus();
    return () => {
      if (before && before.isConnected && before !== document.body) before.focus();
      else layer?.focus();
    };
  }, []);
  return (
    <div role="alertdialog" aria-label={typeof message === 'string' ? message : undefined} data-testid={testId}
         className="flex flex-wrap items-center gap-3 rounded-md border border-[var(--color-status-warning)] p-3 text-small text-[var(--color-text)]"
         style={{ background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }}>
      <span className="flex-1 min-w-[180px]">{message}</span>
      <button ref={keep} type="button" className="btn-sm btn-secondary" onClick={onCancel} data-testid={testId ? `${testId}-cancel` : undefined}>{cancel}</button>
      <button type="button" className="btn-sm btn-danger disabled:opacity-40 disabled:cursor-not-allowed" disabled={busy} onClick={onConfirm}
              data-testid={testId ? `${testId}-ok` : undefined}>{confirm}</button>
    </div>
  );
}

/** The right-hand panel (case detail, the case form, a follow-up). Escape, ✕ or a
 *  click outside asks to close; when ``isDirty()`` says the panel holds unsaved input,
 *  it first asks whether to discard it. ``isDirty`` is read at the moment of the key
 *  or click, not from the last render: a key pressed right after typing would
 *  otherwise see the state from before the typing and drop it. Focus moves in, stays
 *  in, and goes back (``useLayerFocus``). */
export function SidePanel({ open, onClose, children, testId, label, isDirty, resetKey }: {
  open: boolean; onClose: () => void; children: ReactNode; testId?: string;
  /** what the panel is, read out when it opens */
  label: string;
  /** whether the panel holds input that closing would throw away, now */
  isDirty?: () => boolean;
  resetKey?: string;
}) {
  const { t } = useLang();
  const ref = useRef<HTMLElement>(null);
  const [asking, setAsking] = useState(false);
  useEffect(() => { if (!open) setAsking(false); }, [open]);
  const request = useCallback(() => { if (isDirty?.()) setAsking(true); else onClose(); }, [isDirty, onClose]);
  useEscape(open, request);           // while the question is open, it takes Escape
  useLayerFocus(open, ref, { resetKey });
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" onClick={request}>
      <aside ref={ref} data-testid={testId} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}
             className="absolute right-0 top-0 h-full w-[560px] max-w-[92vw] overflow-y-auto bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-xl p-7 outline-none"
             onClick={(e) => e.stopPropagation()}>
        <button type="button" aria-label={t('Close', '关闭')} onClick={request}
                className="absolute right-5 top-4 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">✕</button>
        {asking && (
          <div className="sticky top-0 z-10 -mx-1 mb-4 bg-[var(--color-card)] pr-8">
            <InlineConfirm testId="discard" message={t('You have unsaved changes.', '有没保存的修改。')}
                           cancel={t('Keep editing', '继续编辑')} confirm={t('Discard', '不要了')}
                           onCancel={() => setAsking(false)} onConfirm={() => { setAsking(false); onClose(); }} />
          </div>
        )}
        {children}
      </aside>
    </div>
  );
}

/** A section's own data still loading or not loaded: said as such, never shown as
 *  "nothing yet" (an empty answer reads as a result). */
export function QueryNote({ q, children }: { q: { isLoading: boolean; isError: boolean }; children: ReactNode }) {
  const { t } = useLang();
  if (q.isLoading) return <div className="text-small text-[var(--color-text-dim)] py-2">{t('Loading…', '加载中…')}</div>;
  if (q.isError) return <div className="text-small text-[var(--color-status-error)] py-2">{t('Could not load this part.', '这一部分没加载出来。')}</div>;
  return <>{children}</>;
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
export type TabId = 'overview' | 'cases' | 'online' | 'method';
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
