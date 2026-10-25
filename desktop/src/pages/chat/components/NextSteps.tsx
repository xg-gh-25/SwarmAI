/**
 * NextSteps — the reply's proposed next actions as clickable chips.
 *
 * Source: the trailing ```next-steps block of the final answer (STEERING #7
 * format), lifted out by `deriveTurnView` (pages/chat/turnView.ts) so the raw
 * block is never rendered. Each chip has two actions:
 *   - click the text → ADD it to the input box after what is already there (a
 *                      blank line between), so several chips can be picked and
 *                      edited together before one send
 *   - click ↑        → send that one step now
 * Both go through the existing inject-chat-input contract (injectChatInput.ts),
 * so there is exactly one input/send path. Nothing is ever sent without a click.
 * Keys 1/2/3 fill the matching chip, only when focus is not in an editable field,
 * is inside this chat (or nowhere), and only from the visible tab (background tabs
 * stay mounted with aria-hidden).
 *
 * Model-authored text is rendered as plain React text (never HTML).
 *
 * @exports NextSteps
 */
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { dispatchInjectChatInput } from '../injectChatInput';
import { tf } from '../tf';

function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export interface NextStepsProps {
  steps: string[];
}

export function NextSteps({ steps }: NextStepsProps) {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (steps.length === 0) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isEditableTarget(document.activeElement)) return;
      // Chat tabs stay MOUNTED in the background (TabView sets aria-hidden on an
      // inactive tab) — only the visible tab's chips may answer the keys.
      if (!rootRef.current || rootRef.current.closest('[aria-hidden="true"]')) return;
      // ...and only when focus is in THIS chat (or nowhere): a key pressed while a
      // button in an overlay / another panel has focus is not meant for the chips.
      const active = document.activeElement;
      const tabRoot = rootRef.current.closest('[aria-hidden]');
      if (active && active !== document.body && !(tabRoot ?? rootRef.current).contains(active)) return;
      const i = ['1', '2', '3'].indexOf(e.key);
      if (i < 0 || i >= steps.length) return;
      e.preventDefault();
      dispatchInjectChatInput({ text: steps[i], focus: true, autoSend: false, append: true });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [steps]);

  if (steps.length === 0) return null;

  return (
    <div ref={rootRef} className="mt-5" data-testid="next-steps">
      <div className="text-[12px] text-[var(--color-text-dim)] mb-2">{tf(t, 'chat.nextSteps.label', 'Next steps')}</div>
      <div className="flex flex-wrap gap-2">
        {steps.map((step, i) => (
          <span
            key={`${i}-${step}`}
            data-testid="next-step-chip"
            className="group/chip inline-flex items-stretch max-w-full rounded-full border border-[var(--color-border)] overflow-hidden transition-colors hover:border-primary/50 hover:bg-primary/10 focus-within:border-primary/50"
          >
            <button
              type="button"
              data-testid="next-step-fill"
              onClick={() => dispatchInjectChatInput({ text: step, focus: true, autoSend: false, append: true })}
              title={`${step}\n${tf(t, 'chat.nextSteps.fill', 'Add to input (shortcut {{key}})', { key: i + 1 })}`}
              className="pl-3.5 pr-1.5 py-1.5 text-[13.5px] text-[var(--color-text-secondary)] group-hover/chip:text-[var(--color-text)] text-left whitespace-normal break-words rounded-l-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary/50"
            >
              {step}
            </button>
            <button
              type="button"
              data-testid="next-step-send"
              onClick={() => dispatchInjectChatInput({ text: step, focus: true, autoSend: true })}
              title={`${tf(t, 'chat.nextSteps.send', 'Send now')}: ${step}`}
              aria-label={`${tf(t, 'chat.nextSteps.send', 'Send now')}: ${step}`}
              className="w-8 shrink-0 flex items-center justify-center text-[var(--color-text-dim)] border-l border-transparent group-hover/chip:text-primary group-hover/chip:border-primary/25 hover:bg-primary/20 transition-colors rounded-r-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary/50"
            >
              <span className="material-symbols-outlined icon-sm">arrow_upward</span>
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
