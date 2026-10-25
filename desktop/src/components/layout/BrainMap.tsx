/**
 * BrainMap — the Brain map picture: the six DDD sections as a hex flower around the
 * Brain, with You (review & judge), the Project Agent, the request sources, the one
 * Run Conductor, Reflect / Evolution and Eval laid out around it, and arrows that
 * say who does what. Each arrow's meaning is written ON the map, in a label pill
 * beside the line ("drafts, you submit", "writes rules after your OK"), and each
 * part carries a status dot (healthy / attention / needs you) and one line of what
 * it is, so the picture reads without a legend.
 *
 * Pure presentation: every number and tone arrives in props (see brainMapModel).
 * Each part is a button-like <g> that calls `onOpen(target)`. Theme variables only;
 * type uses the nav scale classes (text-meta / text-small / text-body).
 *
 * Exports: BrainMap, BrainMapTarget, BrainMapProps, MapTone
 */
import type { SectionKey } from '../../services/ddd';
import { SECTION_ORDER, type RunTone } from './brainMapModel';

export type BrainMapTarget = 'need' | 'agent' | 'runs' | 'reflect' | 'eval' | 'browse';
/** A part's status dot: healthy, attention, needs you, busy; undefined = no dot. */
export type MapTone = 'ok' | 'attention' | 'needs' | 'busy';

export interface BrainMapProps {
  version: string;
  counts: Record<SectionKey, string>;
  runs: { id: string; title: string; tone: RunTone }[];
  lanes: { chat: number; worker: number; hive: number };
  needs: number;
  reflect: { auto: number; pending: number };
  /** one line under the Project Agent and Eval titles (status / headline number) */
  agentLine?: string;
  evalLine?: string;
  /** status dots per part (section keys, plus agent / conductor / reflect) */
  tones?: Partial<Record<SectionKey | 'agent' | 'conductor' | 'reflect', MapTone>>;
  onOpen: (t: BrainMapTarget, section?: SectionKey) => void;
}

const W = 720, H = 600;
const C = { x: 360, y: 246 };
const HR = 50;
const HD = Math.sqrt(3) * HR + 6;
const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};
const P = {
  agent: { x: 92, y: 246 }, conductor: { x: 360, y: 500 }, evo: { x: 622, y: 412 },
  need: { x: 70, y: 56 }, eval: { x: 620, y: 56 },
};
const SRC = ['Chat tab', 'Slack', 'Schedule'];

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

const RUN_FILL: Record<RunTone, string> = {
  running: 'fill-[var(--color-primary)]',
  needs: 'fill-[var(--color-status-error)]',
  landed: 'fill-[var(--color-status-success)]',
  failed: 'fill-[var(--color-status-warning)]',
  stopped: 'fill-[var(--color-text-muted)]',
};
const DOT_FILL: Record<MapTone, string> = {
  ok: 'fill-[var(--color-status-success)]',
  attention: 'fill-[var(--color-status-warning)]',
  needs: 'fill-[var(--color-status-error)]',
  busy: 'fill-[var(--color-primary)]',
};
const LINE = 'stroke-[var(--color-text-muted)] fill-none';
const SUB = 'text-small fill-[var(--color-text-muted)]';
const TITLE = 'text-body font-semibold fill-[var(--color-text)]';

function Dot({ x, y, tone }: { x: number; y: number; tone?: MapTone }) {
  if (!tone) return null;
  return <circle cx={x} cy={y} r={4.5} className={DOT_FILL[tone]} data-tone={tone} />;
}

/** An arrow with its meaning in a pill beside the line (never on top of it). */
function Arrow({ a, b, label, dashed, lx, ly }: {
  a: { x: number; y: number }; b: { x: number; y: number }; label?: string; dashed?: boolean; lx?: number; ly?: number;
}) {
  const x = lx ?? (a.x + b.x) / 2, y = ly ?? (a.y + b.y) / 2;
  const w = label ? label.length * 6.4 + 18 : 0;
  return (
    <g>
      <path d={`M${a.x},${a.y} L${b.x},${b.y}`} className={LINE} strokeWidth={1.4}
        strokeDasharray={dashed ? '5 4' : undefined} markerEnd="url(#bm-arrow)" />
      {label && (
        <g>
          <rect x={x - w / 2} y={y - 10} width={w} height={20} rx={10}
            className="fill-[var(--color-card)] stroke-[var(--color-border)]" />
          <text x={x} y={y + 4} textAnchor="middle" className="text-meta fill-[var(--color-text-muted)]">{label}</text>
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

export function BrainMap({ version, counts, runs, lanes, needs, reflect, agentLine, evalLine, tones = {}, onOpen }: BrainMapProps) {
  const d = secPos('delivery'), k = secPos('knowledge'), g = secPos('gates');
  const box = 'fill-[var(--color-card)] stroke-[var(--color-border)] group-hover:stroke-[var(--color-primary)] group-focus:stroke-[var(--color-primary)]';
  const shown = runs.slice(0, 12);
  const cTop = P.conductor.y - 50;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Brain map" data-testid="brain-map"
      className="block mx-auto max-w-[820px]">
      <defs>
        <marker id="bm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0L10,5L0,10z" className="fill-[var(--color-text-muted)]" />
        </marker>
      </defs>

      {/* faint Eval reads: it scores the Brain and the Runs, offline */}
      <path d={`M${P.eval.x - 20},${P.eval.y + 26} L${C.x + 40},${C.y - HD - 34}`} className="stroke-[var(--color-border)] fill-none" strokeDasharray="2 5" />
      <path d={`M${P.eval.x},${P.eval.y + 26} L${P.evo.x},${P.evo.y - 58}`} className="stroke-[var(--color-border)] fill-none" strokeDasharray="2 5" />

      <Arrow a={{ x: P.agent.x + 72, y: P.agent.y }} b={{ x: d.x - HR + 4, y: d.y }} label="reads" lx={(P.agent.x + 72 + d.x - HR) / 2} ly={P.agent.y - 16} />
      <Arrow a={{ x: P.agent.x, y: P.agent.y + 38 }} b={{ x: P.conductor.x - 132, y: cTop + 6 }} label="drafts, you submit" lx={150} ly={372} />
      <Arrow a={{ x: C.x, y: C.y + HD * 0.87 + HR - 2 }} b={{ x: C.x, y: cTop }} label="each Run uses this version" lx={C.x} ly={(C.y + HD * 0.87 + HR + cTop) / 2} />
      <Arrow a={{ x: P.conductor.x + 132, y: P.conductor.y - 4 }} b={{ x: P.evo.x - 58, y: P.evo.y + 14 }} label="REPORT" lx={540} ly={474} />
      <Arrow a={{ x: P.evo.x - 6, y: P.evo.y - 58 }} b={toward(k, P.evo, HR * 0.92)} label="writes knowledge" lx={574} ly={310} />
      <Arrow a={{ x: P.evo.x - 40, y: P.evo.y - 42 }} b={toward(g, P.evo, HR * 0.92)} label="writes rules after your OK" dashed lx={556} ly={344} />
      {SRC.map((_, i) => (
        <Arrow key={i} a={{ x: 112, y: 446 + i * 44 }} b={{ x: P.conductor.x - 132, y: P.conductor.y - 6 + i * 14 }} />
      ))}

      {/* the DDD flower */}
      <Part label="Brain: open its knowledge" onClick={() => onOpen('browse')} testid="bm-brain">
        <polygon points={hex(C.x, C.y, HR)} className="fill-[color-mix(in_srgb,var(--color-primary)_14%,transparent)] stroke-[var(--color-primary)]" strokeWidth={2} />
        <text x={C.x} y={C.y - 3} textAnchor="middle" className="text-heading font-semibold fill-[var(--color-text)]">Brain</text>
        <text x={C.x} y={C.y + 15} textAnchor="middle" className="text-small fill-[var(--color-primary)]">{version}</text>
      </Part>
      {SECTION_ORDER.map(({ key, label }) => {
        const p = secPos(key);
        return (
          <Part key={key} label={`${label}: ${counts[key]}`} onClick={() => onOpen('browse', key)} testid={`bm-sec-${key}`}>
            <polygon points={hex(p.x, p.y, HR)} className={box} strokeWidth={1.4} />
            <Dot x={p.x} y={p.y - 26} tone={tones[key]} />
            <text x={p.x} y={p.y + 2} textAnchor="middle" className={TITLE}>{label}</text>
            <text x={p.x} y={p.y + 19} textAnchor="middle" className={SUB}>{counts[key]}</text>
          </Part>
        );
      })}

      {/* Project Agent + sources */}
      <Part label="Project Agent" onClick={() => onOpen('agent')} testid="bm-agent">
        <rect x={P.agent.x - 72} y={P.agent.y - 38} width={144} height={76} rx={10} className={box} />
        <Dot x={P.agent.x - 58} y={P.agent.y - 24} tone={tones.agent} />
        <text x={P.agent.x} y={P.agent.y - 12} textAnchor="middle" className={TITLE}>Project Agent</text>
        <text x={P.agent.x} y={P.agent.y + 6} textAnchor="middle" className={SUB}>delivers with you</text>
        {agentLine && <text x={P.agent.x} y={P.agent.y + 24} textAnchor="middle" className={SUB}>{agentLine}</text>}
      </Part>
      {SRC.map((s, i) => (
        <Part key={s} label={`Source: ${s}`} onClick={() => onOpen('agent')} testid={`bm-src-${i}`}>
          <rect x={30} y={432 + i * 44} width={82} height={28} rx={14} className={box} />
          <text x={71} y={450 + i * 44} textAnchor="middle" className="text-small fill-[var(--color-text)]">{s}</text>
        </Part>
      ))}

      {/* the one Conductor + this brain's Runs */}
      <Part label="Run Conductor" onClick={() => onOpen('runs')} testid="bm-conductor">
        <rect x={P.conductor.x - 132} y={cTop} width={264} height={104} rx={10} className={box} />
        <Dot x={P.conductor.x - 118} y={cTop + 14} tone={tones.conductor} />
        <text x={P.conductor.x} y={cTop + 22} textAnchor="middle" className={TITLE}>Run Conductor</text>
        <text x={P.conductor.x} y={cTop + 40} textAnchor="middle" className={SUB}>
          Chat tab {lanes.chat} · Local Worker {lanes.worker}/1 · Hive {lanes.hive}/2
        </text>
        {shown.map((r, i) => (
          <circle key={r.id} cx={P.conductor.x - 99 + i * 18} cy={cTop + 70} r={6.5} className={RUN_FILL[r.tone]} data-testid={`bm-run-${r.id}`}>
            <title>{r.title}</title>
          </circle>
        ))}
        {shown.length === 0 && (
          <text x={P.conductor.x} y={cTop + 74} textAnchor="middle" className={SUB}>no Runs in 30 days</text>
        )}
      </Part>

      {/* Reflect / Evolution */}
      <Part label="Reflect and Evolution" onClick={() => onOpen('reflect')} testid="bm-reflect">
        <circle cx={P.evo.x} cy={P.evo.y} r={66} className="fill-[color-mix(in_srgb,var(--color-zone-cognitive)_16%,transparent)] stroke-[var(--color-zone-cognitive)] group-hover:stroke-[var(--color-primary)]" />
        <Dot x={P.evo.x} y={P.evo.y - 40} tone={tones.reflect} />
        <text x={P.evo.x} y={P.evo.y - 14} textAnchor="middle" className={TITLE}>Reflect</text>
        <text x={P.evo.x} y={P.evo.y + 4} textAnchor="middle" className={SUB}>learns from Runs</text>
        <text x={P.evo.x} y={P.evo.y + 22} textAnchor="middle"
          className={reflect.pending ? 'text-small fill-[var(--color-status-error)]' : SUB}>{reflect.auto} auto · {reflect.pending} to review</text>
      </Part>

      {/* You, Eval */}
      <Part label={`You: ${needs} need you`} onClick={() => onOpen('need')} testid="bm-you">
        <circle cx={P.need.x} cy={P.need.y} r={28}
          className={needs ? 'fill-[color-mix(in_srgb,var(--color-status-error)_14%,transparent)] stroke-[var(--color-status-error)]'
            : 'fill-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] stroke-[var(--color-status-success)]'} />
        <text x={P.need.x} y={P.need.y + 6} textAnchor="middle"
          className={`text-title font-semibold ${needs ? 'fill-[var(--color-status-error)]' : 'fill-[var(--color-status-success)]'}`}>{needs}</text>
        <text x={P.need.x + 40} y={P.need.y - 3} className={TITLE}>You</text>
        <text x={P.need.x + 40} y={P.need.y + 15} className={SUB}>review &amp; judge</text>
      </Part>
      <Part label="Eval" onClick={() => onOpen('eval')} testid="bm-eval">
        <rect x={P.eval.x - 80} y={P.eval.y - 26} width={160} height={52} rx={10} className={box} />
        <text x={P.eval.x} y={P.eval.y - 5} textAnchor="middle" className={TITLE}>Eval</text>
        <text x={P.eval.x} y={P.eval.y + 13} textAnchor="middle" className={SUB}>{evalLine ?? 'reads offline · 5 dims'}</text>
      </Part>
    </svg>
  );
}
