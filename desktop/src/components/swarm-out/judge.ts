/**
 * Autonomy Judge rules (pure): the tag state and the one-sentence summary shown by
 * JudgeTag. The state is the worst verdict, not an average — any BLOCK makes the run
 * BLOCK (Run Isolation and Quality are hard constraints), otherwise the number of
 * WARNs, otherwise PASS; N/A and PENDING do not count.
 */
import { DIMENSIONS, type RunJudge, type Verdict } from './mockData';

export type JudgeState = 'PASS' | 'WARN' | 'BLOCK';

export function judgeState(verdicts: readonly Verdict[]): { state: JudgeState; warns: number } {
  const warns = verdicts.filter((v) => v === 'WARN').length;
  if (verdicts.includes('BLOCK')) return { state: 'BLOCK', warns };
  return { state: warns > 0 ? 'WARN' : 'PASS', warns };
}

export function judgeSummary(judge: RunJudge): string {
  const flagged = judge.verdicts
    .map((v, i) => [v, i] as const)
    .filter(([v]) => v === 'WARN' || v === 'BLOCK');
  if (flagged.length === 0) {
    return judge.verdicts.includes('PENDING')
      ? '到目前为止各维度都正常；Quality 要到验证阶段才有结论。'
      : '五个维度都 PASS：隔离干净、验证和 finding 全过、没有人类决策和救援、在时间和 Token 警戒线内。';
  }
  const parts = flagged.map(([v, i]) => `${DIMENSIONS[i]} ${v}：${judge.numbers[i]}`).join('；');
  return judge.verdicts.includes('BLOCK')
    ? `${parts}。有 BLOCK 的维度，这个 run 不算合格交付。`
    : `${parts}。其余维度 PASS。`;
}
