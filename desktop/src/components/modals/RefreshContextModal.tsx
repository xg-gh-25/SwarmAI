/**
 * RefreshContextModal — Confirms context refresh with clear explanation.
 *
 * Shows a friendly, non-technical explanation of what happens when the user
 * refreshes context. Goal: no panic, clear expectations, informed consent.
 *
 * Exports:
 * - ``RefreshContextModal`` — Modal component (default export)
 */

import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation();
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('ui.RefreshContextModal.title')} size="md">
      <div className="space-y-4">
        {/* Explanation */}
        <p className="text-sm text-[var(--color-text-secondary)]">
          {t('ui.RefreshContextModal.intro')}
        </p>

        {/* What stays */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-[var(--color-text)]">{t('ui.RefreshContextModal.staysTitle')}</p>
          <ul className="text-xs text-[var(--color-text-secondary)] space-y-1 pl-4">
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>{t('ui.RefreshContextModal.staysHistory')}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>{t('ui.RefreshContextModal.staysSummary')}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-green-500 mt-0.5">✓</span>
              <span>{t('ui.RefreshContextModal.staysPipelines')}</span>
            </li>
          </ul>
        </div>

        {/* What changes */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-[var(--color-text)]">{t('ui.RefreshContextModal.changesTitle')}</p>
          <ul className="text-xs text-[var(--color-text-secondary)] space-y-1 pl-4">
            <li className="flex items-start gap-2">
              <span className="text-yellow-500 mt-0.5">•</span>
              <span>{t('ui.RefreshContextModal.changesDetail')}</span>
            </li>
            <li className="flex items-start gap-2">
              <span className="text-yellow-500 mt-0.5">•</span>
              <span>{t('ui.RefreshContextModal.changesRemention')}</span>
            </li>
          </ul>
        </div>

        {/* Analogy */}
        <p className="text-xs text-[var(--color-text-muted)] italic border-l-2 border-[var(--color-border)] pl-3">
          {t('ui.RefreshContextModal.analogy')}
        </p>

        {/* Actions */}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={onClose} disabled={isLoading}>
            {t('common.button.cancel')}
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={isLoading}>
            {isLoading ? t('ui.RefreshContextModal.refreshing') : t('ui.RefreshContextModal.title')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
