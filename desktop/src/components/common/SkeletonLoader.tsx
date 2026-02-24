import React from 'react';
import clsx from 'clsx';

interface SkeletonProps {
  className?: string;
  width?: string | number;
  height?: string | number;
  rounded?: 'none' | 'sm' | 'md' | 'lg' | 'xl' | 'full';
  animate?: boolean;
}

export function Skeleton({
  className,
  width,
  height,
  rounded = 'md',
  animate = true,
}: SkeletonProps): React.ReactElement {
  const roundedClasses = {
    none: 'rounded-none',
    sm: 'rounded-sm',
    md: 'rounded-md',
    lg: 'rounded-lg',
    xl: 'rounded-xl',
    full: 'rounded-full',
  };

  return (
    <div
      className={clsx(
        'bg-[#2a3142]',
        roundedClasses[rounded],
        animate && 'animate-pulse',
        className
      )}
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
      }}
    />
  );
}

// Skeleton for text lines
interface SkeletonTextProps {
  lines?: number;
  className?: string;
  lineHeight?: string;
  spacing?: string;
  lastLineWidth?: string;
}

export function SkeletonText({
  lines = 3,
  className,
  lineHeight = '1rem',
  spacing = '0.5rem',
  lastLineWidth = '60%',
}: SkeletonTextProps): React.ReactElement {
  return (
    <div className={clsx('space-y-2', className)} style={{ gap: spacing }}>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton
          key={index}
          height={lineHeight}
          width={index === lines - 1 ? lastLineWidth : '100%'}
        />
      ))}
    </div>
  );
}

// Skeleton for table rows
interface SkeletonTableProps {
  rows?: number;
  columns?: number;
  className?: string;
  showHeader?: boolean;
}

export function SkeletonTable({
  rows = 5,
  columns = 4,
  className,
  showHeader = true,
}: SkeletonTableProps): React.ReactElement {
  return (
    <div className={clsx('w-full', className)}>
      {showHeader && (
        <div className="flex gap-4 p-4 border-b border-[#2a3142]">
          {Array.from({ length: columns }).map((_, index) => (
            <Skeleton key={index} height={16} className="flex-1" />
          ))}
        </div>
      )}
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div
          key={rowIndex}
          className="flex gap-4 p-4 border-b border-[#2a3142] last:border-b-0"
        >
          {Array.from({ length: columns }).map((_, colIndex) => (
            <Skeleton key={colIndex} height={16} className="flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

// Skeleton for cards
interface SkeletonCardProps {
  className?: string;
  showImage?: boolean;
  imageHeight?: number;
  showTitle?: boolean;
  showDescription?: boolean;
  descriptionLines?: number;
  showActions?: boolean;
}

export function SkeletonCard({
  className,
  showImage = false,
  imageHeight = 120,
  showTitle = true,
  showDescription = true,
  descriptionLines = 2,
  showActions = true,
}: SkeletonCardProps): React.ReactElement {
  return (
    <div
      className={clsx(
        'p-4 rounded-xl bg-[#1a1f2e] border border-[#2a3142]',
        className
      )}
    >
      {showImage && (
        <Skeleton
          height={imageHeight}
          className="w-full mb-4"
          rounded="lg"
        />
      )}
      {showTitle && (
        <Skeleton height={20} width="70%" className="mb-3" />
      )}
      {showDescription && (
        <SkeletonText lines={descriptionLines} className="mb-4" />
      )}
      {showActions && (
        <div className="flex gap-2">
          <Skeleton height={32} width={80} rounded="lg" />
          <Skeleton height={32} width={80} rounded="lg" />
        </div>
      )}
    </div>
  );
}

// Skeleton for list items
interface SkeletonListProps {
  items?: number;
  className?: string;
  showAvatar?: boolean;
  avatarSize?: number;
  showSecondaryText?: boolean;
}

export function SkeletonList({
  items = 5,
  className,
  showAvatar = true,
  avatarSize = 40,
  showSecondaryText = true,
}: SkeletonListProps): React.ReactElement {
  return (
    <div className={clsx('space-y-3', className)}>
      {Array.from({ length: items }).map((_, index) => (
        <div key={index} className="flex items-center gap-3 p-3">
          {showAvatar && (
            <Skeleton
              width={avatarSize}
              height={avatarSize}
              rounded="full"
            />
          )}
          <div className="flex-1 space-y-2">
            <Skeleton height={16} width="60%" />
            {showSecondaryText && (
              <Skeleton height={12} width="40%" />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// Loading spinner component
interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  color?: string;
}

export function Spinner({
  size = 'md',
  className,
  color = '#2b6cee',
}: SpinnerProps): React.ReactElement {
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
  };

  return (
    <svg
      className={clsx('animate-spin', sizeClasses[size], className)}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
        style={{ color }}
      />
      <path
        className="opacity-75"
        fill={color}
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

// Loading overlay for forms/sections
interface LoadingOverlayProps {
  isLoading: boolean;
  children: React.ReactNode;
  className?: string;
  text?: string;
}

export function LoadingOverlay({
  isLoading,
  children,
  className,
  text,
}: LoadingOverlayProps): React.ReactElement {
  return (
    <div className={clsx('relative', className)}>
      {children}
      {isLoading && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#101622]/80 rounded-xl z-10">
          <Spinner size="lg" />
          {text && (
            <p className="mt-3 text-sm text-[#9da6b9]">{text}</p>
          )}
        </div>
      )}
    </div>
  );
}

// Page loading state
interface PageLoadingProps {
  text?: string;
}

export function PageLoading({ text = 'Loading...' }: PageLoadingProps): React.ReactElement {
  return (
    <div className="flex flex-col items-center justify-center min-h-[400px]">
      <Spinner size="lg" />
      <p className="mt-4 text-[#9da6b9]">{text}</p>
    </div>
  );
}

export default Skeleton;
