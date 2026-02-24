import { useState } from 'react';
import type { AskUserQuestion as AskUserQuestionType } from '../../types';
import Button from './Button';

interface AskUserQuestionProps {
  questions: AskUserQuestionType[];
  toolUseId: string;
  onSubmit: (toolUseId: string, answers: Record<string, string>) => void;
  disabled?: boolean;
}

export default function AskUserQuestion({
  questions,
  toolUseId,
  onSubmit,
  disabled = false,
}: AskUserQuestionProps) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [showCustom, setShowCustom] = useState<Record<string, boolean>>({});

  const handleOptionToggle = (question: string, optionLabel: string, multiSelect: boolean) => {
    if (disabled) return;

    setAnswers((prev) => {
      const current = prev[question] || [];

      if (multiSelect) {
        // Toggle for multi-select
        if (current.includes(optionLabel)) {
          return { ...prev, [question]: current.filter((o) => o !== optionLabel) };
        } else {
          return { ...prev, [question]: [...current, optionLabel] };
        }
      } else {
        // Replace for single-select
        return { ...prev, [question]: [optionLabel] };
      }
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

  const handleCustomInputChange = (question: string, value: string) => {
    if (disabled) return;
    setCustomInputs((prev) => ({ ...prev, [question]: value }));
  };

  const handleSubmit = () => {
    const finalAnswers: Record<string, string> = {};

    questions.forEach((q) => {
      if (showCustom[q.question] && customInputs[q.question]) {
        finalAnswers[q.question] = customInputs[q.question];
      } else {
        const selected = answers[q.question] || [];
        finalAnswers[q.question] = selected.join(', ');
      }
    });

    onSubmit(toolUseId, finalAnswers);
  };

  const isComplete = questions.every((q) => {
    if (showCustom[q.question]) {
      return !!customInputs[q.question]?.trim();
    }
    return (answers[q.question] || []).length > 0;
  });

  return (
    <div className="bg-[var(--color-card)] border border-primary/30 rounded-lg p-4 my-3">
      <div className="flex items-center gap-2 mb-4">
        <span className="material-symbols-outlined text-primary">help_outline</span>
        <span className="font-medium text-[var(--color-text)]">Claude needs your input</span>
      </div>

      {questions.map((q, qIndex) => (
        <div key={qIndex} className="mb-4 last:mb-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="px-2 py-0.5 bg-primary/20 text-primary text-xs rounded">
              {q.header}
            </span>
            {q.multiSelect && (
              <span className="text-xs text-[var(--color-text-muted)]">(Select multiple)</span>
            )}
          </div>
          <p className="text-[var(--color-text)] text-sm mb-3">{q.question}</p>

          <div className="space-y-2">
            {q.options.map((option, optIndex) => {
              const isSelected = (answers[q.question] || []).includes(option.label);
              const isDisabledByCustom = showCustom[q.question];

              return (
                <button
                  key={optIndex}
                  onClick={() => handleOptionToggle(q.question, option.label, q.multiSelect)}
                  disabled={disabled || isDisabledByCustom}
                  className={`w-full text-left p-3 rounded-lg border transition-all ${
                    isSelected
                      ? 'border-primary bg-primary/10'
                      : isDisabledByCustom
                      ? 'border-[var(--color-border)] bg-[var(--color-bg)] opacity-50 cursor-not-allowed'
                      : 'border-[var(--color-border)] bg-[var(--color-bg)] hover:border-primary/50'
                  } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
                >
                  <div className="flex items-start gap-3">
                    <div
                      className={`w-5 h-5 rounded-${q.multiSelect ? 'sm' : 'full'} border-2 flex items-center justify-center flex-shrink-0 mt-0.5 ${
                        isSelected ? 'border-primary bg-primary' : 'border-[var(--color-text-muted)]'
                      }`}
                    >
                      {isSelected && (
                        <span className="material-symbols-outlined text-[var(--color-text)] text-sm">
                          {q.multiSelect ? 'check' : 'circle'}
                        </span>
                      )}
                    </div>
                    <div>
                      <p className="text-[var(--color-text)] text-sm font-medium">{option.label}</p>
                      <p className="text-[var(--color-text-muted)] text-xs mt-0.5">{option.description}</p>
                    </div>
                  </div>
                </button>
              );
            })}

            {/* Custom "Other" option */}
            <div className="pt-2 border-t border-[var(--color-border)]">
              <button
                onClick={() => handleCustomToggle(q.question)}
                disabled={disabled}
                className={`w-full text-left p-3 rounded-lg border transition-all ${
                  showCustom[q.question]
                    ? 'border-primary bg-primary/10'
                    : 'border-[var(--color-border)] bg-[var(--color-bg)] hover:border-primary/50'
                } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
                      showCustom[q.question] ? 'border-primary bg-primary' : 'border-[var(--color-text-muted)]'
                    }`}
                  >
                    {showCustom[q.question] && (
                      <span className="material-symbols-outlined text-[var(--color-text)] text-sm">circle</span>
                    )}
                  </div>
                  <span className="text-[var(--color-text)] text-sm">Other (custom answer)</span>
                </div>
              </button>

              {showCustom[q.question] && (
                <div className="mt-2 ml-8">
                  <input
                    type="text"
                    value={customInputs[q.question] || ''}
                    onChange={(e) => handleCustomInputChange(q.question, e.target.value)}
                    placeholder="Enter your custom answer..."
                    disabled={disabled}
                    className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] text-sm placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary"
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      ))}

      <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
        <Button
          onClick={handleSubmit}
          disabled={disabled || !isComplete}
          className="w-full"
        >
          Submit Answer{questions.length > 1 ? 's' : ''}
        </Button>
      </div>
    </div>
  );
}
