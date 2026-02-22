import clsx from 'clsx';

type Status = 'online' | 'offline' | 'error' | 'active' | 'inactive';

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusConfig: Record<Status, { label: string; color: string; bgColor: string }> = {
  online: {
    label: 'Online',
    color: 'text-status-online',
    bgColor: 'bg-status-online/20',
  },
  offline: {
    label: 'Offline',
    color: 'text-status-offline',
    bgColor: 'bg-status-offline/20',
  },
  error: {
    label: 'Error',
    color: 'text-status-error',
    bgColor: 'bg-status-error/20',
  },
  active: {
    label: 'Active',
    color: 'text-status-online',
    bgColor: 'bg-status-online/20',
  },
  inactive: {
    label: 'Inactive',
    color: 'text-status-offline',
    bgColor: 'bg-status-offline/20',
  },
};

export default function StatusBadge({ status, className }: StatusBadgeProps) {
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
      {config.label}
    </span>
  );
}
