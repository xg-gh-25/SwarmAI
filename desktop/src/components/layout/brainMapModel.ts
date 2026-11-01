/**
 * brainMapModel — pure builders for the Brain map (Brain Hub detail "Map" view and
 * the Hub map above the card wall). No React, no fetch: every number comes from a
 * record the backend already produced (GET /ddd/brains[/{name}]). A Brain holds
 * knowledge only: Runs, their waits and their autonomy live in Pipelines and Swarm Out
 * (design docs/design/2026-11-01-brain-eval-and-runs.md §2), so nothing here reads a Run.
 *
 * Exports:
 *   SECTION_ORDER        the six DDD sections, map order
 *   sectionCounts        per-section count line from a BrainDetail
 *   needsYouItems        one queue: what waits on you in one Brain (needsCount: its total)
 *   brainPending         a Brain's own waiting items (Need You's count without its Runs)
 *   hubSummary           Brain Hub home totals across all brains
 *   hubNeeds             Brain Hub home: everything waiting on you, all brains
 *   pendingSplit         health.pending split by source (Runs / escalations / jobs)
 *   brainSignal          the one signal a Hub card shows, and its sort rank
 */
import type { BrainDetail, BrainSummary, GeneralBrain, SectionKey } from '../../services/ddd';

export const SECTION_ORDER: { key: SectionKey; label: string }[] = [
  { key: 'identity', label: 'Identity' },
  { key: 'knowledge', label: 'Knowledge' },
  { key: 'gates', label: 'Gates' },
  { key: 'capabilities', label: 'Capabilities' },
  { key: 'refresher', label: 'Refresher' },
  { key: 'delivery', label: 'Delivery' },
];

/** The short count line under each hex. */
export function sectionCounts(detail: BrainDetail | null): Record<SectionKey, string> {
  const out = {} as Record<SectionKey, string>;
  for (const { key } of SECTION_ORDER) {
    const s = detail?.sections.find((x) => x.key === key);
    if (!s) { out[key] = '--'; continue; }
    if (key === 'knowledge') out[key] = `${s.entries.length} entries`;
    else if (s.members.length === 0) out[key] = s.completeNotBroken ? 'none (ok)' : 'empty';
    else out[key] = `${s.members.length} file${s.members.length === 1 ? '' : 's'}`;
  }
  return out;
}

export interface NeedItem {
  kind: 'other';
  id: string;
  title: string;
  detail: string;
  /** how many of the Brain's waiting items this row stands for (an escalations row: N) */
  count: number;
}

/** What waits for a person in this Brain itself, one row per kind the attention channel
 *  holds: escalations, failing jobs, newly read repositories, anything else. A Run waiting
 *  on you is not the Brain's: it is in Need You and in Pipelines / Swarm Out. The rows'
 *  counts add up to the Brain header's count (``brainPending``), so the header, the map's
 *  You and this list agree. The Project Agent never waits on the person: the agent keeps
 *  it current and fixes its problems (design 2026-11-01-project-agent-simple). */
export function needsYouItems(bySource?: Record<string, number>): NeedItem[] {
  const items: NeedItem[] = [];
  if (!bySource) return items;
  const split = pendingSplit({ health: { pending: 0 } as BrainSummary['health'], pendingBySource: bySource });
  const rest: [string, number, string, string][] = [
    ['escalations', split.escalations, `${plural(split.escalations, 'escalation')} to decide`, 'open Need You to answer'],
    ['jobs', split.jobs, plural(split.jobs, 'failing job'), 'open Need You to triage'],
    ['repos', split.repos, `${plural(split.repos, 'repository', 'repositories')} newly read`, 'the Brain started reading it; open Need You to see it'],
    ['other', split.other, plural(split.other, 'other item'), 'open Need You'],
  ];
  for (const [id, n, title, detail] of rest) if (n > 0) items.push({ kind: 'other', id, title, detail, count: n });
  return items;
}

/** How many waiting items a needs list stands for. */
export const needsCount = (items: NeedItem[]): number => items.reduce((s, i) => s + i.count, 0);

export interface HubSummary {
  brains: number;
  /** items waiting on a person in the Brains themselves (``brainPending``) */
  needsYou: number;
  /** brains whose setup is recorded as setting-up */
  born: number;
  /** entries CDF wrote in the last 7 days (each Brain's learnedWeek, the General Brain's
   *  included), summed; null = no Brain reported it (an older daemon) */
  learned: number | null;
  learnedBrains: number;
}

/** How much an open item asks of the person, most first: an escalation is
 *  blocked on them; a failing job waits for a review; a Brain being born is setup; a
 *  repository a Brain started reading is a
 *  notice. Only urgent and review carry a warm color on the Hub. */
export type Severity = 'urgent' | 'review' | 'setup' | 'notice';
export const SEVERITY_ORDER: Severity[] = ['urgent', 'review', 'setup', 'notice'];

export interface HubNeed {
  brain: string;
  /** escalations / jobs / repos: counted per brain */
  kind: 'escalations' | 'jobs' | 'repos';
  id: string;
  text: string;
  /** how many items this row stands for (a per-brain count, or 1) */
  count: number;
  severity: Severity;
  /** the built-in General Brain (opened by its own view, never by the name) */
  builtin?: boolean;
}

/** health.pending split by source (the attention channel's own split). An old daemon
 *  without the split counts everything as one unnamed bucket. */
export function pendingSplit(b: Pick<BrainSummary, 'health' | 'pendingBySource'>) {
  const by = b.pendingBySource;
  if (!by) return { runs: 0, escalations: 0, jobs: 0, repos: 0, other: b.health?.pending ?? 0 };
  const runs = by.paused_run ?? 0, escalations = by.escalation ?? 0, jobs = by.job ?? 0;
  const repos = by.local_root ?? 0;   // a repository it started reading: a notice, not a task
  const total = Object.values(by).reduce((x, y) => x + y, 0);
  return { runs, escalations, jobs, repos, other: total - runs - escalations - jobs - repos };
}

/** A Brain's own waiting items: the attention channel's count without the Runs waiting
 *  on you (those are Pipelines' and Swarm Out's). */
export function brainPending(b: Pick<BrainSummary, 'health' | 'pendingBySource'>): number {
  const s = pendingSplit(b);
  return s.escalations + s.jobs + s.repos + s.other;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Every item across all brains that waits for a person in the Brain itself:
 *  escalations, then failing jobs and other items, then newly read repositories. Each
 *  is counted once, from the attention channel's count per source. */
export function hubNeeds(brains: BrainSummary[], _general?: GeneralBrain | null): HubNeed[] {
  const out: HubNeed[] = [];
  for (const b of brains) {
    const split = pendingSplit(b);
    const row = (kind: HubNeed['kind'], count: number, severity: Severity, text: string) =>
      out.push({ brain: b.name, kind, id: `${b.name}-${kind}`, text, count, severity });
    if (split.escalations) row('escalations', split.escalations, 'urgent', `${plural(split.escalations, 'escalation')} to decide`);
    if (split.jobs + split.other) row('jobs', split.jobs + split.other, 'review', `${plural(split.jobs + split.other, 'other item')} in Need You`);
    if (split.repos) row('repos', split.repos, 'notice', `now reads ${plural(split.repos, 'new repository', 'new repositories')}`);
  }
  const order: HubNeed['kind'][] = ['escalations', 'jobs', 'repos'];
  out.sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind));
  return out;
}

export function hubSummary(brains: BrainSummary[], general?: GeneralBrain | null): HubSummary {
  const reported = [...brains, ...(general ? [general] : [])].filter((b) => typeof b.learnedWeek === 'number');
  return {
    brains: brains.length,
    needsYou: brains.reduce((s, b) => s + brainPending(b), 0),
    born: brains.filter((b) => b.setupState === 'setting-up').length,
    learned: reported.length ? reported.reduce((s, b) => s + (b.learnedWeek ?? 0), 0) : null,
    learnedBrains: reported.filter((b) => (b.learnedWeek ?? 0) > 0).length,
  };
}

/** A card's status: an open item's severity, `calm` when nothing waits (the card then
 *  shows no status at all). A Run's state is not the Brain's (Pipelines / Swarm Out). */
export type BrainSignalTone = Severity | 'calm';
export interface BrainSignal { tone: BrainSignalTone; text: string; rank: number }

/** The ONE most pressing thing to say about a brain on its Hub card: what blocks on the
 *  person, then what waits for a review, setup, a notice. */
export function brainSignal(b: BrainSummary): BrainSignal {
  const split = pendingSplit(b);
  if (split.escalations) return { tone: 'urgent', rank: 0, text: `${plural(split.escalations, 'escalation')} to decide` };
  if (split.jobs + split.other) return { tone: 'review', rank: 1, text: `${plural(split.jobs + split.other, 'item')} in Need You` };
  if (b.projectAgentStatus === 'invalid') return { tone: 'review', rank: 1, text: 'Project Agent has problems' };
  if (b.setupState === 'setting-up') return { tone: 'setup', rank: 3, text: bornText(b.setupProgress) };
  if (split.repos) return { tone: 'notice', rank: 4, text: `Reads ${plural(split.repos, 'new repository', 'new repositories')}` };
  return { tone: 'calm', rank: 5, text: '' };
}

/** What each P6 check asks for, as the next thing to do (verify_ddd_complete.py names).
 *  A check not listed falls back to its own name without the number. */
const NEXT_STEP: Record<string, string> = {
  'Identity/Manifest': 'repair the manifest',
  'Knowledge': 'create the four docs',
  'Knowledge/structure': 'add the missing sections',
  'Knowledge/content': 'write the first knowledge',
  'Knowledge/format': 'fix the entry format',
  'Knowledge/references': 'fix the broken references',
  'Gates': 'add the gates folder',
  'Capabilities': 'add the skills',
  'Delivery Contract': 'bind the repo',
  'Project Agent': 'draft the Project Agent',
  'Refresher/code-intel': 'build the code graph',
};

/** "Being born · next: write the first knowledge (4/9)" — the next step first, so a
 *  truncated card still shows it; the count is the P6 checks done of all. */
export function bornText(p: { done: number; total: number; next?: string | null } | null | undefined): string {
  if (!p) return 'Being born';
  const key = p.next ? p.next.replace(/^[^A-Za-z]+/, '').trim() : '';
  const step = key ? (NEXT_STEP[key] ?? key.replace(/\//g, ' ')) : '';
  return step ? `Being born · next: ${step} (${p.done}/${p.total})` : `Being born · ${p.done} of ${p.total} checks`;
}
