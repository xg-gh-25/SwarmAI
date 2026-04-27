/**
 * WelcomeScreen — 2-column briefing hub landing view.
 *
 * Displays a branded welcome with live session briefing data organized in
 * a 2-column card grid. Each section auto-hides when data is empty (D3).
 *
 * Section order follows action priority gradient (D8):
 * Focus (full width) → Working | Signals → Hot News → Swarm Output (full width) → Stocks (full width, collapsed)
 *
 * Click behavior (D12): all chat-bound clicks populate ChatInput with
 * rich blockquote context — no auto-send. Stock/Output clicks open files.
 *
 * @exports WelcomeScreen, WelcomeScreenProps
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  systemService,
  type SessionBriefing,
  type BriefingFocusItem,
} from '../../../services/system';
import type { ItemClickHandler } from './RightSidebar/types';
import type { RadarTodo } from '../../../types';
import { DEFAULT_WORKSPACE_ID } from '../../../types/workspace-config';
import { radarService } from '../../../services/radar';
import {
  filterActiveTodos,
  sortByPriorityThenDate,
  PRIORITY_COLORS,
} from './RightSidebar/TodoSection';
import {
  WorkingSection,
  SignalsSection,
  HotNewsSection,
  StocksSection,
  SwarmOutputSection,
  buildTodoContext,
} from './briefing';

// ---------------------------------------------------------------------------
// Focus Item (kept inline — only used here)
// ---------------------------------------------------------------------------

const PRIORITY_BADGES: Record<string, { label: string; cls: string }> = {
  P0: { label: 'P0', cls: 'bg-red-500/20 text-red-400 border-red-500/30' },
  P1: { label: 'P1', cls: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' },
  P2: { label: 'P2', cls: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
};

function FocusItem({
  item,
  onClick,
  onDismiss,
}: {
  item: BriefingFocusItem;
  onClick?: (title: string) => void;
  onDismiss?: (title: string) => void;
}) {
  const badge = PRIORITY_BADGES[item.priority] ?? PRIORITY_BADGES.P2;
  return (
    <div className="group flex items-center gap-2 px-2 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors">
      <button
        type="button"
        onClick={() => onClick?.(item.title)}
        className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer text-left"
      >
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${badge.cls}`}>
          {badge.label}
        </span>
        <span className="text-sm text-[var(--color-text)] truncate">{item.title}</span>
        {item.momentum && (
          <span className="text-[10px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded font-mono shrink-0">
            ⚡ active
          </span>
        )}
      </button>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onDismiss?.(item.title); }}
        className="shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-[var(--color-bg-hover)] transition-all cursor-pointer"
        title="Dismiss"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section Card wrapper
// ---------------------------------------------------------------------------

function SectionCard({
  icon,
  title,
  count,
  accent,
  children,
  className = '',
}: {
  icon: string;
  title: string;
  count?: number;
  accent?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))] ${className}`}
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
        <span className="text-[12px]">{icon}</span>
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[var(--color-text-muted)]">
          {title}
        </span>
        {count != null && count > 0 && (
          <span className="ml-auto inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-medium rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]">
            {count}
          </span>
        )}
      </div>
      <div className="px-3 pb-2.5">
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Container width hook — responsive to actual panel width, not viewport
// ---------------------------------------------------------------------------

/** Minimum container width (px) to render 2-column layout. */
const TWO_COL_MIN_WIDTH = 560;

function useContainerWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setWidth(entry.contentRect.width);
      }
    });
    ro.observe(el);
    // Set initial width
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);

  return { ref, width };
}

// ---------------------------------------------------------------------------
// Todo priorities reuse PRIORITY_COLORS from TodoSection (single source of truth)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export interface WelcomeScreenProps {
  /** Legacy: sends title as chat message (auto-send). Used by focus items. */
  onFocusClick?: (title: string) => void;
  /** New: populates ChatInput with message + context (no auto-send). */
  onItemClick?: ItemClickHandler;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onFocusClick, onItemClick }) => {
  const [briefing, setBriefing] = useState<SessionBriefing | null>(null);
  const [radarTodos, setRadarTodos] = useState<RadarTodo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const { ref: containerRef, width: containerWidth } = useContainerWidth();

  useEffect(() => {
    let cancelled = false;
    // Fetch briefing + radar todos in parallel.
    // Todos come from the same API as Radar sidebar (single source of truth).
    Promise.all([
      systemService.getBriefing(),
      radarService.fetchActiveTodos(DEFAULT_WORKSPACE_ID).catch(() => [] as RadarTodo[]),
    ])
      .then(([data, todos]) => {
        if (!cancelled) {
          setBriefing(data);
          setRadarTodos(sortByPriorityThenDate(filterActiveTodos(todos)));
        }
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, []);

  const handleDismissFocus = useCallback((title: string) => {
    if (briefing) {
      setBriefing({ ...briefing, focus: briefing.focus.filter((f) => f.title !== title) });
    }
    systemService.dismissFocus(title).catch(() => {});
  }, [briefing]);

  // Use onItemClick if available, fall back to onFocusClick (legacy compat)
  const handleItemClick: ItemClickHandler = useCallback((message, context) => {
    if (onItemClick) {
      onItemClick(message, context);
    } else if (onFocusClick) {
      onFocusClick(message);
    }
  }, [onItemClick, onFocusClick]);

  const hasFocus = briefing && briefing.focus.length > 0;
  const hasWorking = briefing && briefing.working.length > 0;
  const hasSignals = briefing && briefing.signals.length > 0;
  const hasHotNews = briefing && briefing.hotNews.length > 0;
  const hasStocks = briefing && briefing.stocks.length > 0;
  const hasTodos = radarTodos.length > 0;
  const hasOutput = briefing && (
    briefing.output.builds.length > 0 ||
    briefing.output.content.length > 0 ||
    briefing.output.files.length > 0
  );
  const hasAnyBriefing = hasFocus || hasWorking || hasSignals || hasHotNews ||
    hasStocks || hasTodos || hasOutput || briefing?.learning;

  return (
    <div ref={containerRef} className="flex flex-col items-center h-full px-4 overflow-y-auto">
      {/* Top spacer */}
      <div className="flex-1 min-h-6" />

      {/* Icon + heading */}
      <div className="relative mb-3 select-none">
        <div
          className="absolute top-1/2 left-1/2 w-[120px] h-[120px] -translate-x-1/2 -translate-y-1/2 rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(0, 212, 255, 0.2) 0%, transparent 70%)' }}
        />
        <img
          src="/swarm-avatar.svg" alt="SwarmAI" className="relative w-12 h-12 rounded-full" draggable={false}
        />
      </div>
      <h1
        className="text-xl font-bold mb-3 select-none"
        style={{
          background: 'linear-gradient(135deg, #00d4ff 0%, #a855f7 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
        }}
      >
        Welcome to SwarmAI
      </h1>

      {/* Briefing content */}
      {!loaded ? null : hasAnyBriefing ? (
        <div className="w-full max-w-2xl space-y-3 mt-2">
          {/* Focus bar (full width) */}
          {hasFocus && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))] px-2 py-1.5">
              {briefing!.focus.map((item) => (
                <FocusItem key={item.title} item={item} onClick={onFocusClick} onDismiss={handleDismissFocus} />
              ))}
            </div>
          )}

          {/* Two-column grid — collapses to single column when one side is empty */}
          {(() => {
            const leftCards = [];
            if (hasWorking) leftCards.push(
              <SectionCard key="working" icon="📋" title="Working" count={briefing!.working.length} accent="rgba(251,191,36,0.6)">
                <WorkingSection items={briefing!.working} onItemClick={handleItemClick} />
              </SectionCard>
            );
            if (hasHotNews) leftCards.push(
              <SectionCard key="hot" icon="🔥" title="Hot News" count={briefing!.hotNews.length} accent="rgba(245,158,11,0.5)">
                <HotNewsSection items={briefing!.hotNews} onItemClick={handleItemClick} />
              </SectionCard>
            );
            if (hasTodos) leftCards.push(
              <SectionCard key="todo" icon="☑" title="Todo" count={radarTodos.length} accent="rgba(239,68,68,0.5)">
                <div className="space-y-0.5">
                  {radarTodos.slice(0, 10).map((todo) => {
                    const dotColor = PRIORITY_COLORS[todo.priority] ?? PRIORITY_COLORS.none;
                    return (
                      <button
                        key={todo.id}
                        type="button"
                        onClick={() => handleItemClick(
                          `[ToDo:${todo.id}] ${todo.title}`,
                          buildTodoContext({
                            id: todo.id,
                            title: todo.title,
                            priority: todo.priority,
                            status: todo.status,
                            nextStep: undefined,
                            description: todo.description ?? undefined,
                          }),
                        )}
                        className="flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer"
                      >
                        <span
                          className="shrink-0 w-2 h-2 rounded-full"
                          style={{ backgroundColor: dotColor }}
                        />
                        <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                          {todo.title}
                        </span>
                        {todo.priority !== 'none' && (
                          <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] uppercase font-mono">
                            {todo.priority}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </SectionCard>
            );

            const rightCards = [];
            if (hasSignals) rightCards.push(
              <SectionCard key="signals" icon="📡" title="Signals" count={briefing!.signals.length} accent="rgba(59,130,246,0.5)">
                <SignalsSection items={briefing!.signals} onItemClick={handleItemClick} />
              </SectionCard>
            );

            const hasLeft = leftCards.length > 0;
            const hasRight = rightCards.length > 0;
            const useTwoCol = hasLeft && hasRight && containerWidth >= TWO_COL_MIN_WIDTH;

            return (
              <div className={useTwoCol ? 'grid grid-cols-2 gap-3' : 'space-y-3'}>
                {useTwoCol ? (
                  <>
                    <div className="space-y-3">{leftCards}</div>
                    <div className="space-y-3">{rightCards}</div>
                  </>
                ) : (
                  [...leftCards, ...rightCards]
                )}
              </div>
            );
          })()}

          {/* Swarm Output (full width) */}
          {hasOutput && (
            <SectionCard icon="🐝" title="Swarm Output" accent="rgba(168,85,247,0.45)">
              <SwarmOutputSection output={briefing!.output} />
            </SectionCard>
          )}

          {/* Stocks (full width, collapsed — personal info, kept low-profile) */}
          {hasStocks && (
            <SectionCard icon="📈" title="Stocks" count={briefing!.stocks.length} accent="rgba(34,197,94,0.45)">
              <StocksSection items={briefing!.stocks} defaultVisible={4} />
            </SectionCard>
          )}

          {/* Learning insight */}
          {briefing?.learning && (
            <div className="flex items-start gap-2 bg-[var(--color-bg-hover)] rounded-md px-3 py-2">
              <span className="text-purple-400 shrink-0 mt-0.5">💡</span>
              <p className="text-[11px] text-[var(--color-text-secondary)] leading-relaxed">
                {briefing.learning}
              </p>
            </div>
          )}
        </div>
      ) : (
        /* Fallback when no briefing data */
        <div className="mt-4 space-y-3 text-center">
          <p className="text-base text-[var(--color-text)]">
            Work smarter. Move faster. Stress less.
          </p>
          <p className="text-sm text-[var(--color-text-secondary)] max-w-sm">
            Remembers everything. Learns every session. Gets better every time.
          </p>
          <div className="flex items-center gap-4 mt-4 text-[var(--color-text-secondary)] justify-center">
            {['Chat', 'Research', 'Code', 'Remember'].map((label) => (
              <span key={label} className="text-[11px]">{label}</span>
            ))}
          </div>
        </div>
      )}

      {/* Bottom spacer */}
      <div className="flex-1 min-h-6" />
    </div>
  );
};

export default WelcomeScreen;
