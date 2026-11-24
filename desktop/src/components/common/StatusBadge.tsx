import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

type Status = 'online' | 'offline' | 'error' | 'active' | 'inactive';

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusConfig: Record<Status, { labelKey: string; color: string; bgColor: string }> = {
  online: {
    labelKey: 'ui.StatusBadge.online',
    color: 'text-status-online',
    bgColor: 'bg-status-online/20',
  },
  offline: {
    labelKey: 'ui.StatusBadge.offline',
    color: 'text-status-offline',
    bgColor: 'bg-status-offline/20',
  },
  error: {
    labelKey: 'ui.StatusBadge.error',
    color: 'text-status-error',
    bgColor: 'bg-status-error/20',
  },
  active: {
    labelKey: 'ui.StatusBadge.active',
    color: 'text-status-online',
    bgColor: 'bg-status-online/20',
  },
  inactive: {
    labelKey: 'ui.StatusBadge.inactive',
    color: 'text-status-offline',
    bgColor: 'bg-status-offline/20',
  },
};

export default function StatusBadge({ status, className }: StatusBadgeProps) {
  const { t } = useTranslation();
  const config = statusConfig[status];

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium',
        config.bgColor,
        config.color,
        className
      )}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {t(config.labelKey)}
    </span>
  );
}
