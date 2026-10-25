/**
 * BrainHubMap — the Hub-level picture above the Brain card wall: every Brain as a
 * small six-petal flower (a petal is filled when that DDD section exists), all of
 * them feeding the ONE Run Conductor shared by the machine, with You (the open
 * items across all brains) and Eval. Clicking a flower opens that Brain's map.
 *
 * Reads only the gallery summaries already fetched (zero extra calls).
 * Exports: BrainHubMap
 */
import type { BrainSummary, SectionKey } from '../../services/ddd';
import { SECTION_ORDER, hubSummary } from './brainMapModel';

const W = 900;
const PER_ROW = 6;
const R = 15;           // petal hex radius
const D = Math.sqrt(3) * R + 2;
const ANG: Record<SectionKey, number> = {
  identity: -120, knowledge: -60, gates: 0, capabilities: 60, refresher: 120, delivery: 180,
};

function hex(x: number, y: number, r: number) {
  const p: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    p.push(`${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`);
  }
  return p.join(' ');
}

export function BrainHubMap({ brains, selfName, onOpen }: {
  brains: BrainSummary[]; selfName?: string; onOpen: (name: string) => void;
}) {
  const sum = hubSummary(brains);
  const rows = Math.max(1, Math.ceil(brains.length / PER_ROW));
  const rowH = 130;
  const top = 20;
  const condY = top + rows * rowH + 30;
  const H = condY + 40;
  const left = 190, right = W - 80;
  const step = brains.length > 1 ? (right - left) / (Math.min(PER_ROW, brains.length) - 1 || 1) : 0;
  const pos = (i: number) => {
    const row = Math.floor(i / PER_ROW), col = i % PER_ROW;
    const n = Math.min(PER_ROW, brains.length - row * PER_ROW);
    const x = n === 1 ? (left + right) / 2 : left + col * step + ((PER_ROW - n) * step) / 2;
    return { x, y: top + 50 + row * rowH };
  };
  const condX = (left + right) / 2;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Brain Hub map" data-testid="brainhub-map">
      <defs>
        <marker id="bhm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0L10,5L0,10z" className="fill-[var(--color-text-muted)]" />
        </marker>
      </defs>

      {/* You */}
      <g>
        <circle cx={62} cy={70} r={26}
          className={sum.needsYou ? 'fill-[color-mix(in_srgb,var(--color-status-error)_14%,transparent)] stroke-[var(--color-status-error)]'
            : 'fill-[color-mix(in_srgb,var(--color-status-success)_14%,transparent)] stroke-[var(--color-status-success)]'} />
        <text x={62} y={76} textAnchor="middle"
          className={`text-heading font-semibold ${sum.needsYou ? 'fill-[var(--color-status-error)]' : 'fill-[var(--color-status-success)]'}`}>{sum.needsYou}</text>
        <text x={62} y={112} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">You</text>
        <text x={62} y={126} textAnchor="middle" className="text-meta fill-[var(--color-text-muted)]">review &amp; judge</text>
      </g>

      {/* Eval */}
      <g>
        <rect x={W - 150} y={condY - 22} width={110} height={50} rx={10} className="fill-[var(--color-card)] stroke-[var(--color-border)]" />
        <text x={W - 95} y={condY - 2} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Eval</text>
        <text x={W - 95} y={condY + 14} textAnchor="middle" className="text-meta fill-[var(--color-text-muted)]">reads offline</text>
      </g>

      {/* arrows into the one Conductor */}
      {brains.map((b, i) => {
        const p = pos(i);
        return (
          <path key={'a' + b.name} d={`M${p.x},${p.y + D + R + 18} L${condX + (p.x - condX) * 0.55},${condY - 22}`}
            className="stroke-[var(--color-border)] fill-none" strokeWidth={1.2} markerEnd="url(#bhm-arrow)" />
        );
      })}

      {/* the brains */}
      {brains.map((b, i) => {
        const p = pos(i);
        const self = b.name === selfName;
        return (
          <g key={b.name} role="button" tabIndex={0} aria-label={`Open ${b.name}`} data-testid={`brainhub-map-${b.name}`}
            className="cursor-pointer group outline-none" onClick={() => onOpen(b.name)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(b.name); } }}>
            <rect x={p.x - 62} y={p.y - 48} width={124} height={118} rx={10}
              className="fill-transparent stroke-transparent group-hover:stroke-[var(--color-primary)] group-focus:stroke-[var(--color-primary)]" />
            {SECTION_ORDER.map(({ key }) => {
              const a = (ANG[key] * Math.PI) / 180;
              const on = b.sectionsPresent[key];
              return (
                <polygon key={key} points={hex(p.x + D * Math.cos(a), p.y + D * Math.sin(a), R)}
                  className={on ? 'fill-[color-mix(in_srgb,var(--color-primary)_22%,transparent)] stroke-[var(--color-primary)]'
                    : 'fill-[var(--color-card)] stroke-[var(--color-border)]'} strokeWidth={1}>
                  <title>{`${key}: ${on ? 'present' : 'missing'}`}</title>
                </polygon>
              );
            })}
            <polygon points={hex(p.x, p.y, R)}
              className={self ? 'fill-[var(--color-zone-cognitive)] stroke-[var(--color-zone-cognitive)]' : 'fill-[var(--color-primary)] stroke-[var(--color-primary)]'} />
            {b.health.liveRun && (
              <circle cx={p.x + 40} cy={p.y - 34} r={5} className="fill-[var(--color-primary)] motion-safe:animate-pulse"><title>a Run is in progress</title></circle>
            )}
            {b.health.pending > 0 && (
              <g>
                <circle cx={p.x - 40} cy={p.y - 34} r={9} className="fill-[var(--color-status-error)]" />
                <text x={p.x - 40} y={p.y - 30} textAnchor="middle" className="text-meta font-semibold fill-[var(--color-on-accent)]">{b.health.pending}</text>
              </g>
            )}
            <text x={p.x} y={p.y + D + R + 14} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">{b.name}</text>
            {b.setupState === 'setting-up' && (
              <text x={p.x} y={p.y + D + R + 28} textAnchor="middle" className="text-meta fill-[var(--color-status-warning)]"
                data-testid={`brainhub-map-born-${b.name}`}>being born</text>
            )}
          </g>
        );
      })}

      {/* the one Run Conductor */}
      <g data-testid="brainhub-map-conductor">
        <rect x={condX - 210} y={condY - 22} width={420} height={50} rx={10} className="fill-[var(--color-card)] stroke-[var(--color-border)]" />
        <text x={condX} y={condY - 2} textAnchor="middle" className="text-small font-semibold fill-[var(--color-text)]">Run Conductor · one for this machine</text>
        <text x={condX} y={condY + 14} textAnchor="middle" className="text-meta fill-[var(--color-text-muted)]">
          {sum.live} Brain{sum.live === 1 ? '' : 's'} with a live Run · Chat tab · Local Worker (mock) · Hive (mock)
        </text>
      </g>
    </svg>
  );
}
