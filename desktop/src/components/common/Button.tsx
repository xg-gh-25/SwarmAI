import clsx from 'clsx';
import LoadingSpinner from './LoadingSpinner';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  icon?: string;
  children: React.ReactNode;
}

const variantClasses = {
  primary: 'bg-primary hover:bg-primary-hover text-white',
  secondary: 'bg-[var(--color-card)] border border-[var(--color-border)] hover:bg-[var(--color-hover)] text-[var(--color-text)]',
  ghost: 'hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
  danger: 'bg-status-error/20 hover:bg-status-error/30 text-status-error',
};

const sizeClasses = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-2.5 text-base',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  isLoading = false,
  icon,
  children,
  className,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center gap-2 font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
        variantClasses[variant],
        sizeClasses[size],
        className
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <LoadingSpinner size="sm" />
      ) : icon ? (
        <span className="material-symbols-outlined text-xl">{icon}</span>
      ) : null}
      {children}
    </button>
  );
}
