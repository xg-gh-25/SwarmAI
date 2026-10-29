/**
 * Autonomy Judge rules (pure): the tag state and the one-sentence summary shown by
 * JudgeTag. The state is the worst verdict, not an average — any BLOCK makes the run
 * BLOCK (Run Isolation and Quality are hard constraints), otherwise the number of
 * WARNs, otherwise PASS; N/A and PENDING do not count.
 */
import i18n from '../../i18n';
import { DIMENSIONS, type RunJudge, type Verdict } from './shapes';

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
  // N/A: no reference line on this install yet (Speed / Token) or nothing to judge; a
  // sentence never says such a dimension is within its line
  const na = judge.verdicts
    .map((v, i) => [v, i] as const)
    .filter(([v]) => v === 'NA')
    .map(([, i]) => DIMENSIONS[i]);
  const naNote = na.length ? i18n.t('swarmOut.judge.naNote', { list: na.join(i18n.t('swarmOut.sep.list')) }) : '';
  if (flagged.length === 0) {
    if (judge.verdicts.includes('PENDING')) return i18n.t('swarmOut.judge.pending');
    return na.length ? i18n.t('swarmOut.judge.noFlags', { na: naNote }) : i18n.t('swarmOut.judge.allPass');
  }
  const parts = flagged.map(([v, i]) => i18n.t('swarmOut.judge.flag', { dim: DIMENSIONS[i], verdict: v, number: judge.numbers[i] }))
    .join(i18n.t('swarmOut.sep.item'));
  return judge.verdicts.includes('BLOCK')
    ? i18n.t('swarmOut.judge.blocked', { parts, na: naNote })
    : i18n.t('swarmOut.judge.warned', { parts, na: naNote });
}
