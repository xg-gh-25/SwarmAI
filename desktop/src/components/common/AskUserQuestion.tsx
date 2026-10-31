/**
 * AskUserQuestion — the agent's multiple-choice question, in the one frame every card
 * that waits on the user uses (ActCard; design 2026-10-26-turn-cards-unified.md §1 D).
 *
 * - Waiting: "Swarm needs your decision", each question with its tag, its options as
 *   ActOption boxes (multi-select ones square), "Other (custom answer)" with a text
 *   field, and Submit on the right of the footer.
 * - Answered (the block carries `answers`): one grey line per question that keeps the
 *   decision: the question, then what was chosen (a multi-select answer split back
 *   into its picks, a custom answer as typed). No form, no Submit.
 *
 * @exports AskUserQuestion (default)
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AskUserQuestion as AskUserQuestionType } from '../../types';
import { ActDone, ActFrame, ActOption } from './ActCard';

interface AskUserQuestionProps {
  questions: AskUserQuestionType[];
  toolUseId: string;
  onSubmit: (toolUseId: string, answers: Record<string, string>) => void;
  disabled?: boolean;
  /**
   * The user's submitted answers (question text → selected label(s)). When
   * present, the component renders the answered lines instead of the form.
   * Sourced from the persisted block, so it survives tab-switch + reconcile.
   */
  answers?: Record<string, string>;
  /** Whether something is picked or typed and not yet submitted (a host card, such as
   *  Swarm Out's run card, keeps it from a step away). */
  onDirty?: (dirty: boolean) => void;
}

export default function AskUserQuestion({
  questions: rawQuestions,
  toolUseId,
  onSubmit,
  disabled = false,
  answers: submittedAnswers,
  onDirty,
}: AskUserQuestionProps) {
  const { t } = useTranslation();
  // Defensive: questions may arrive as undefined/non-array from malformed
  // DB history or SSE events — guard to prevent crash (t.every is not a function).
  const questions = Array.isArray(rawQuestions) ? rawQuestions : [];

  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [showCustom, setShowCustom] = useState<Record<string, boolean>>({});
  const dirty = Object.values(answers).some((v) => v.length > 0)
    || Object.values(customInputs).some((v) => !!v.trim());
  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);
  useEffect(() => () => onDirty?.(false), [onDirty]);

  const handleOptionToggle = (question: string, optionLabel: string, multiSelect: boolean) => {
    if (disabled) return;
    setAnswers((prev) => {
      const current = prev[question] || [];
      if (multiSelect) {
        return current.includes(optionLabel)
          ? { ...prev, [question]: current.filter((o) => o !== optionLabel) }
          : { ...prev, [question]: [...current, optionLabel] };
      }
      return { ...prev, [question]: [optionLabel] };
    });
    // Clear custom input when selecting predefined option
    setShowCustom((prev) => ({ ...prev, [question]: false }));
    setCustomInputs((prev) => ({ ...prev, [question]: '' }));
  };

  const handleCustomToggle = (question: string) => {
    if (disabled) return;
    setShowCustom((prev) => ({ ...prev, [question]: !prev[question] }));
    if (!showCustom[question]) {
      // Clear predefined selections when switching to custom
      setAnswers((prev) => ({ ...prev, [question]: [] }));
    }
  };

  const handleSubmit = () => {
    const finalAnswers: Record<string, string> = {};
    questions.forEach((q) => {
      if (showCustom[q.question] && customInputs[q.question]) {
        finalAnswers[q.question] = customInputs[q.question];
      } else {
        finalAnswers[q.question] = (answers[q.question] || []).join(', ');
      }
    });
    onSubmit(toolUseId, finalAnswers);
  };

  const isComplete = questions.every((q) => (showCustom[q.question]
    ? !!customInputs[q.question]?.trim()
    : (answers[q.question] || []).length > 0));

  // Nothing to render — don't show empty shell with a Submit button.
  // (Placed after hooks to comply with Rules of Hooks.)
  if (questions.length === 0) return null;

  // ── Answered: one grey line per question that keeps the decision ───────
  if (submittedAnswers && Object.keys(submittedAnswers).length > 0) {
    return (
      <div data-testid="ask-answered" className="my-1">
        {questions.map((q, i) => {
          const chosen = submittedAnswers[q.question];
          // Multi-select answers are persisted as a ", "-joined string: split them
          // back into the picks (an option label holding ", " over-splits; display
          // only — the answer sent was the unsplit string).
          const picks = chosen ? (q.multiSelect ? chosen.split(', ').filter(Boolean) : [chosen]) : [];
          return (
            <ActDone key={i} label={t('chat.ask.answered', 'Answered')}>
              {q.question} →{' '}
              {picks.length === 0
                ? <span className="italic text-[var(--color-text-dim)]">{t('chat.ask.noSelection', 'no selection')}</span>
                : picks.map((p, j) => (
                  <span key={j}>{j > 0 && ', '}<span className="text-[var(--color-text)]">{p}</span></span>
                ))}
            </ActDone>
          );
        })}
      </div>
    );
  }

  const meta = questions.length === 1 ? questions[0].header : undefined;
  return (
    <ActFrame testId="ask-question" icon="help" title={t('chat.ask.title', 'Swarm needs your decision')} meta={meta}
      footer={(
        <button type="button" onClick={handleSubmit} disabled={disabled || !isComplete} className="btn-sm btn-primary">
          {questions.length > 1 ? t('chat.ask.submitMany', 'Submit Answers') : t('chat.ask.submit', 'Submit Answer')}
        </button>
      )}>
      {questions.map((q, qIndex) => (
        <div key={qIndex} className="space-y-1.5">
          {questions.length > 1 && (
            <div className="text-[11px] text-[var(--color-text-dim)]">{q.header}</div>
          )}
          <p className="text-[13px] text-[var(--color-text)]">
            {q.question}
            {q.multiSelect && <span className="ml-1.5 text-[11.5px] text-[var(--color-text-dim)]">{t('chat.ask.multi', '(Select multiple)')}</span>}
          </p>
          <div className="space-y-1.5">
            {(q.options || []).map((option, optIndex) => (
              <ActOption key={optIndex} label={option.label} description={option.description || undefined}
                round={!q.multiSelect}
                selected={(answers[q.question] || []).includes(option.label)}
                onClick={() => handleOptionToggle(q.question, option.label, q.multiSelect)}
                disabled={disabled || !!showCustom[q.question]} />
            ))}
            <ActOption label={t('chat.ask.other', 'Other (custom answer)')} selected={!!showCustom[q.question]}
              onClick={() => handleCustomToggle(q.question)} disabled={disabled} />
            {showCustom[q.question] && (
              <input
                type="text"
                value={customInputs[q.question] || ''}
                onChange={(e) => !disabled && setCustomInputs((prev) => ({ ...prev, [q.question]: e.target.value }))}
                placeholder={t('chat.ask.customPlaceholder', 'Enter your custom answer...')}
                disabled={disabled}
                className="w-full px-3 py-1.5 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-[7px] text-[13px] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary"
              />
            )}
          </div>
        </div>
      ))}
    </ActFrame>
  );
}
