/**
 * BrainMap — the Brain map picture: the six DDD sections as a hex flower around the
 * Brain, with You (review & judge), the Project Agent, the request sources, the one
 * Run Conductor, Reflect / Evolution and Eval laid out around it, and arrows that
 * say who does what ("drafts → you submit", "auto", "after your OK").
 *
 * Pure presentation: every number arrives in props (see brainMapModel). Each part
 * is a button-like <g> that calls `onOpen(target)`. Theme variables only; type uses
 * the nav scale classes (text-meta / text-small / text-body).
 *
 * Exports: BrainMap, BrainMapTarget, BrainMapProps
 */
import type { SectionKey } from '../../services/ddd';
import { SECTION_ORDER, type RunTone } from './brainMapModel';

export type BrainMapTarget = 'need' | 'agent' | 'runs' | 'reflect' | 'eval' | 'browse';

export interface BrainMapProps {
  version: string;
  counts: Record<SectionKey, string>;
  runs: { id: string; title: string; tone: RunTone }[];
  lanes: { chat: number; worker: number; hive: number };
  needs: number;
  reflect: { auto: number; pending: number };
  onOpen: (t: BrainMapTarget, section?: SectionKey) => void;
}

const C = { x: 320, y: 222 };
const HR = 40;
const HD = Math.sqrt(3) * HR + 6;
const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};
const P = {
  agent: { x: 76, y: 222 }, conductor: { x: 320, y: 450 }, evo: { x: 566, y: 372 },
  need: { x: 84, y: 50 }, eval: { x: 556, y: 50 },
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

const TONE: Record<RunTone, string> = {
  running: 'fill-[var(--color-primary)]',
  needs: 'fill-[var(--color-status-error)]',
  landed: 'fill-[var(--color-status-success)]',
  failed: 'fill-[var(--color-status-warning)]',
  stopped: 'fill-[var(--color-text-muted)]',
};
const LINE = 'stroke-[var(--color-text-muted)] fill-none';
const LABEL = 'text-meta fill-[var(--color-text-muted)]';

function Arrow({ a, b, label, dashed, lx, ly }: {
  a: { x: number; y: number }; b: { x: number; y: number }; label?: string; dashed?: boolean; lx?: number; ly?: number;
}) {
  return (
    <g>
      <path d={`M${a.x},${a.y} L${b.x},${b.y}`} className={LINE} strokeWidth={1.3}
        strokeDasharray={dashed ? '4 4' : undefined} markerEnd="url(#bm-arrow)" />
      {label && (
        <text x={lx ?? (a.x + b.x) / 2} y={ly ?? (a.y + b.y) / 2 - 5} textAnchor="middle" className={LABEL}>{label}</text>
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

export function BrainMap({ version, counts, runs, lanes, needs, reflect, onOpen }: BrainMapProps) {
  const d = secPos('delivery'), k = secPos('knowledge'), g = secPos('gates');
  const box = 'fill-[var(--color-card)] stroke-[var(--color-border)] group-hover:stroke-[var(--color-primary)] group-focus:stroke-[var(--color-primary)]';
  const shown = runs.slice(0, 12);
  return (
    <svg viewBox="0 0 640 560" width="100%" role="img" aria-label="Brain map" data-testid="brain-map">
      <defs>
        <marker id="bm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0L10,5L0,10z" className="fill-[var(--color-text-muted)]" />
        </marker>
      </defs>

      <Arrow a={{ x: P.agent.x + 62, y: P.agent.y }} b={{ x: d.x - HR + 4, y: d.y }} label="reads" />
      <Arrow a={{ x: P.agent.x, y: P.agent.y + 30 }} b={{ x: P.conductor.x - 122, y: P.conductor.y - 22 }} label="drafts → you submit" lx={110} ly={345} />
      <Arrow a={{ x: C.x, y: C.y + HD * 0.87 + HR - 2 }} b={{ x: C.x, y: P.conductor.y - 46 }} label="Brain pinned per Run" lx={C.x + 68} ly={392} />
      <Arrow a={{ x: P.conductor.x + 122, y: P.conductor.y - 6 }} b={{ x: P.evo.x - 46, y: P.evo.y + 12 }} label="REPORT" />
      <Arrow a={{ x: P.evo.x - 10, y: P.evo.y - 44 }} b={toward(k, P.evo, HR * 0.9)} label="auto" lx={522} ly={262} />
      <Arrow a={{ x: P.evo.x - 30, y: P.evo.y - 34 }} b={toward(g, P.evo, HR * 0.9)} label="after your OK" dashed lx={468} ly={318} />
      {SRC.map((_, i) => (
        <Arrow key={i} a={{ x: 112, y: 392 + i * 46 }} b={{ x: P.conductor.x - 122, y: P.conductor.y - 4 + i * 12 }} />
      ))}
      <path d={`M${P.eval.x},${P.eval.y + 20} L${C.x + 30},${C.y - HD - 20}`} className="stroke-[var(--color-border)] fill-none" strokeDasharray="2 5" />
      <path d={`M${P.eval.x},${P.eval.y + 20} L${P.evo.x},${P.evo.y - 46}`} className="stroke-[var(--color-border)] fill-none" strokeDasharray="2 5" />

      {/* the DDD flower */}
      <Part label="Brain: open the DDD" onClick={() => onOpen('browse')} testid="bm-brain">
        <polygon points={hex(C.x, C.y, HR)} className="fill-[color-mix(in_srgb,var(--color-primary)_14%,transparent)] stroke-[var(--color-primary)]" strokeWidth={2} />
        <text x={C.x} y={C.y - 4} textAnchor="middle" className="text-body font-semibold fill-[var(--color-text)]">Brain</text>
        <text x={C.x} y={C.y + 12} textAnchor="middle" className="text-meta fill-[var(--color-primary)]">{version}</text>
      </Part>
      {SECTION_ORDER.map(({ key, label }) => {
        const p = secPos(key);
        return (
          <Part key={key} label={`${label}: ${counts[key]}`} onClick={() => onOpen('browse', key)} testid={`bm-sec-${key}`}>
            <polygon points={hex(p.x, p.y, HR)} className={box} strokeWidth={1.3} />
            <text x={p.x} y={p.y - 3} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">{label}</text>
            <text x={p.x} y={p.y + 12} textAnchor="middle" className="text-meta fill-[var(--color-text-muted)]">{counts[key]}</text>
          </Part>
        );
      })}

      {/* Project Agent + sources */}
      <Part label="Project Agent" onClick={() => onOpen('agent')} testid="bm-agent">
        <rect x={P.agent.x - 62} y={P.agent.y - 30} width={124} height={60} rx={10} className={box} />
        <text x={P.agent.x} y={P.agent.y - 6} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Project Agent</text>
        <text x={P.agent.x} y={P.agent.y + 11} textAnchor="middle" className={LABEL}>delivers with you</text>
      </Part>
      {SRC.map((s, i) => (
        <Part key={s} label={`Source: ${s}`} onClick={() => onOpen('agent')} testid={`bm-src-${i}`}>
          <rect x={50} y={381 + i * 46} width={62} height={22} rx={11} className={box} />
          <text x={81} y={396 + i * 46} textAnchor="middle" className="text-meta fill-[var(--color-text)]">{s}</text>
        </Part>
      ))}

      {/* the one Conductor + this brain's Runs */}
      <Part label="Run Conductor" onClick={() => onOpen('runs')} testid="bm-conductor">
        <rect x={P.conductor.x - 122} y={P.conductor.y - 44} width={244} height={98} rx={10} className={box} />
        <text x={P.conductor.x} y={P.conductor.y - 24} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Run Conductor</text>
        <text x={P.conductor.x} y={P.conductor.y - 8} textAnchor="middle" className={LABEL}>
          Chat tab {lanes.chat} · Local Worker {lanes.worker}/1 · Hive {lanes.hive}/2
        </text>
        {shown.map((r, i) => (
          <circle key={r.id} cx={P.conductor.x - 99 + i * 18} cy={P.conductor.y + 20} r={6.5} className={TONE[r.tone]} data-testid={`bm-run-${r.id}`}>
            <title>{r.title}</title>
          </circle>
        ))}
        {shown.length === 0 && (
          <text x={P.conductor.x} y={P.conductor.y + 24} textAnchor="middle" className={LABEL}>no Runs in 30 days</text>
        )}
      </Part>

      {/* Reflect / Evolution */}
      <Part label="Reflect and Evolution" onClick={() => onOpen('reflect')} testid="bm-reflect">
        <circle cx={P.evo.x} cy={P.evo.y} r={52} className="fill-[color-mix(in_srgb,var(--color-zone-cognitive)_16%,transparent)] stroke-[var(--color-zone-cognitive)] group-hover:stroke-[var(--color-primary)]" />
        <text x={P.evo.x} y={P.evo.y - 8} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Reflect</text>
        <text x={P.evo.x} y={P.evo.y + 7} textAnchor="middle" className="text-meta fill-[var(--color-text)]">Evolution</text>
        <text x={P.evo.x} y={P.evo.y + 22} textAnchor="middle" className={LABEL}>{reflect.auto} auto · {reflect.pending} review</text>
      </Part>

      {/* You, Eval */}
      <Part label={`You: ${needs} need you`} onClick={() => onOpen('need')} testid="bm-you">
        <circle cx={P.need.x} cy={P.need.y} r={24}
          className={needs ? 'fill-[color-mix(in_srgb,var(--color-status-error)_14%,transparent)] stroke-[var(--color-status-error)] motion-safe:animate-pulse'
            : 'fill-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] stroke-[var(--color-status-success)]'} />
        <text x={P.need.x} y={P.need.y + 5} textAnchor="middle"
          className={`text-heading font-semibold ${needs ? 'fill-[var(--color-status-error)]' : 'fill-[var(--color-status-success)]'}`}>{needs}</text>
        <text x={P.need.x + 34} y={P.need.y - 2} className="text-small font-semibold fill-[var(--color-text)]">You</text>
        <text x={P.need.x + 34} y={P.need.y + 13} className={LABEL}>review &amp; judge</text>
      </Part>
      <Part label="Eval" onClick={() => onOpen('eval')} testid="bm-eval">
        <rect x={P.eval.x - 58} y={P.eval.y - 20} width={116} height={40} rx={10} className={box} />
        <text x={P.eval.x} y={P.eval.y - 3} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Eval</text>
        <text x={P.eval.x} y={P.eval.y + 12} textAnchor="middle" className={LABEL}>reads offline · 5 dims</text>
      </Part>
    </svg>
  );
}
