import React, { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import type { ErrorResponse } from '../../types';
import { ErrorCodes } from '../../types';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  onRetry?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo });
    this.props.onError?.(error, errorInfo);

    // Log to console in development
    if (import.meta.env.DEV) {
      console.error('ErrorBoundary caught an error:', error, errorInfo);
    }
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <ErrorFallback
          error={this.state.error}
          onRetry={this.handleRetry}
        />
      );
    }

    return this.props.children;
  }
}

interface ErrorFallbackProps {
  error: Error | null;
  onRetry?: () => void;
}

export function ErrorFallback({ error, onRetry }: ErrorFallbackProps): React.ReactElement {
  const errorMessage = error?.message || 'An unexpected error occurred';

  return (
    <div className="flex flex-col items-center justify-center min-h-[400px] p-8 text-center">
      <div className="w-16 h-16 mb-4 rounded-full bg-red-500/10 flex items-center justify-center">
        <span className="material-symbols-outlined text-red-500 text-3xl">error</span>
      </div>
      <h2 className="text-xl font-semibold text-[var(--color-text)] mb-2">Something went wrong</h2>
      <p className="text-[var(--color-text-muted)] mb-6 max-w-md">{errorMessage}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-[var(--color-text)] rounded-lg hover:bg-primary/80 transition-colors"
        >
          <span className="material-symbols-outlined text-sm">refresh</span>
          Try again
        </button>
      )}
    </div>
  );
}

// API Error Display Component
interface ApiErrorProps {
  error: ErrorResponse;
  onRetry?: () => void;
  onDismiss?: () => void;
  compact?: boolean;
}

export function ApiError({ error, onRetry, onDismiss, compact = false }: ApiErrorProps): React.ReactElement {
  const getErrorIcon = (code: string): string => {
    if (code.startsWith('AUTH_')) return 'lock';
    if (code === ErrorCodes.RATE_LIMIT_EXCEEDED) return 'timer';
    if (code.endsWith('_NOT_FOUND')) return 'search_off';
    if (code === ErrorCodes.VALIDATION_FAILED) return 'warning';
    if (code === ErrorCodes.SERVICE_UNAVAILABLE || code === ErrorCodes.DATABASE_UNAVAILABLE) return 'cloud_off';
    if (code === ErrorCodes.AGENT_TIMEOUT) return 'hourglass_empty';
    return 'error';
  };

  const getErrorColor = (code: string): string => {
    if (code.startsWith('AUTH_')) return 'text-yellow-500';
    if (code === ErrorCodes.RATE_LIMIT_EXCEEDED) return 'text-orange-500';
    if (code.endsWith('_NOT_FOUND')) return 'text-blue-500';
    return 'text-red-500';
  };

  if (compact) {
    return (
      <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20">
        <span className={`material-symbols-outlined text-sm ${getErrorColor(error.code)}`}>
          {getErrorIcon(error.code)}
        </span>
        <span className="text-sm text-[var(--color-text-muted)] flex-1">{error.message}</span>
        {onDismiss && (
          <button onClick={onDismiss} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="p-6 rounded-xl bg-[var(--color-card)] border border-[var(--color-border)]">
      <div className="flex items-start gap-4">
        <div className={`w-10 h-10 rounded-full bg-red-500/10 flex items-center justify-center flex-shrink-0`}>
          <span className={`material-symbols-outlined ${getErrorColor(error.code)}`}>
            {getErrorIcon(error.code)}
          </span>
        </div>
        <div className="flex-1">
          <h3 className="text-lg font-medium text-[var(--color-text)] mb-1">{error.message}</h3>
          {error.detail && (
            <p className="text-sm text-[var(--color-text-muted)] mb-3">{error.detail}</p>
          )}
          {error.suggestedAction && (
            <p className="text-sm text-[var(--color-text-muted)] mb-4 flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">lightbulb</span>
              {error.suggestedAction}
            </p>
          )}
          <div className="flex items-center gap-3">
            {onRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-[var(--color-text)] rounded-lg hover:bg-primary/80 transition-colors text-sm"
              >
                <span className="material-symbols-outlined text-sm">refresh</span>
                Try again
              </button>
            )}
            {onDismiss && (
              <button
                onClick={onDismiss}
                className="px-4 py-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors text-sm"
              >
                Dismiss
              </button>
            )}
          </div>
        </div>
      </div>
      {error.requestId && (
        <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
          <p className="text-xs text-[var(--color-text-muted)]">
            Request ID: <code className="bg-[var(--color-bg)] px-2 py-1 rounded">{error.requestId}</code>
          </p>
        </div>
      )}
    </div>
  );
}

// Toast-style error notification
interface ErrorToastProps {
  error: ErrorResponse;
  onDismiss: () => void;
  autoHideDuration?: number;
}

export function ErrorToast({ error, onDismiss, autoHideDuration = 5000 }: ErrorToastProps): React.ReactElement {
  React.useEffect(() => {
    if (autoHideDuration > 0) {
      const timer = setTimeout(onDismiss, autoHideDuration);
      return () => clearTimeout(timer);
    }
  }, [autoHideDuration, onDismiss]);

  return (
    <div className="fixed bottom-4 right-4 z-50 animate-in slide-in-from-bottom-2 fade-in duration-200">
      <ApiError error={error} onDismiss={onDismiss} compact />
    </div>
  );
}

export default ErrorBoundary;
