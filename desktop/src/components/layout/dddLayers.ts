/**
 * dddLayers.ts — the 3-layer knowledge-ontology model, shared by every surface
 * that renders a brain's cognitive shape (DddCard's Ontology + CompactLayerBar,
 * and the Welcome BrainPulse strip).
 *
 * PURE DATA & LOGIC — MUST NOT import from DddCard or any consumer component
 * (extracted from DddCard.tsx run_fc7078c4 to kill duplication, R25). Its only
 * import is the EntryType union from the ddd service. No back-edges → no cycle.
 *
 * Authoritative mapping: backend MEMORY_SECTIONS[*].layer.
 */
import type { EntryType } from '../../services/ddd';

export type Layer = 'meta' | 'cognitive' | 'operational';

/** 7-type → 3-layer map (authoritative: backend MEMORY_SECTIONS[*].layer).
 *  Internal — only `layerTotals` consumes it; not part of the public surface. */
const LAYER_OF_TYPE: Record<EntryType, Layer> = {
  principle: 'meta', correction: 'meta',
  decision: 'cognitive', model: 'cognitive',
  guideline: 'operational', pitfall: 'operational', process: 'operational',
};

/** Layer display + which types sit under each (fixed cognitive order, not by count). */
export const LAYERS: { key: Layer; label: string; color: string; types: EntryType[] }[] = [
  { key: 'meta', label: 'Meta-cognitive', color: '#a371f7', types: ['principle', 'correction'] },
  { key: 'cognitive', label: 'Cognitive', color: '#58a6ff', types: ['decision', 'model'] },
  { key: 'operational', label: 'Operational', color: '#3fb950', types: ['guideline', 'pitfall', 'process'] },
];

/** Sum a 7-type histogram into its 3 layer totals. */
export function layerTotals(tc: Record<EntryType, number>): Record<Layer, number> {
  const t: Record<Layer, number> = { meta: 0, cognitive: 0, operational: 0 };
  for (const [k, n] of Object.entries(tc) as [EntryType, number][]) t[LAYER_OF_TYPE[k]] += n;
  return t;
}
