/**
 * How loudly each part of a chat turn is drawn (docs/design/2026-10-25-turn-attention-tiers.md).
 *
 * - `Attention`: `act` (the user must do something), `know` (a result to know), `process`
 *   (how the agent got there).
 * - `zoneAttention(zone)`: the tier of a zone, from one table over every `Zone['kind']`
 *   (a new kind without an entry does not compile).
 * - `attnProps(zone)`: the attributes for the zone's existing host node: `data-attn`, and
 *   `data-prose="process"` on the agent's process prose, the hook of the one CSS rule in
 *   index.css that dims it.
 * - `PROCESS_PROSE`: the same hook for prose rendered inside a process zone (a phase's or
 *   a stage's notes).
 */
import { isUnanswered } from './turnView';
import type { Zone } from './turnZones';

export type Attention = 'act' | 'know' | 'process';

type ZoneKind = Zone['kind'];
type Rule<K extends ZoneKind> = Attention | ((z: Extract<Zone, { kind: K }>) => Attention);

const ATTENTION: { [K in ZoneKind]: Rule<K> } = {
  needs: (z) => (isUnanswered(z.block) ? 'act' : 'process'),
  failure: 'know',
  text: (z) => (z.role === 'answer' ? 'know' : 'process'),
  thinking: 'process',
  phase: 'process',
  stage: 'process',
  pipelineStart: 'process',
  stageResult: 'know',
  gate: 'know',
  stopped: 'know',
};

export function zoneAttention(z: Zone): Attention {
  const rule = ATTENTION[z.kind] as Rule<typeof z.kind>;
  return typeof rule === 'function' ? (rule as (z: Zone) => Attention)(z) : rule;
}

export const PROCESS_PROSE = { 'data-prose': 'process' } as const;

export function attnProps(z: Zone): { 'data-attn': Attention; 'data-prose'?: 'process' } {
  const attn = zoneAttention(z);
  return z.kind === 'text' && attn === 'process' ? { 'data-attn': attn, ...PROCESS_PROSE } : { 'data-attn': attn };
}
