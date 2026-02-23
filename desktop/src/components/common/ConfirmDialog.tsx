import { type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import Modal from './Modal';
import Button from './Button';

interface ConfirmDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'warning' | 'info';
  isLoading?: boolean;
}

export default function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  confirmText,
  cancelText,
  variant = 'danger',
  isLoading = false,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const resolvedConfirmText = confirmText ?? t('common.button.confirm');
  const resolvedCancelText = cancelText ?? t('common.button.cancel');
  const iconMap = {
    danger: { icon: 'delete', color: 'text-status-error', bg: 'bg-status-error/10' },
    warning: { icon: 'warning', color: 'text-status-warning', bg: 'bg-status-warning/10' },
    info: { icon: 'info', color: 'text-primary', bg: 'bg-primary/10' },
  };

  const { icon, color, bg } = iconMap[variant];

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="sm">
      <div className="text-center">
        <div className={`w-16 h-16 rounded-full ${bg} flex items-center justify-center mx-auto mb-4`}>
          <span className={`material-symbols-outlined text-3xl ${color}`}>{icon}</span>
        </div>
        <div className="text-muted mb-6">{message}</div>
        <div className="flex gap-3">
          <Button
            variant="secondary"
            className="flex-1"
            onClick={onClose}
            disabled={isLoading}
          >
            {resolvedCancelText}
          </Button>
          <Button
            variant={variant === 'danger' ? 'danger' : 'primary'}
            className="flex-1"
            onClick={onConfirm}
            isLoading={isLoading}
          >
            {resolvedConfirmText}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
