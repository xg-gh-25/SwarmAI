import clsx from 'clsx';
import LoadingSpinner from './LoadingSpinner';

/**
 * Button — a labelled button on the app's button scale (index.css: btn-sm / btn-md /
 * btn-lg for size, btn-primary / -secondary / -ghost / -danger for look). The icon
 * takes the tier glyph of its size (icon-xs / icon-sm / icon-md).
 */
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
  icon?: string;
  children: React.ReactNode;
}

const ICON = {
  sm: 'material-symbols-outlined icon-xs',
  md: 'material-symbols-outlined icon-sm',
  lg: 'material-symbols-outlined icon-md',
} as const;

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
        `btn-${size}`,
        `btn-${variant}`,
        'transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
        className
      )}
      disabled={disabled || isLoading}
      {...props}
    >
      {isLoading ? (
        <LoadingSpinner size="sm" />
      ) : icon ? (
        <span className={ICON[size]}>{icon}</span>
      ) : null}
      {children}
    </button>
  );
}
