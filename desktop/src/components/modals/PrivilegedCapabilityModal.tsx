import { useTranslation } from 'react-i18next';
import Modal from '../common/Modal';
import Button from '../common/Button';

interface PrivilegedCapabilityModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  capabilityName: string;
  capabilityType: 'skill' | 'mcp';
}

export default function PrivilegedCapabilityModal({
  isOpen,
  onClose,
  onConfirm,
  capabilityName,
  capabilityType,
}: PrivilegedCapabilityModalProps) {
  const { t } = useTranslation();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('settings.privileged.title')}
      size="sm"
    >
      <div className="space-y-4">
        {/* Warning Icon */}
        <div className="flex items-center gap-3 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
          <span className="text-2xl text-amber-500">⚠️</span>
          <p className="text-sm text-amber-400">
            {t('settings.privileged.warning', {
              name: capabilityName,
              type: t(`settings.privileged.type.${capabilityType}`),
            })}
          </p>
        </div>

        {/* Explanation */}
        <p className="text-sm text-[var(--color-text-muted)]">
          {t('settings.privileged.explanation')}
        </p>

        {/* Actions */}
        <div className="flex gap-3 pt-2">
          <Button variant="secondary" className="flex-1" onClick={onClose}>
            {t('common.button.cancel')}
          </Button>
          <Button variant="primary" className="flex-1" onClick={onConfirm}>
            {t('settings.privileged.confirm')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
