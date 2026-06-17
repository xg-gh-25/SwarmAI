/**
 * RefreshContextModal — Confirms context refresh with clear explanation.
 *
 * Shows a friendly, non-technical explanation of what happens when the user
 * refreshes context. Goal: no panic, clear expectations, informed consent.
 *
 * Exports:
 * - ``RefreshContextModal`` — Modal component (default export)
 */

import Modal from '../common/Modal';
import Button from '../common/Button';

interface RefreshContextModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  isLoading?: boolean;
}

export default function RefreshContextModal({
  isOpen,
  onClose,
  onConfirm,
  isLoading = false,
}: RefreshContextModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Refresh Context" size="md">
      <div className="space-y-4">
        {/* Explanation */}
        <p className="text-sm text-[var(--color-text-secondary)]">
          This will restart the AI's memory in this conversation.
        </p>

        {/* What stays */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-[var(--color-text)]">What stays the same:</p>
          <ul className="text-xs text-[var(--color-text-secondary)] space-y-1 pl-4">
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>Your chat history stays visible (you can scroll up)</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>The AI restarts with a summary of key decisions and context</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>Any in-progress pipelines are safe (auto-resumes)</span>
            </li>
          </ul>
        </div>

        {/* What changes */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-[var(--color-text)]">What changes:</p>
          <ul className="text-xs text-[var(--color-text-secondary)] space-y-1 pl-4">
            <li className="flex items-start gap-2">
              <span className="text-yellow-500 mt-0.5">•</span>
              <span>The AI won't remember every detail — only the highlights</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-yellow-500 mt-0.5">•</span>
              <span>You may need to re-mention specific decisions if needed</span>
            </li>
          </ul>
        </div>

        {/* Analogy */}
        <p className="text-xs text-[var(--color-text-muted)] italic border-l-2 border-[var(--color-border)] pl-3">
          Think of it like briefing a colleague the next morning — they remember the important stuff, not every word.
        </p>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={isLoading}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={isLoading}>
            {isLoading ? 'Refreshing...' : 'Refresh Context'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
