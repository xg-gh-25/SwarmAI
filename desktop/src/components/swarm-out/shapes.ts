/**
 * Shapes of a Swarm Out pipeline run's audit as its Hive recorded it: the
 * five-dimension verdicts (Pipeline v1 run audit, run_protocol.audit: Run Isolation,
 * Quality, Autonomy, Speed, Token Cost — each PASS / WARN / BLOCK, or N/A / PENDING, no
 * composite score). /runs carries the verdicts; the numbers behind them come with the
 * run's whole record (/runs/{id}/result). JudgeTag renders them on each run card
 * (live.runJudge builds one from a result).
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

