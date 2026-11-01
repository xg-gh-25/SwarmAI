/**
 * BrainMap — the Brain map picture: the six DDD sections as a hex flower, and around it
 * one clockwise loop that says how the Brain is used and grows: its knowledge drives the
 * Project Agent, the agent dispatches Runs in parallel (one lane each), each run reports
 * to Reflect, Reflect proposes lessons, Eval judges them, and what passes is written back
 * into Knowledge; a rule change waits for your OK at Gates, and You come in where the
 * agent asks. The Runs lanes are the rows Pipelines shows (``pipelineRows.brainLanes``,
 * design 2026-11-01-brain-eval-and-runs §2.4): the map never reads runs a second way.
 * Each arrow's meaning is written ON the map, in a label pill beside the line, coloured
 * by what it carries (a request, the run flow, knowledge written, a rule waiting for your
 * OK), and each part carries a status dot, so the picture reads without a legend.
 *
 * Pure presentation: every number and tone arrives in props (see brainMapModel and
 * pipelineRows). Each part is a button-like <g> that calls `onOpen(target)` (a lane:
 * `onOpenRun(row)`). Theme variables only; type uses the nav scale classes.
 *
 * Exports: BrainMap, BrainMapTarget, BrainMapProps, MapTone, MapLane
 */
import { useTranslation } from 'react-i18next';
import type { SectionKey } from '../../services/ddd';
import { SECTION_ORDER } from './brainMapModel';

export type BrainMapTarget = 'need' | 'agent' | 'reflect' | 'eval' | 'browse' | 'runs';
/** A part's status dot: healthy, attention, needs you, busy; undefined = no dot. */
export type MapTone = 'ok' | 'attention' | 'needs' | 'busy';
/** One Runs lane (``pipelineRows.Lane`` without the row it came from). */
export interface MapLane {
  key: string;
  place: string;
  title: string;
  waiting: boolean;
  progress: { done: number; total: number } | null;
}

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
  /** the Brain's runs under way (undefined: not loaded yet) */
  runs?: { lanes: MapLane[]; more: number; total: number };
  onOpen: (t: BrainMapTarget, section?: SectionKey) => void;
  onOpenRun?: (key: string) => void;
}

const W = 860, H = 580;
const C = { x: 430, y: 262 };
const HR = 50;
const HD = Math.sqrt(3) * HR + 6;
const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};
/** the loop's stations, clockwise from the Brain: You (outside), Project Agent, Runs,
 *  Reflect, Eval */
const P = {
  need: { x: 64, y: 96 }, agent: { x: 112, y: 262 }, runs: { x: 152, y: 410 },
  evo: { x: 650, y: 470 }, eval: { x: 760, y: 262 },
};
const LANE = { w: 262, h: 28, gap: 6 };

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
  const v = FLOW_VAR[flow];
  return (
    <g data-flow={flow}>
      {/* non-scaling: 2px on screen however wide the map is drawn */}
      <path d={`M${a.x},${a.y} ${via.map((q) => `L${q.x},${q.y} `).join('')}L${b.x},${b.y}`} fill="none" strokeLinejoin="round" style={{ stroke: `var(${v})` }} strokeWidth={2}
        vectorEffect="non-scaling-stroke" strokeLinecap="round"
        strokeDasharray={dashed ? '6 5' : undefined} markerEnd={`url(#bm-arrow-${flow})`} />
      {label && <Pill x={x} y={y} label={label} flow={flow} />}
    </g>
  );
}

/** An arrow's meaning: a pill coloured by what the line carries. */
/** A label's width at text-meta: a CJK glyph takes about two Latin ones. */
function labelWidth(label: string): number {
  let w = 0;
  for (const ch of label) w += (ch.codePointAt(0) ?? 0) >= 0x2e80 ? 12 : 6.4;
  return w;
}

function Pill({ x, y, label, flow }: { x: number; y: number; label: string; flow: Flow }) {
  const w = labelWidth(label) + 18;
  const v = FLOW_VAR[flow];
  return (
    <g data-pill={flow}>
      <rect x={x - w / 2} y={y - 10} width={w} height={20} rx={10} className="fill-[var(--color-card)]"
        style={{ stroke: `color-mix(in srgb, var(${v}) 55%, transparent)` }} />
      <text x={x} y={y + 4} textAnchor="middle" className="text-meta" style={{ fill: `var(${v})` }}>{label}</text>
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

/** the top of lane *i* */
function laneY(i: number) { return P.runs.y + 22 + i * (LANE.h + LANE.gap); }

export function BrainMap({ version, counts, needs, reflect, agentLine, evalLine, tones = {}, runs, onOpen, onOpenRun }: BrainMapProps) {
  const { t } = useTranslation();
  const k = secPos('knowledge'), g = secPos('gates');
  const lanes = runs?.lanes ?? [];
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

      {/* the loop, clockwise: knowledge drives the agent, the agent dispatches runs,
          each run reports to Reflect, Reflect proposes to Eval, Eval writes back */}
      <Arrow a={{ x: k.x - 6, y: k.y - HR - 2 }} via={[{ x: k.x - 6, y: 128 }, { x: P.agent.x + 20, y: 128 }]}
        b={{ x: P.agent.x + 20, y: P.agent.y - 42 }} label={t('ui.BrainMap.arrowReads')} lx={268} ly={128} flow="learn" />
      {lanes.length ? lanes.map((_, i) => (
        <Arrow key={i} a={{ x: P.agent.x - 30, y: P.agent.y + 40 }}
          via={[{ x: P.agent.x - 30, y: laneY(i) + LANE.h / 2 }]}
          b={{ x: P.runs.x - 4, y: laneY(i) + LANE.h / 2 }} flow="request" />
      )) : <Arrow a={{ x: P.agent.x - 30, y: P.agent.y + 40 }} via={[{ x: P.agent.x - 30, y: laneY(0) + LANE.h / 2 }]}
        b={{ x: P.runs.x - 4, y: laneY(0) + LANE.h / 2 }} flow="request" />}
      <Pill x={P.agent.x + 70} y={P.agent.y + 84} label={t('ui.BrainMap.arrowDispatch')} flow="request" />
      <Arrow a={{ x: P.runs.x + LANE.w + 2, y: laneY(Math.min(1, Math.max(lanes.length - 1, 0))) + LANE.h / 2 }} b={{ x: P.evo.x - 76, y: P.evo.y }}
        label={t('ui.BrainMap.arrowReports')} lx={512} ly={P.evo.y + 30} flow="run" />
      <Arrow a={{ x: P.evo.x + 56, y: P.evo.y - 48 }} via={[{ x: P.eval.x, y: P.evo.y - 48 }]}
        b={{ x: P.eval.x, y: P.eval.y + 50 }} label={t('ui.BrainMap.arrowProposes')} lx={P.eval.x + 22} ly={P.eval.y + 108} flow="request" />
      <Arrow a={{ x: P.eval.x, y: P.eval.y - 48 }} via={[{ x: P.eval.x, y: 150 }, { x: k.x + 70, y: 150 }]}
        b={{ x: k.x + HR * 0.5, y: k.y - HR * 0.72 }} label={t('ui.BrainMap.arrowWritesKnowledge')} lx={650} ly={150} flow="learn" />
      <Arrow a={{ x: P.eval.x - 86, y: P.eval.y }} b={{ x: g.x + HR * 0.87 + 3, y: g.y }}
        label={t('ui.BrainMap.arrowWritesRules')} dashed lx={612} ly={P.eval.y + 62} flow="approve" />
      <Arrow a={{ x: P.need.x, y: P.need.y + 28 }} b={{ x: P.need.x, y: P.agent.y - 42 }} dashed flow="approve" />

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
        <rect x={P.agent.x - 80} y={P.agent.y - 40} width={160} height={80} rx={10}
          className="fill-[var(--color-card)] stroke-[var(--color-primary)] group-hover:stroke-[var(--color-text)]" {...OUTLINE} />
        <Dot x={P.agent.x - 66} y={P.agent.y - 26} tone={tones.agent} />
        <text x={P.agent.x} y={P.agent.y - 12} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.agentTitle')}</text>
        <text x={P.agent.x} y={P.agent.y + 6} textAnchor="middle" className={SUB}>{t('ui.BrainMap.agentSub')}</text>
        {agentLine && <text x={P.agent.x} y={P.agent.y + 24} textAnchor="middle" className={SUB}>{agentLine}</text>}
      </Part>

      {/* Reflect / Evolution */}
      <Part label={t('ui.BrainMap.reflectAria')} onClick={() => onOpen('reflect')} testid="bm-reflect">
        <circle cx={P.evo.x} cy={P.evo.y} r={74} className="fill-[color-mix(in_srgb,var(--color-zone-cognitive)_16%,transparent)] stroke-[var(--color-zone-cognitive)] group-hover:stroke-[var(--color-primary)]" {...OUTLINE} strokeWidth={2} />
        <Dot x={P.evo.x} y={P.evo.y - 40} tone={tones.reflect} />
        <text x={P.evo.x} y={P.evo.y - 14} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.reflectTitle')}</text>
        <text x={P.evo.x} y={P.evo.y + 4} textAnchor="middle" className={SUB}>{t('ui.BrainMap.reflectSub')}</text>
        <text x={P.evo.x} y={P.evo.y + 22} textAnchor="middle"
          className={SUB}>{reflect.week == null ? '…' : t('ui.BrainMap.reflectWeek', { n: reflect.week })}</text>
      </Part>

      {/* You, Eval */}
      <Part label={t('ui.BrainMap.youAria', { n: needs })} onClick={() => onOpen('need')} testid="bm-you">
        <circle cx={P.need.x} cy={P.need.y} r={26} {...OUTLINE} strokeWidth={2}
          className={needs ? 'fill-[color-mix(in_srgb,var(--color-status-error)_14%,transparent)] stroke-[var(--color-status-error)]'
            : 'fill-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] stroke-[var(--color-status-success)]'} />
        <text x={P.need.x} y={P.need.y + 6} textAnchor="middle"
          className={`text-title font-semibold ${needs ? 'fill-[var(--color-status-error)]' : 'fill-[var(--color-status-success)]'}`}>{needs}</text>
        <text x={P.need.x + 36} y={P.need.y - 3} className={TITLE}>{t('ui.BrainMap.youTitle')}</text>
        <text x={P.need.x + 36} y={P.need.y + 15} className={SUB}>{t('ui.BrainMap.youSub')}</text>
      </Part>
      <Part label={t('ui.BrainMap.evalTitle')} onClick={() => onOpen('eval')} testid="bm-eval">
        <rect x={P.eval.x - 86} y={P.eval.y - 46} width={172} height={92} rx={10}
          className="fill-[var(--color-card)] stroke-[var(--color-primary)] group-hover:stroke-[var(--color-text)]" {...OUTLINE} />
        <text x={P.eval.x} y={P.eval.y - 10} textAnchor="middle" className={TITLE}>{t('ui.BrainMap.evalTitle')}</text>
        <text x={P.eval.x} y={P.eval.y + 12} textAnchor="middle" className={SUB}>{evalLine ?? t('ui.BrainMap.evalDefault')}</text>
      </Part>

      {/* Runs: one lane per run under way (the rows Pipelines shows) */}
      <Part label={t('ui.BrainMap.runsAria')} onClick={() => onOpen('runs')} testid="bm-runs">
        <text x={P.runs.x} y={P.runs.y + 10} className={TITLE}>{t('ui.BrainMap.runsTitle')}</text>
        <text x={P.runs.x + 46} y={P.runs.y + 10} className="text-small fill-[var(--color-primary)]">
          {runs === undefined ? '…' : runs.total ? t('ui.BrainMap.runsCount', { count: runs.total }) : t('ui.BrainMap.runsNone')}</text>
        <text x={P.runs.x + LANE.w} y={laneY(Math.max(lanes.length, 1)) + 14} textAnchor="end"
          className="text-meta fill-[var(--color-primary)]">
          {(runs?.more ? `${t('ui.BrainMap.runsMore', { count: runs.more })} · ` : '') + t('ui.BrainMap.runsOpen')}</text>
      </Part>
      {lanes.length === 0 && (
        <rect x={P.runs.x} y={laneY(0)} width={LANE.w} height={LANE.h} rx={7} fill="none"
          className="stroke-[color-mix(in_srgb,var(--color-text-muted)_45%,transparent)]" strokeDasharray="4 4" {...OUTLINE} />
      )}
      {lanes.map((lane, i) => {
        const y = laneY(i);
        const tone = lane.waiting ? 'var(--color-status-warning)' : 'var(--color-primary)';
        return (
          <Part key={lane.key} label={t('ui.BrainMap.laneAria', { place: lane.place, title: lane.title })}
            onClick={() => onOpenRun?.(lane.key)} testid={`bm-lane-${lane.key}`}>
            <rect x={P.runs.x} y={y} width={LANE.w} height={LANE.h} rx={7} className={box} {...OUTLINE} />
            <text x={P.runs.x + 12} y={y + 18} className="text-small fill-[var(--color-text)]">{lane.place}</text>
            {lane.progress ? Array.from({ length: lane.progress.total }, (_, j) => (
              <circle key={j} cx={P.runs.x + 112 + j * 15} cy={y + LANE.h / 2} r={4}
                style={{ fill: j < lane.progress!.done ? tone : 'var(--color-border)' }} data-testid={`bm-lane-dot-${lane.key}`} />
            )) : (
              <text x={P.runs.x + 112} y={y + 18} className="text-meta" style={{ fill: tone }}>
                {lane.waiting ? t('ui.BrainMap.laneWaits') : t('ui.BrainMap.laneGoing')}</text>
            )}
            {lane.progress && lane.waiting && (
              <text x={P.runs.x + LANE.w - 10} y={y + 18} textAnchor="end" className="text-meta" style={{ fill: tone }}>
                {t('ui.BrainMap.laneWaits')}</text>
            )}
          </Part>
        );
      })}
    </svg>
  );
}
