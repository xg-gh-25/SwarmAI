import { useEffect, useRef } from 'react';
import clsx from 'clsx';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | 'fullscreen';
}

const sizeClasses = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  fullscreen: 'w-[90vw] max-w-6xl',  // slightly inset from edges; height managed on card
};

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  size = 'md',
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      ref={overlayRef}
      className={clsx(
        "fixed inset-0 z-50 flex justify-center bg-black/50 backdrop-blur-sm",
        size === 'fullscreen'
          ? 'items-start px-4 pb-4 pt-12'  // items-start + top padding clears macOS title bar
          : 'items-center p-4'
      )}
      onMouseDown={(e) => {
        // Only close when clicking directly on the overlay background
        if (e.target === overlayRef.current) {
          onClose();
        }
      }}
    >
      <div
        className={clsx(
          'w-full bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl flex flex-col',
          size === 'fullscreen' ? 'h-[calc(100vh-6rem)]' : 'max-h-[90vh]',
          sizeClasses[size]
        )}
        // Stop event propagation to prevent overlay close when clicking inside modal
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* Content - fills remaining height; fullscreen delegates scroll to children.
            overflow-hidden is REQUIRED: it hard-clips to the card and gives the child
            a definite height to resolve h-full/flex-1 against. Without it the child
            expands to natural height, overflows below the card, and the inner scroll
            container never engages (content cut off at bottom). */}
        <div className={clsx(
          "flex-1 min-h-0",
          size === 'fullscreen' ? 'flex flex-col overflow-hidden' : 'overflow-y-auto p-6'
        )}>{children}</div>
      </div>
    </div>
  );
}
