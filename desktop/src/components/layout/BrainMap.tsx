/**
 * BrainMap — the Brain map picture: the six DDD sections as a hex flower around the
 * Brain, with You (review & judge), the Project Agent, Reflect / Evolution and Eval laid
 * out around it, and arrows that say who does what. A Brain holds knowledge only: the
 * Runs that use it are in Pipelines and Swarm Out, not on the map (design
 * 2026-11-01-brain-eval-and-runs §2.1). Each arrow's meaning is written ON the map, in a label pill
 * beside the line ("drafts, you submit", "writes rules after your OK"), coloured by what
 * it carries (a request, the Run flow, knowledge written, a rule waiting for your OK), and each
 * part carries a status dot (healthy / attention / needs you) and one line of what
 * it is, so the picture reads without a legend.
 *
 * Pure presentation: every number and tone arrives in props (see brainMapModel).
 * Each part is a button-like <g> that calls `onOpen(target)`. Theme variables only;
 * type uses the nav scale classes (text-meta / text-small / text-body).
 *
 * Exports: BrainMap, BrainMapTarget, BrainMapProps, MapTone
 */
import { useTranslation } from 'react-i18next';
import type { SectionKey } from '../../services/ddd';
import { SECTION_ORDER } from './brainMapModel';

export type BrainMapTarget = 'need' | 'agent' | 'reflect' | 'eval' | 'browse';
/** A part's status dot: healthy, attention, needs you, busy; undefined = no dot. */
export type MapTone = 'ok' | 'attention' | 'needs' | 'busy';

export interface BrainMapProps {
  version: string;
  counts: Record<SectionKey, string>;
  needs: number;
  /** entries learned in the last 7 days (null: not loaded yet) */
  reflect: { week: number | null };
  /** one line under the Project Agent and Eval titles (status / headline number) */
  agentLine?: string;
  evalLine?: string;
  /** status dots per part (section keys, plus agent / reflect) */
  tones?: Partial<Record<SectionKey | 'agent' | 'reflect', MapTone>>;
  onOpen: (t: BrainMapTarget, section?: SectionKey) => void;
}

const W = 720, H = 490;
const C = { x: 360, y: 246 };
const HR = 50;
const HD = Math.sqrt(3) * HR + 6;
const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};
const P = {
  agent: { x: 92, y: 246 }, evo: { x: 622, y: 412 },
  need: { x: 70, y: 56 }, eval: { x: 620, y: 56 },
};

function secPos(k: SectionKey) {
  const a = (ANG[k] * Math.PI) / 180;
  return { x: C.x + HD * Math.cos(a), y: C.y + HD * Math.sin(a) };
}
function hex(x: number, y: number, r: number) {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    p.push(`${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`);
  }
  return p.join(' ');
}
function toward(c: { x: number; y: number }, p: { x: number; y: number }, r: number) {
  const dx = p.x - c.x, dy = p.y - c.y, l = Math.hypot(dx, dy) || 1;
  return { x: c.x + (dx / l) * r, y: c.y + (dy / l) * r };
}

const DOT_FILL: Record<MapTone, string> = {
  ok: 'fill-[var(--color-status-success)]',
  attention: 'fill-[var(--color-status-warning)]',
  needs: 'fill-[var(--color-status-error)]',
  busy: 'fill-[var(--color-primary)]',
};
/** Part outlines: 1.5px on screen at any map width (the viewBox scales down to ~0.85). */
const OUTLINE = { strokeWidth: 1.5, vectorEffect: 'non-scaling-stroke' as const };

/** What a line means, by colour: a request coming in, the Run flow, knowledge written,
 *  a rule waiting for your OK. Each has its own arrowhead of the same colour. */
type Flow = 'request' | 'run' | 'learn' | 'approve';
const FLOW_VAR: Record<Flow, string> = {
  request: '--color-primary', run: '--color-text-muted', learn: '--color-status-success', approve: '--color-status-warning',
};
const SUB = 'text-small fill-[var(--color-text-muted)]';
const TITLE = 'text-body font-semibold fill-[var(--color-text)]';

function Dot({ x, y, tone }: { x: number; y: number; tone?: MapTone }) {
  if (!tone) return null;
  return <circle cx={x} cy={y} r={4.5} className={DOT_FILL[tone]} data-tone={tone} />;
}

/** An arrow with its meaning in a pill beside the line (never on top of it). */
function Arrow({ a, b, via = [], label, dashed, lx, ly, flow = 'run' }: {
  a: { x: number; y: number }; b: { x: number; y: number }; via?: { x: number; y: number }[];
  label?: string; dashed?: boolean; lx?: number; ly?: number; flow?: Flow;
}) {
  const x = lx ?? (a.x + b.x) / 2, y = ly ?? (a.y + b.y) / 2;
  const w = label ? label.length * 6.4 + 18 : 0;
  const v = FLOW_VAR[flow];
  return (
    <g data-flow={flow}>
      {/* non-scaling: 2px on screen however wide the map is drawn */}
      <path d={`M${a.x},${a.y} ${via.map((q) => `L${q.x},${q.y} `).join('')}L${b.x},${b.y}`} fill="none" strokeLinejoin="round" style={{ stroke: `var(${v})` }} strokeWidth={2}
        vectorEffect="non-scaling-stroke" strokeLinecap="round"
        strokeDasharray={dashed ? '6 5' : undefined} markerEnd={`url(#bm-arrow-${flow})`} />
      {label && (
        <g>
          <rect x={x - w / 2} y={y - 10} width={w} height={20} rx={10} className="fill-[var(--color-card)]"
            style={{ stroke: `color-mix(in srgb, var(${v}) 55%, transparent)` }} />
          <text x={x} y={y + 4} textAnchor="middle" className="text-meta" style={{ fill: `var(${v})` }}>{label}</text>
        </g>
      )}
    </g>
  );
}

/** A clickable part of the map (keyboard reachable). */
function Part({ label, onClick, testid, children }: {
  label: string; onClick: () => void; testid: string; children: React.ReactNode;
}) {
  return (
    <g role="button" tabIndex={0} aria-label={label} data-testid={testid} className="cursor-pointer group outline-none"
      onClick={onClick} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }}>
      {children}
    </g>
  );
}

export function BrainMap({ version, counts, needs, reflect, agentLine, evalLine, tones = {}, onOpen }: BrainMapProps) {
  const { t } = useTranslation();
  const d = secPos('delivery'), k = secPos('knowledge'), g = secPos('gates');
  // outlines: the muted text colour (the border colour sinks into a dark card), and a
  // fixed width on screen however wide the map is drawn
  const box = 'fill-[var(--color-card)] stroke-[color-mix(in_srgb,var(--color-text-muted)_70%,transparent)] group-hover:stroke-[var(--color-primary)] group-focus:stroke-[var(--color-primary)]';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={t('ui.BrainMap.mapAria')} data-testid="brain-map"
      className="block mx-auto max-w-[860px]">
      <defs>
        {(Object.keys(FLOW_VAR) as Flow[]).map((f) => (
          <marker key={f} id={`bm-arrow-${f}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="11" markerHeight="11"
            markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0,0L10,5L0,10z" style={{ fill: `var(${FLOW_VAR[f]})` }} />
          </marker>
        ))}
      </defs>

      {/* faint Eval reads: it scores the Brain's knowledge and Reflect's writes */}
      <path d={`M${P.eval.x - 20},${P.eval.y + 26} L${C.x + 40},${C.y - HD - 34}`} fill="none" style={{ stroke: 'var(--color-text-muted)' }}
        strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeDasharray="1 5" strokeLinecap="round" />
      <path d={`M${P.eval.x},${P.eval.y + 26} L${P.evo.x},${P.evo.y - 58}`} fill="none" style={{ stroke: 'var(--color-text-muted)' }}
        strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeDasharray="1 5" strokeLinecap="round" />

      <Arrow a={{ x: P.agent.x + 72, y: P.agent.y }} b={{ x: d.x - HR + 4, y: d.y }} label={t('ui.BrainMap.arrowReads')} lx={(P.agent.x + 72 + d.x - HR) / 2} ly={P.agent.y - 16} flow="request" />
      {/* around Gates, so it plainly ends at Knowledge (not under the Gates hex) */}
      <Arrow a={{ x: P.evo.x + 26, y: P.evo.y - 61 }} via={[{ x: P.evo.x + 26, y: k.y }]} b={{ x: k.x + HR * 0.87 + 3, y: k.y }}
        label={t('ui.BrainMap.arrowWritesKnowledge')} lx={P.evo.x + 26} ly={262} flow="learn" />
      <Arrow a={{ x: P.evo.x - 40, y: P.evo.y - 42 }} b={toward(g, P.evo, HR * 0.92)} label={t('ui.BrainMap.arrowWritesRules')} dashed lx={552} ly={330} flow="approve" />

      {/* the DDD flower */}
      <Part label={t('ui.BrainMap.brainAria')} onClick={() => onOpen('browse')} testid="bm-brain">
        <polygon points={hex(C.x, C.y, HR)} className="fill-[color-mix(in_srgb,var(--color-primary)_14%,transparent)] stroke-[var(--color-primary)]" {...OUTLINE} strokeWidth={2.5} />
        <text x={C.x} y={C.y - 3} textAnchor="middle" className="text-heading font-semibold fill-[var(--color-text)]">{t('ui.BrainMap.brainTitle')}</text>
        <text x={C.x} y={C.y + 15} textAnchor="middle" className="text-small fill-[var(--color-primary)]">{version}</text>
      </Part>
      {SECTION_ORDER.map(({ key, label }) => {
        const p = secPos(key);
        return (
          <Part key={key} label={`${label}: ${counts[key]}`} onClick={() => onOpen('browse', key)} testid={`bm-sec-${key}`}>
            <polygon points={hex(p.x, p.y, HR)} className={box} {...OUTLINE} />
            <Dot x={p.x} y={p.y - 26} tone={tones[key]} />
            <text x={p.x} y={p.y + 2} textAnchor="middle" className={TITLE}>{label}</text>
            <text x={p.x} y={p.y + 19} textAnchor="middle" className={SUB}>{counts[key]}</text>
          </Part>
        );
      })}

      {/* Project Agent */}
      <Part label={t('ui.BrainMap.agentTitle')} onClick={() => onOpen('agent')} testid="bm-agent">
        <rect x={P.agent.x - 72} y={P.agent.y - 38} width={144} height={76} rx={10} className={box} {...OUTLINE} />
        <Dot x={P.agent.x - 58} y={P.agent.y - 24} tone={tones.agent} />
        <text x={P.agent.x} y={P.agent.y - 12} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.agentTitle')}</text>
        <text x={P.agent.x} y={P.agent.y + 6} textAnchor="middle" className={SUB}>{t('ui.BrainMap.agentSub')}</text>
        {agentLine && <text x={P.agent.x} y={P.agent.y + 24} textAnchor="middle" className={SUB}>{agentLine}</text>}
      </Part>

      {/* Reflect / Evolution */}
      <Part label={t('ui.BrainMap.reflectAria')} onClick={() => onOpen('reflect')} testid="bm-reflect">
        <circle cx={P.evo.x} cy={P.evo.y} r={66} className="fill-[color-mix(in_srgb,var(--color-zone-cognitive)_16%,transparent)] stroke-[var(--color-zone-cognitive)] group-hover:stroke-[var(--color-primary)]" {...OUTLINE} strokeWidth={2} />
        <Dot x={P.evo.x} y={P.evo.y - 40} tone={tones.reflect} />
        <text x={P.evo.x} y={P.evo.y - 14} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.reflectTitle')}</text>
        <text x={P.evo.x} y={P.evo.y + 4} textAnchor="middle" className={SUB}>{t('ui.BrainMap.reflectSub')}</text>
        <text x={P.evo.x} y={P.evo.y + 22} textAnchor="middle"
          className={SUB}>{reflect.week == null ? '…' : t('ui.BrainMap.reflectWeek', { n: reflect.week })}</text>
      </Part>

      {/* You, Eval */}
      <Part label={t('ui.BrainMap.youAria', { n: needs })} onClick={() => onOpen('need')} testid="bm-you">
        <circle cx={P.need.x} cy={P.need.y} r={28} {...OUTLINE} strokeWidth={2}
          className={needs ? 'fill-[color-mix(in_srgb,var(--color-status-error)_14%,transparent)] stroke-[var(--color-status-error)]'
            : 'fill-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] stroke-[var(--color-status-success)]'} />
        <text x={P.need.x} y={P.need.y + 6} textAnchor="middle"
          className={`text-title font-semibold ${needs ? 'fill-[var(--color-status-error)]' : 'fill-[var(--color-status-success)]'}`}>{needs}</text>
        <text x={P.need.x + 40} y={P.need.y - 3} className={TITLE}>{t('ui.BrainMap.youTitle')}</text>
        <text x={P.need.x + 40} y={P.need.y + 15} className={SUB}>{t('ui.BrainMap.youSub')}</text>
      </Part>
      <Part label={t('ui.BrainMap.evalTitle')} onClick={() => onOpen('eval')} testid="bm-eval">
        <rect x={P.eval.x - 80} y={P.eval.y - 26} width={160} height={52} rx={10} className={box} {...OUTLINE} />
        <text x={P.eval.x} y={P.eval.y - 5} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.evalTitle')}</text>
        <text x={P.eval.x} y={P.eval.y + 13} textAnchor="middle" className={SUB}>{evalLine ?? t('ui.BrainMap.evalDefault')}</text>
      </Part>
    </svg>
  );
}
