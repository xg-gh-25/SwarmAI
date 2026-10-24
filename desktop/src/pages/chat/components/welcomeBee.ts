/**
 * welcomeBee — the Welcome screen bee's flight paths (design welcome-screen-v2.md).
 *
 * Pure geometry, no DOM: a flight is a list of `translate(x, y)` keyframes in the
 * logo's own coordinates (origin = the logo's top-left), so the landed bee moves
 * with the logo on resize. Only `transform` is animated (no CSS offset-path, which
 * older WebKit does not fully support).
 *
 * - The path is a Catmull-Rom curve through waypoints, re-sampled by arc length so
 *   the bee's speed follows the easing, not the waypoint spacing.
 * - The easing starts soft, slows on the approach, holds a short hover just before
 *   touchdown, then settles.
 *
 * @exports LANDED, FLIGHT_MS, LAP_MS, flightKeyframes, landedTransform, ease
 */

export interface Point { x: number; y: number }

/** Where the bee sits on the logo's top-right corner (bee box 26x23, logo 60x60).
 *  index.css `.welcome-bee--landed` uses the same translate — keep them equal. */
export const LANDED: Point = { x: 40, y: -16 };
export const FLIGHT_MS = 3600;
export const LAP_MS = 2600;
const KEYFRAMES = 48;

export const landedTransform = `translate(${LANDED.x}px, ${LANDED.y}px)`;

/** Standard cubic-bezier timing function (x1,y1,x2,y2), solved by bisection. */
function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const bx = (s: number) => 3 * x1 * s * (1 - s) ** 2 + 3 * x2 * s * s * (1 - s) + s ** 3;
  const by = (s: number) => 3 * y1 * s * (1 - s) ** 2 + 3 * y2 * s * s * (1 - s) + s ** 3;
  return (t) => {
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 30; i++) {
      const mid = (lo + hi) / 2;
      if (bx(mid) < t) lo = mid; else hi = mid;
    }
    return by((lo + hi) / 2);
  };
}

const approach = cubicBezier(0.33, 0, 0.2, 1);

/** Time (0..1) → distance along the path (0..1): approach, a short hover, settle. */
export function ease(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  if (t < 0.82) return 0.92 * approach(t / 0.82);
  if (t < 0.9) return 0.92 + 0.01 * ((t - 0.82) / 0.08);
  const u = (t - 0.9) / 0.1;
  return 0.93 + 0.07 * (1 - (1 - u) ** 2);
}

function catmullRom(pts: Point[], samplesPerSeg = 24): Point[] {
  const out: Point[] = [];
  const p = [pts[0], ...pts, pts[pts.length - 1]];
  for (let i = 1; i < p.length - 2; i++) {
    const [a, b, c, d] = [p[i - 1], p[i], p[i + 1], p[i + 2]];
    for (let k = 0; k < samplesPerSeg; k++) {
      const t = k / samplesPerSeg;
      const t2 = t * t;
      const t3 = t2 * t;
      const f = (q0: number, q1: number, q2: number, q3: number) =>
        0.5 * (2 * q1 + (-q0 + q2) * t + (2 * q0 - 5 * q1 + 4 * q2 - q3) * t2 + (-q0 + 3 * q1 - 3 * q2 + q3) * t3);
      out.push({ x: f(a.x, b.x, c.x, d.x), y: f(a.y, b.y, c.y, d.y) });
    }
  }
  out.push(pts[pts.length - 1]);
  return out;
}

/** Waypoints, in logo coordinates. `start` is where the entrance begins (off to
 *  the upper left of the screen); the lap starts and ends on the landing spot. */
function waypoints(kind: 'in' | 'lap', start: Point): Point[] {
  if (kind === 'lap') {
    return [LANDED, { x: 95, y: -50 }, { x: 20, y: -62 }, { x: -50, y: -30 }, { x: -48, y: 40 },
      { x: 25, y: 70 }, { x: 88, y: 20 }, { x: 60, y: -30 }, LANDED];
  }
  return [start, { x: -90, y: 40 }, { x: 17, y: 78 }, { x: 95, y: 10 }, { x: 85, y: -45 },
    { x: 54, y: -32 }, LANDED];
}

/** The keyframes of one flight: `transform` only, evenly spaced in time. */
export function flightKeyframes(kind: 'in' | 'lap', start: Point = { x: -200, y: 40 }): Keyframe[] {
  const pts = catmullRom(waypoints(kind, start));
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
  }
  const total = cum[cum.length - 1] || 1;
  const frames: Keyframe[] = [];
  let j = 1;
  for (let k = 0; k <= KEYFRAMES; k++) {
    const target = ease(k / KEYFRAMES) * total;
    while (j < cum.length - 1 && cum[j] < target) j++;
    const seg = cum[j] - cum[j - 1] || 1;
    const r = Math.min(1, Math.max(0, (target - cum[j - 1]) / seg));
    const x = pts[j - 1].x + (pts[j].x - pts[j - 1].x) * r;
    const y = pts[j - 1].y + (pts[j].y - pts[j - 1].y) * r;
    frames.push({ transform: `translate(${Number(x.toFixed(1))}px, ${Number(y.toFixed(1))}px)`, offset: k / KEYFRAMES });
  }
  frames[frames.length - 1] = { transform: landedTransform, offset: 1 };
  return frames;
}
