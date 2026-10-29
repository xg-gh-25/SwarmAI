/**
 * Shapes of what a Swarm Out run will carry once its result and audit come back from
 * the Hive: the five-dimension verdicts (Pipeline v1 run audit, run_protocol.audit:
 * Run Isolation, Quality, Autonomy, Speed, Token Cost — each PASS / WARN / BLOCK, or
 * N/A / PENDING, no composite score). The live panel does not render them yet (it says TBD where they go): the Host
 * does not fetch a run's result or audit from its Hive. JudgeTag renders the verdicts
 * and keeps its own tests, for when that data exists.
 */

export type Verdict = 'PASS' | 'WARN' | 'BLOCK' | 'NA' | 'PENDING';

export const DIMENSIONS = ['Run Isolation', 'Quality', 'Autonomy', 'Speed', 'Token Cost'] as const;
/** Each dimension's role, as a locale key under swarmOut.judge.role. */
export const DIMENSION_ROLE = ['hard', 'hard', 'primary', 'efficiency', 'efficiency'] as const;

export interface RunJudge {
  /** Outcome line, e.g. "COMPLETED · LANDED · PUSH READY · 7d34ab1". */
  outcome: string;
  verdicts: [Verdict, Verdict, Verdict, Verdict, Verdict];
  numbers: [string, string, string, string, string];
  /** True while the run is still going: values are "so far". */
  live?: boolean;
}

