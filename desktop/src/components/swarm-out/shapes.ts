/**
 * Shapes of what a Swarm Out run will carry once its result and audit come back from
 * the Hive: the five-dimension verdicts (Pipeline v1 run audit, run_protocol.audit:
 * Run Isolation, Quality, Autonomy, Speed, Token Cost — each PASS / WARN / BLOCK, or
 * N/A / PENDING, no composite score) and the trace of what was prepared and what came
 * back. The live panel does not render them yet (it says TBD where they go): the Host
 * does not fetch a run's result or audit from its Hive. JudgeTag and TraceTags render
 * these shapes and keep their own tests, for when that data exists.
 */

export type Verdict = 'PASS' | 'WARN' | 'BLOCK' | 'NA' | 'PENDING';

export const DIMENSIONS = ['Run Isolation', 'Quality', 'Autonomy', 'Speed', 'Token Cost'] as const;
export const DIMENSION_ROLE = ['硬约束', '硬约束', '主要目标', '效率', '效率'] as const;

export interface RunJudge {
  /** Outcome line, e.g. "COMPLETED · LANDED · PUSH READY · 7d34ab1". */
  outcome: string;
  verdicts: [Verdict, Verdict, Verdict, Verdict, Verdict];
  numbers: [string, string, string, string, string];
  /** True while the run is still going: values are "so far". */
  live?: boolean;
}

/**
 * The trace of one Swarm Out run, in the order the run is built:
 * PREPARED before it starts (brain → Project Agent → context → Hive placement → start)
 * and DELIVERED after it ends (report → five-dimension audit → end, duration, tokens
 * → what flowed back into the brain). Every field is what the run record will hold;
 * a value not known yet (a run still going) is undefined, never a placeholder.
 */
export interface RunTrace {
  /** The DDD the context came from; undefined = a task with no brain (general agent). */
  brain?: { name: string; version: string; docs: string[] };
  /** From the brain's project-agent.yaml: why this agent ran the task this way. */
  agent?: {
    name: string;
    status: 'reviewed' | 'draft';
    profile: string;
    rule: string;
    validations: string[];
    placement: string;
    landing: string;
  };
  context: {
    goal: string;
    acceptance: string;
    scope?: string;
    slices: { source: string; score: number }[];
    memory: number;
    tokens: string;
    packedAt: string;
  };
  hive: { instance: string; slot: number; session: string; run?: string };
  startedAt?: string;
  endedAt?: string;
  duration: string;
  tokens: string;
  report?: {
    outcome: string;
    deliverables: string[];
    turns: number;
    flowedBack: string[];
    path: string;
  };
}
