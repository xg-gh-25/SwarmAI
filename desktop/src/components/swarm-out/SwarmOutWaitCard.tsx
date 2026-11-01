/**
 * SwarmOutWaitCard — one thing a Hive waits on the person for (a question, an approval,
 * a pipeline run's decision), drawn with the chat's own card (ActFrame / ActOption /
 * ActDone, design docs/design/2026-10-26-turn-cards-unified.md) so it reads the same
 * here as in a chat tab (design docs/design/2026-10-31-swarm-out-single-loop-fixes.md
 * §2.2).
 *
 * By the wait's phase (§2.1, from the backend):
 * - waiting: the card. A question is the chat's AskUserQuestion; a decision is one
 *   ActOption per option (pick, then "Submit choice", ⌘↩); an approval shows the command
 *   and Deny / Approve (no Approve for a command too long to show whole). Nothing is sent
 *   before the person submits.
 * - sent / taken / gone: one ActDone line with what was asked and what was chosen, then
 *   "waiting for the Hive", "Hive received", or "not used" (settled somewhere else). A
 *   submit turns the card into the sent line at once, not at the next poll.
 * - refused: the card, its choice kept but not sent again (an answer is written once),
 *   the reason by code, and "Open session" to answer on the Hive.
 * A failed submit shows the backend's reason under the card; the choice is kept.
 *
 * Everything a Hive wrote (question, options, command, reason) is shown as plain text.
 *
 * @exports SwarmOutWaitCard
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import AskUserQuestion from '../common/AskUserQuestion';
import { ActDone, ActFrame, ActOption } from '../common/ActCard';
import { swarmOutService, type SwarmOutAsk } from '../../services/swarmOut';
import { when } from './live';
import { modKeyLabel } from '../../utils/platform';

type Body = { answers?: Record<string, string>; decision?: 'approve' | 'deny'; option_id?: string };

function errText(e: unknown, fallback: string): string {
  const m = (e as { message?: string })?.message;
  return m && m.length < 300 ? m : fallback;
}

/** the classes the agent that dispatched the run answers itself (backend core/approval_class) */
const HOST_CLASSES = new Set(['question', 'decision', 'hive_local', 'run_authorization']);

export function SwarmOutWaitCard({ wait, onAnswered, onDirty, onOpenSession, source }: {
  wait: SwarmOutAsk;
  /** an answer was written: reload the runs */
  onAnswered: () => void;
  /** something is picked and not yet sent (a run card keeps it from a step away) */
  onDirty?: (dirty: boolean) => void;
  /** open the run's Hive session (refused: answer it there) */
  onOpenSession?: () => void;
  /** the line above the card naming where it comes from (the run view's own list) */
  source?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pick, setPick] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);   // what was just sent, until the next poll says
  // an older backend gives no phase: refused, else waiting
  const given = wait.phase ?? (wait.refused ? 'refused' : 'waiting');
  const phase = sent !== null && given === 'waiting' ? 'sent' : given;

  useEffect(() => { onDirty?.(pick !== null && phase === 'waiting'); }, [pick, phase, onDirty]);
  useEffect(() => () => onDirty?.(false), [onDirty]);

  const send = useCallback(async (body: Body, shown: string) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await swarmOutService.answer(wait, body);
      setSent(shown);
      onAnswered();
    } catch (e) {
      setError(t('swarmOut.wait.failed', { error: errText(e, t('swarmOut.live.submitFailed')) }));
    } finally {
      setBusy(false);
    }
  }, [busy, onAnswered, t, wait]);

  const chosenText = (raw: string | undefined) => (wait.kind === 'approval'
    ? (raw === 'approve' ? t('swarmOut.wait.approved') : raw === 'deny' ? t('swarmOut.wait.denied') : (raw ?? ''))
    : (raw ?? ''));
  const asked = wait.question_text ?? (wait.kind === 'decision' ? wait.question
    : wait.kind === 'approval' ? wait.command : wait.questions?.[0]?.question) ?? '';
  const errorLine = error && (
    <div className="mt-1 text-[12px] text-[var(--color-status-error)]" role="alert" data-testid="swarm-out-wait-error">{error}</div>
  );
  const wrap = (node: React.ReactNode) => (
    <div data-testid="swarm-out-ask" data-phase={phase} data-kind={wait.kind}>
      {source && <div className="mb-1 text-[12px] text-[var(--color-text-muted)] min-w-0 truncate" data-testid="swarm-out-ask-source-line">{source}</div>}
      {node}
      {errorLine}
    </div>
  );

  // ── answered: one line ────────────────────────────────────────────────────────
  if (phase === 'sent' || phase === 'taken' || phase === 'gone') {
    const chosen = phase === 'sent' && sent !== null ? sent : chosenText(wait.chosen);
    const tail = phase === 'sent' ? t('swarmOut.wait.sent')
      : phase === 'taken' ? t('swarmOut.wait.taken') : t('swarmOut.wait.gone');
    return wrap(
      <ActDone testId="swarm-out-wait-done" icon={phase === 'sent' ? 'schedule' : phase === 'taken' ? 'check' : 'remove'}
        label={wait.answered_by === 'host' ? t('swarmOut.wait.doneByAgent') : t('swarmOut.wait.done')}>
        <span className="whitespace-pre-wrap">{asked}</span> → <span className="text-[var(--color-text)]">{chosen}</span>{' '}
        <span data-testid="swarm-out-wait-phase" className={phase === 'sent' ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-dim)]'}>· {tail}</span>
      </ActDone>,
    );
  }

  // ── refused: the card, the choice kept, the reason, and where to answer ─────────
  if (phase === 'refused') {
    const reason = t(`swarmOut.wait.reason.${wait.refused_reason ?? 'other'}`, { defaultValue: t('swarmOut.wait.reason.other') });
    return wrap(
      <ActFrame testId="swarm-out-wait-refused" icon="error" title={titleOf(wait, t)} meta={when(wait.asked_at)}
        footer={onOpenSession && (
          <button type="button" className="btn-sm btn-secondary" onClick={onOpenSession} data-testid="swarm-out-wait-open">{t('swarmOut.wait.openSession')} ↗</button>
        )}>
        <div className="whitespace-pre-wrap">{asked}</div>
        {wait.chosen && <div className="text-[12.5px] text-[var(--color-text-secondary)]">→ {chosenText(wait.chosen)}</div>}
        <div className="text-[12.5px] text-[var(--color-status-error)]" data-testid="swarm-out-ask-refused">{t('swarmOut.wait.refused', { reason })}</div>
        <div className="text-[12px] text-[var(--color-text-muted)]">{t('swarmOut.wait.refusedHint')}</div>
      </ActFrame>,
    );
  }

  // ── waiting ─────────────────────────────────────────────────────────────────────
  // a wait the dispatching agent answers for: it is handling it; the person may answer first
  const agentNote = HOST_CLASSES.has(wait.approval_class ?? '') && (
    <div className="mb-1 text-[12px] text-[var(--color-text-muted)]" data-testid="swarm-out-wait-agent">{t('swarmOut.wait.agentAnswers')}</div>
  );
  const wrapWaiting = (node: React.ReactNode) => wrap(<>{agentNote}{node}</>);
  if (wait.kind === 'question') {
    const questions = (wait.questions ?? []).map((q) => ({
      question: q.question, header: q.header, multiSelect: q.multiSelect,
      options: q.options.map((o) => ({ label: o.label, description: o.description })),
    }));
    const omitted = (wait.omitted?.questions ?? 0) + (wait.omitted?.options ?? 0);
    return wrapWaiting(
      <>
        <AskUserQuestion questions={questions} toolUseId={wait.request_id} disabled={busy} onDirty={onDirty}
          onSubmit={(_id, answers) => { void send({ answers }, Object.values(answers).join(' · ')); }} />
        {omitted > 0 && (
          <div className="text-[12px] text-[var(--color-text-muted)]" data-testid="swarm-out-ask-omitted">{t('swarmOut.ask.omitted', { count: omitted })}</div>
        )}
      </>,
    );
  }

  if (wait.kind === 'approval') {
    return wrapWaiting(
      <ActFrame testId="swarm-out-wait-approval" icon="shield" title={titleOf(wait, t)}
        meta={`${t('swarmOut.wait.approvalMeta')} · ${when(wait.asked_at)}`}
        footer={(
          <>
            <button type="button" className="btn-sm btn-secondary" disabled={busy}
              onClick={() => { void send({ decision: 'deny' }, t('swarmOut.wait.denied')); }}>{t('swarmOut.ask.deny')}</button>
            {!wait.truncated && (
              <button type="button" className="btn-sm btn-primary" disabled={busy}
                onClick={() => { void send({ decision: 'approve' }, t('swarmOut.wait.approved')); }}>{t('swarmOut.ask.approve')}</button>
            )}
          </>
        )}>
        {wait.tool_name && <div className="font-mono text-[12px] text-[var(--color-text-muted)]" data-testid="swarm-out-ask-tool">{wait.tool_name}</div>}
        {wait.reason && <p className="text-[12.5px] text-[var(--color-text-secondary)]">{wait.reason}</p>}
        <div className="rounded-[7px] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-[12px]">
          <code className="break-all whitespace-pre-wrap text-[var(--color-text)]">{wait.command}</code>
        </div>
        {wait.truncated && <p className="text-[12px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-truncated">{t('swarmOut.ask.truncated')}</p>}
      </ActFrame>,
    );
  }

  // a pipeline run's decision: pick one option, then submit
  const options = wait.options ?? [];
  const submit = () => {
    const o = options.find((x) => x.id === pick);
    if (!o || busy || wait.truncated) return;
    void send({ option_id: o.id }, `${o.id} — ${o.choice}`);
  };
  return wrapWaiting(
    <div onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); } }}>
      <ActFrame testId="swarm-out-wait-decision" icon="help" title={t('swarmOut.ask.decisionTitle')}
        meta={`${t('swarmOut.wait.decisionMeta')} · ${when(wait.asked_at)}`}
        footer={!wait.truncated && (
          <button type="button" className="btn-sm btn-primary" disabled={busy || pick === null} onClick={submit}
            data-testid="swarm-out-wait-submit">
            {t('swarmOut.wait.submit')} <span className="opacity-70 text-[11px]">{modKeyLabel()}↩</span>
          </button>
        )}>
        <div className="whitespace-pre-wrap">{wait.question}</div>
        {wait.why && <div className="text-[12.5px] text-[var(--color-text-muted)]">{t('swarmOut.ask.decisionWhy', { why: wait.why })}</div>}
        {wait.truncated ? (
          <div className="text-[12px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-truncated">{t('swarmOut.ask.decisionTruncated')}</div>
        ) : (
          <div className="space-y-1.5" data-testid="swarm-out-ask-decision">
            {options.map((o) => (
              <ActOption key={o.id} selected={pick === o.id} disabled={busy} onClick={() => setPick(o.id)}
                label={`${o.id} — ${o.choice}`}
                description={(o.consequence || !o.reversible || o.risk === 'high') ? (
                  <>
                    {o.risk === 'high' && <Tag tone="error">{t('swarmOut.ask.highRisk')}</Tag>}
                    {!o.reversible && <Tag tone="warning">{t('swarmOut.ask.irreversible')}</Tag>}
                    {o.consequence}
                  </>
                ) : undefined} />
            ))}
          </div>
        )}
      </ActFrame>
    </div>,
  );
}

function titleOf(wait: SwarmOutAsk, t: (k: string) => string): string {
  return wait.kind === 'decision' ? t('swarmOut.ask.decisionTitle')
    : wait.kind === 'approval' ? t('swarmOut.ask.approvalTitle') : t('swarmOut.wait.questionMeta');
}

/** A small label in front of an option's consequence (high risk, irreversible). */
function Tag({ tone, children }: { tone: 'error' | 'warning'; children: React.ReactNode }) {
  const color = tone === 'error' ? 'var(--color-status-error)' : 'var(--color-status-warning)';
  return (
    <span className="mr-1.5 inline-block rounded px-1 text-[11px]"
      style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}>{children}</span>
  );
}
