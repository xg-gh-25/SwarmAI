import { ReactNode, useState, useCallback, useRef, useEffect, useMemo, type CSSProperties } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { useQuery } from '@tanstack/react-query';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { ExplorerProvider, useTreeData } from '../../contexts/ExplorerContext';
import { WorkspaceExplorer } from '../workspace-explorer';
import { BottomBar } from './BottomBar';
import { TerminalProvider, useTerminal, useTerminalHotkey } from '../../contexts/TerminalContext';
import TerminalPanel from '../terminal/TerminalPanel';
import { EXPLORER_OPEN_TERMINAL } from '../../constants/explorerEvents';
import FileEditorModal from '../common/FileEditorModal';
import FileViewerPanel from '../file-viewer/FileViewerPanel';
import { BrainHubDemoOverlay } from './BrainHubDemoOverlay';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';
import { OPEN_SETTINGS_EVENT } from '../common/CredentialBanner';
import { openExternal } from '../../utils/openExternal';
import SettingsModal from '../modals/SettingsModal';
import WorkspaceSettingsModal from '../modals/WorkspaceSettingsModal';
import EvalModal from '../modals/EvalModal';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import type { GitStatus } from '../../types';
import api from '../../services/api';
import { useToast } from '../../contexts/ToastContext';


// Left sidebar width constant
const LEFT_SIDEBAR_WIDTH = LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH;

// Minimum width for main chat panel to ensure usability
const MIN_MAIN_CHAT_PANEL_WIDTH = 300;

interface ThreeColumnLayoutProps {
  children: ReactNode;
}

// TopBar -- App-level intelligence bar.
// Shows: context ring (left), token usage metrics (right).
// Remains draggable for Tauri window move (macOS).
interface TokenUsageData {
  today_tokens_m: number;
  total_tokens_m: number;
  today_cost_usd: number;
  total_cost_usd: number;
}

function formatTokens(m: number): string {
  if (m >= 1000) return `${(m / 1000).toFixed(1)}B`;
  if (m >= 1) return `${m.toFixed(1)}M`;
  if (m >= 0.01) return `${(m * 1000).toFixed(0)}K`;
  return '0';
}

function TopBar() {
  const { data: tokenUsage } = useQuery<TokenUsageData>({
    queryKey: ['token-usage'],
    queryFn: async () => {
      const resp = await api.get<TokenUsageData>('/system/tokens/usage');
      return resp.data;
    },
    refetchInterval: 30_000, // refresh every 30s
    staleTime: 10_000,
  });

  const handleMouseDown = async (e: React.MouseEvent) => {
    if (e.button === 0 && e.clientX > 80) {
      try {
        await getCurrentWindow().startDragging();
      } catch (err) {
        console.error('Failed to start dragging:', err);
      }
    }
  };

  const todayDisplay = tokenUsage ? formatTokens(tokenUsage.today_tokens_m) : '--';
  const totalDisplay = tokenUsage ? formatTokens(tokenUsage.total_tokens_m) : '--';

  return (
    <div
      onMouseDown={handleMouseDown}
      className="h-10 bg-[var(--color-bg-chrome)] border-b border-[var(--color-border)] flex-shrink-0 select-none cursor-default flex items-center"
      data-tauri-drag-region
      data-testid="top-bar"
    >
      {/* Spacer for macOS traffic lights */}
      <div className="w-20 flex-shrink-0" />

      {/* Center: drag region (flexible spacer) */}
      <div className="flex-1" />

      {/* Right: token usage metrics — pr-5 (20px) keeps content away from window
          edge/resize handle. macOS has no right-side controls; Windows/Linux may
          have snap layout button area (~8px). 20px covers both safely. */}
      <div
        className="flex items-center gap-2 pr-5 text-[11px] text-[var(--color-text-muted)]"
        role="status"
        aria-label="Token usage"
        title={tokenUsage
          ? `Today: $${tokenUsage.today_cost_usd.toFixed(2)} | Total: $${tokenUsage.total_cost_usd.toFixed(2)}`
          : 'Loading...'}
      >
        <span className="text-[13px]">&#x1FA99;</span>
        <span>Today <strong className="text-[var(--color-text-secondary)]">{todayDisplay}</strong></span>
        <span className="text-[var(--color-border)]">|</span>
        <span>Total <strong className="text-[var(--color-text-secondary)]">{totalDisplay}</strong></span>
      </div>
    </div>
  );
}

// Group brand colors (B2 group-tint, 2026-07-12). Passed to each NavIconButton
// as the inline `--ac` CSS custom property so hover/active bg+ring+bar tint by
// group WITHOUT a Tailwind class (arbitrary-color classes risk JIT purge; an
// inline custom property never does — matches the existing color-mix convention
// in index.css). 4 groups: 做事(Terminal) / 能力(Skills,MCP) / 观测(CodeIntel,
// Engine,OSEval) / 知识(Memory,Signals). Footer (Settings) passes NO accent →
// .nav-btn falls back to the user's --color-primary accent (app-chrome follows
// the accent system, not a fixed group color); GitHub is a plain <a>, untouched.
const NAV_GROUP_COLOR = {
  do: '#60a5fa', // blue — 做事 (Terminal, the active tool)
  power: '#a78bfa', // purple — 能力 (Skills, MCP)
  observe: '#2dd4bf', // teal — 观测 (Code Intel, Engine, OS Eval)
  know: '#fbbf24', // amber — 知识 (Memory, Signals)
} as const;

/** Pure resolution core for the Signals nav click (exported for test).
 *  Given the /workspace/tree/expand children of Knowledge/Signals, return the
 *  path of the newest digest — names are YYYY-MM-DD-digest.md so the lexical max
 *  IS the chronological latest. *-weekly.md (same dir) is excluded. null if none. */
export function pickLatestDigest(children: Array<{ name?: string; path?: string }>): string | null {
  let best: { name: string; path: string } | null = null;
  for (const c of children) {
    if (!c?.name || !c?.path) continue;
    if (!c.name.endsWith('-digest.md')) continue;
    if (best === null || c.name > best.name) best = { name: c.name, path: c.path };
  }
  return best?.path ?? null;
}

// Left Sidebar - narrow navigation column with icon-only navigation
function LeftSidebar() {
  const { activeModal, openModal, closeModal, settingsTab, setSettingsTab, workspaceExplorerCollapsed, setWorkspaceExplorerCollapsed } = useLayout();
  const { addToast } = useToast();
  // Terminal panel open-state + toggle — LeftSidebar is inside <TerminalProvider>
  // so it reads the real panelOpen (for the active indicator) and shares the SAME
  // togglePanel as the BottomBar button + ⌘` hotkey (all three entries stay synced).
  const { panelOpen: terminalPanelOpen, togglePanel: toggleTerminal } = useTerminal();

  // Skills and MCP now open Settings with the corresponding tab pre-selected
  // Toggle: if already on that tab, close the modal
  const handleNavClick = (target: 'skills' | 'mcp' | 'engine') => {
    const tabMap = { skills: 'skills', mcp: 'mcp-servers', engine: 'engine' };
    const targetTab = tabMap[target];
    if (activeModal === 'settings' && settingsTab === targetTab) {
      closeModal();
    } else {
      setSettingsTab(targetTab);
      openModal('settings');
    }
  };

  // Open Code Intelligence graph overlay via custom event (BottomBar listens)
  const handleCodeIntelClick = () => {
    window.dispatchEvent(new CustomEvent('swarm:show-code-graph'));
  };

  // Open MEMORY.md in file viewer panel via custom event (ThreeColumnLayout listens on document)
  const handleMemoryClick = () => {
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: '.context/MEMORY.md' } }));
  };

  // Open the LATEST signal digest. The digest is written by a scheduled job, so
  // today's file often doesn't exist yet (esp. early in the day / weekends) —
  // hardcoding `<today>-digest.md` produced a file-not-found (run_a73566c4). We
  // list Knowledge/Signals via the existing tree/expand endpoint and open the
  // newest *-digest.md. Graceful toast on empty/failure — never a dead click.
  const handleSignalsClick = async () => {
    try {
      const resp = await api.get<Array<{ name?: string; path?: string }>>(
        '/workspace/tree/expand',
        { params: { path: 'Knowledge/Signals', depth: 1 } },
      );
      const latest = pickLatestDigest(resp.data ?? []);
      if (latest) {
        document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: latest } }));
      } else {
        addToast({ severity: 'info', message: 'No signal digest available yet.', autoDismiss: true });
      }
    } catch {
      addToast({ severity: 'warning', message: 'Could not load signals.', autoDismiss: true });
    }
  };

  // Tools group nav items
  const toolItems: { icon: string; label: string; target: 'skills' | 'mcp' }[] = [
    { icon: 'lightning', label: 'Skills', target: 'skills' },
    { icon: 'server', label: 'MCP Servers', target: 'mcp' },
  ];

  return (
    <aside
      className="bg-[var(--color-bg-chrome)] border-r border-[var(--color-border)] flex flex-col flex-shrink-0"
      style={{ width: LEFT_SIDEBAR_WIDTH }}
      data-testid="left-sidebar"
    >
      {/* Logo/Brand area — click toggles workspace explorer */}
      <button
        className="h-10 flex items-center justify-center border-b border-[var(--color-border)] w-full hover:bg-[var(--color-hover)] transition-colors"
        onClick={() => setWorkspaceExplorerCollapsed(!workspaceExplorerCollapsed)}
        title={workspaceExplorerCollapsed ? 'Show workspace explorer' : 'Hide workspace explorer'}
        aria-label="Toggle workspace explorer"
        data-testid="logo-toggle"
      >
        <SwarmAILogo />
      </button>

      {/* Navigation icons — B ordering (2026-07-12): 4 groups top-to-bottom,
          most-active tool first. 做事 → 能力 → 观测 → 知识. Each button carries
          its group's `accent` (→ inline --ac) for B2 group-tint hover/active. */}
      <nav className="flex-1 pt-2.5 pb-1 space-y-1.5 overflow-y-auto flex flex-col items-center" data-testid="nav-icons">
        {/* 做事 — Integrated terminal (also reachable via ⌘` + explorer right-click).
            isActive reflects real panel open-state; onClick shares the SAME
            togglePanel so all three entries stay in sync. Placed first: it is the
            primary active tool, not a config surface. */}
        <NavIconButton
          icon="terminal"
          label="Terminal (⌘`)"
          accent={NAV_GROUP_COLOR.do}
          isActive={terminalPanelOpen}
          onClick={toggleTerminal}
          data-testid="nav-terminal"
        />

        <NavGroupSeparator />

        {/* 能力 — Tools (Skills, MCP Servers) */}
        {toolItems.map((item) => (
          <NavIconButton
            key={item.target}
            icon={item.icon}
            label={item.label}
            accent={NAV_GROUP_COLOR.power}
            isActive={activeModal === 'settings' && settingsTab === (item.target === 'mcp' ? 'mcp-servers' : item.target)}
            onClick={() => handleNavClick(item.target)}
            data-testid={`nav-${item.target}`}
          />
        ))}

        <NavGroupSeparator />

        {/* 观测 — Insights (Code Intelligence, Engine Metrics, OS Eval) */}
        <NavIconButton
          icon="psychology"
          label="Brain Hub"
          accent={NAV_GROUP_COLOR.observe}
          onClick={() => window.dispatchEvent(new CustomEvent('swarm:show-brain-hub'))}
          data-testid="nav-brain-hub"
        />
        <NavIconButton
          icon="graph"
          label="Code Intelligence"
          accent={NAV_GROUP_COLOR.observe}
          onClick={handleCodeIntelClick}
          data-testid="nav-code-intel"
        />
        <NavIconButton
          icon="activity"
          label="Engine Metrics"
          accent={NAV_GROUP_COLOR.observe}
          isActive={activeModal === 'settings' && settingsTab === 'engine'}
          onClick={() => handleNavClick('engine')}
          data-testid="nav-engine"
        />
        <NavIconButton
          icon="heartbeat"
          label="OS Eval"
          accent={NAV_GROUP_COLOR.observe}
          isActive={activeModal === 'eval'}
          onClick={() => {
            if (activeModal === 'eval') {
              closeModal();
            } else {
              openModal('eval');
            }
          }}
          data-testid="nav-eval"
        />

        <NavGroupSeparator />

        {/* 知识 — Knowledge (Memory, Signals) */}
        <NavIconButton
          icon="book"
          label="Memory"
          accent={NAV_GROUP_COLOR.know}
          onClick={handleMemoryClick}
          data-testid="nav-memory"
        />
        <NavIconButton
          icon="radio"
          label="Signals"
          accent={NAV_GROUP_COLOR.know}
          onClick={handleSignalsClick}
          data-testid="nav-signals"
        />
      </nav>

      {/* Bottom section - Settings and GitHub */}
      <div className="pt-1.5 pb-2 border-t border-[var(--color-border)] space-y-1 flex flex-col items-center">
        <NavIconButton
          icon="gear"
          label="Settings"
          isActive={activeModal === 'settings' && !settingsTab}
          onClick={() => {
            if (activeModal === 'settings') {
              closeModal();
            } else {
              setSettingsTab(undefined);
              openModal('settings');
            }
          }}
          data-testid="nav-settings"
        />
        <a
          href="https://github.com/xg-gh-25/SwarmAI.git"
          title="GitHub"
          className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] cursor-pointer"
          data-testid="github-link"
          onClick={(e) => {
            e.preventDefault();
            openExternal('https://github.com/xg-gh-25/SwarmAI.git');
          }}
        >
          <GitHubIcon className="w-4 h-4" />
        </a>
      </div>
    </aside>
  );
}

// SwarmAI Logo component
function SwarmAILogo() {
  return (
    <div
      className="w-[26px] h-[26px] rounded-md flex items-center justify-center overflow-hidden"
      title="SwarmAI"
      data-testid="swarm-logo"
    >
      <img src="/swarm-avatar.svg" alt="SwarmAI" className="w-full h-full object-contain" />
    </div>
  );
}

// Inset group separator between nav groups (B ordering, 2026-07-12).
// data-testid lets the redesign test count groups (4 groups → 3 separators).
function NavGroupSeparator() {
  return (
    <div
      className="w-4 my-1 border-t border-[var(--color-border)]"
      aria-hidden="true"
      data-testid="nav-group-sep"
    />
  );
}

// Navigation icon button component
interface NavIconButtonProps {
  icon: string;
  label: string;
  isActive?: boolean;
  onClick?: () => void;
  /** Group brand color (B2 group-tint). Drives hover/active bg+ring+bar via the
   *  inline `--ac` CSS custom property. Defaults to the accent primary. */
  accent?: string;
  'data-testid'?: string;
}

/** SVG stroke icon lookup — AC6: replace Material Symbols with inline SVGs. */
function NavSvgIcon({ name }: { name: string }) {
  const svgProps = {
    width: 19,
    height: 19,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  switch (name) {
    case 'lightning':
      return (
        <svg {...svgProps} aria-hidden="true">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
      );
    case 'server':
      return (
        <svg {...svgProps} aria-hidden="true">
          <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
          <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
          <line x1="6" y1="6" x2="6.01" y2="6" />
          <line x1="6" y1="18" x2="6.01" y2="18" />
        </svg>
      );
    case 'graph':
      // Hub-and-spoke network for Code Intelligence — one central node linked
      // to 3 satellites at even 120° spacing (top, lower-left, lower-right),
      // equal radius, spokes snapped to satellite centers. Reads as a balanced
      // radial graph at 19px.
      return (
        <svg {...svgProps} aria-hidden="true">
          <line x1="12" y1="12" x2="12" y2="5" />
          <line x1="12" y1="12" x2="18" y2="15.5" />
          <line x1="12" y1="12" x2="6" y2="15.5" />
          <circle cx="12" cy="12" r="2.4" />
          <circle cx="12" cy="5" r="1.9" />
          <circle cx="18" cy="15.5" r="1.9" />
          <circle cx="6" cy="15.5" r="1.9" />
        </svg>
      );
    case 'activity':
      // Single centered pulse line for Engine Metrics — symmetric baseline
      // with one spike, no off-center kink.
      return (
        <svg {...svgProps} aria-hidden="true">
          <polyline points="2 12 7 12 10 5 14 19 17 12 22 12" />
        </svg>
      );
    case 'heartbeat':
      // Heart with a clean pulse notch for OS Eval — pulse sits inside the
      // heart instead of overlapping a second full-width polyline.
      return (
        <svg {...svgProps} aria-hidden="true">
          <path d="M19.5 12.6 12 20l-7.5-7.4a4.6 4.6 0 0 1 6.5-6.5l1 1 1-1a4.6 4.6 0 0 1 6.5 6.5z" />
          <polyline points="6.5 12.5 9.5 12.5 11 9.8 13 14.5 14.5 12.5 17.5 12.5" />
        </svg>
      );
    case 'book':
      // Memory — book with a bookmark ribbon hanging from the top edge (2026-07-12
      // tweak: the ribbon reads as "saved knowledge" and disambiguates from a plain
      // notebook at 19px).
      return (
        <svg {...svgProps} aria-hidden="true">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          <path d="M11 2v8l3-2.2L17 10V2" />
        </svg>
      );
    case 'radio':
      // Signals — symmetric broadcast waves radiating from a center dot (2026-07-12
      // tweak: replaced the off-balance antenna arcs with a clean concentric-wave
      // pair on each side, mirrored around the dot — reads clearly at 19px).
      return (
        <svg {...svgProps} aria-hidden="true">
          <circle cx="12" cy="12" r="1.6" />
          <path d="M8.5 8.5a5 5 0 0 0 0 7" />
          <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          <path d="M5.6 5.6a9 9 0 0 0 0 12.8" />
          <path d="M18.4 5.6a9 9 0 0 1 0 12.8" />
        </svg>
      );
    case 'terminal':
      // Terminal — window frame with a `>` prompt caret and command line.
      return (
        <svg {...svgProps} aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="2" ry="2" />
          <polyline points="7 9 10 12 7 15" />
          <line x1="12.5" y1="15" x2="17" y2="15" />
        </svg>
      );
    case 'tune':
    case 'gear':
      return (
        <svg {...svgProps} aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
    default:
      // Fallback to material-symbols for unknown icons
      return <span className="material-symbols-outlined text-[18px]">{name}</span>;
  }
}

function NavIconButton({ icon, label, isActive, onClick, accent, 'data-testid': testId }: NavIconButtonProps) {
  // Group tint (B2): expose the group color as --ac; the .nav-btn CSS reads it
  // for hover/active bg+ring+icon+bar. Omitted accent → CSS falls back to the
  // accent primary (footer buttons: Settings, GitHub-as-button use no accent).
  const style = accent ? ({ '--ac': accent } as CSSProperties) : undefined;
  // Accent-bearing (nav-group) buttons are toned by DEFAULT via .nav-btn--tinted;
  // footer buttons (no accent) stay neutral grey until hover.
  const tinted = accent ? ' nav-btn--tinted' : '';
  return (
    <button
      onClick={onClick}
      title={label}
      data-testid={testId}
      aria-pressed={isActive}
      style={style}
      className={`nav-btn${tinted} relative flex items-center justify-center w-8 h-8 rounded-lg`}
    >
      {/* Active indicator bar — always present (layout-shift-free, GUI10):
          visible only when active, transparent otherwise. Color from --ac. */}
      <span
        data-testid="active-bar"
        aria-hidden="true"
        className={`nav-active-bar pointer-events-none absolute left-0 top-1/2 -translate-y-1/2 h-4 w-[3px] rounded-r-full transition-opacity ${
          isActive ? 'opacity-100' : 'opacity-0'
        }`}
      />
      <NavSvgIcon name={icon} />
    </button>
  );
}

// GitHub SVG icon component
function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" />
    </svg>
  );
}

// Main Chat Panel with drop zone and context bar
interface MainChatPanelProps {
  children: ReactNode;
}

function MainChatPanel({ children }: MainChatPanelProps) {
  return (
    <main
      className="flex-1 overflow-hidden bg-[var(--color-bg)] flex flex-col"
      style={{ minWidth: MIN_MAIN_CHAT_PANEL_WIDTH }}
    >
      {children}
    </main>
  );
}

/** Invisible bridge that captures refreshTree from ExplorerContext into a ref
 *  so that code outside the provider (e.g. FileEditorModal save handler) can
 *  trigger a tree refresh. */
function RefreshTreeBridge({ refreshTreeRef }: { refreshTreeRef: React.MutableRefObject<(() => void) | null> }) {
  const { refreshTree } = useTreeData();
  refreshTreeRef.current = refreshTree;
  return null;
}

// Inner layout component that uses the context
function ThreeColumnLayoutInner({ children }: ThreeColumnLayoutProps) {
  const { activeModal, closeModal, workspaceSettingsId, settingsTab, openModal, setSettingsTab } = useLayout();
  const { addToast } = useToast();

  // CredentialBanner is mounted at the app root (outside LayoutProvider), so its
  // "Open Settings" deep-link can't call setSettingsTab directly — it dispatches
  // OPEN_SETTINGS_EVENT, which we handle here (inside the provider that owns the
  // settings modal). Harmless no-op if this shell isn't mounted (onboarding).
  useEffect(() => {
    const onOpenSettings = (e: Event) => {
      const tab = (e as CustomEvent<{ tab?: string }>).detail?.tab;
      setSettingsTab(tab);
      openModal('settings');
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, onOpenSettings);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, onOpenSettings);
  }, [openModal, setSettingsTab]);

  // Integrated terminal: global Ctrl/Cmd-` toggle (AC5). The panel is now always
  // mounted and self-hides on panelOpen, so this scope no longer needs the
  // panelOpen flag for a conditional render — only the toggle + cwd-open.
  const { togglePanel: toggleTerminal, openTerminal } = useTerminal();
  useTerminalHotkey(toggleTerminal);

  // Explorer right-click "Open terminal here" → open a terminal cwd'd into the
  // directory (AC3). Bridged via a window event (same idiom as attach/ask).
  useEffect(() => {
    const onOpenTerminal = (e: Event) => {
      const detail = (e as CustomEvent<{ path?: string }>).detail;
      if (detail?.path) openTerminal({ cwd: detail.path });
    };
    window.addEventListener(EXPLORER_OPEN_TERMINAL, onOpenTerminal);
    return () => window.removeEventListener(EXPLORER_OPEN_TERMINAL, onOpenTerminal);
  }, [openTerminal]);

  /** Ref to hold the ExplorerContext refreshTree function (set by bridge component inside provider). */
  const refreshTreeRef = useRef<(() => void) | null>(null);

  // File viewer state — unified for all file types (Requirement 9.1)
  // editorMode: 'panel' = side panel (default), 'modal' = fullscreen overlay (text files only)
  const [editorMode, setEditorMode] = useState<'panel' | 'modal'>('panel');
  const [fileViewerFile, setFileViewerFile] = useState<{
    filePath: string;
    fileName: string;
    gitStatus?: GitStatus;
    workspaceId?: string;
    autoDiff?: boolean;
  } | null>(null);

  // Legacy file editor state — kept for modal mode (fullscreen text editing only)
  const [fileEditorState, setFileEditorState] = useState<{
    isOpen: boolean;
    filePath: string;
    fileName: string;
    workspaceId: string;
    content: string;
    isSwarmWorkspace: boolean;
    gitStatus?: GitStatus;
    readonly?: boolean;
    committedContent?: string;
  } | null>(null);

  // Swarm workspace warning state - Requirement 4.3
  const [swarmWarning, setSwarmWarning] = useState<{
    isOpen: boolean;
    pendingFile: FileTreeItem | null;
  }>({ isOpen: false, pendingFile: null });

  // Listen for swarm:open-file custom events dispatched by clickable file paths
  // in chat messages (MarkdownRenderer). Uses a ref to avoid stale closure on
  // handleFileDoubleClick which depends on external state.

  // Notify RadarSidebar when file viewer panel is open/closed so it can auto-hide
  const isEditorPanelOpen = !!(fileViewerFile && editorMode === 'panel');
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-panel-state', {
      detail: { open: isEditorPanelOpen },
    }));
  }, [isEditorPanelOpen]);

  // Notify ChatPage which file is currently open so it can include in chat requests.
  // Memoize the detail to avoid dispatching redundant null→null events.
  const editorFileDetail = useMemo(
    () => fileViewerFile
      ? { filePath: fileViewerFile.filePath, fileName: fileViewerFile.fileName }
      : null,
    [fileViewerFile?.filePath, fileViewerFile?.fileName], // eslint-disable-line react-hooks/exhaustive-deps -- intentional subset deps
  );
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-file-changed', {
      detail: editorFileDetail,
    }));
  }, [editorFileDetail]);

  // Ref for file open routing — assigned after handleFileDoubleClick is defined below
  const handleFileDoubleClickRef = useRef<(file: FileTreeItem, autoDiff?: boolean) => Promise<void>>(null!);


  // Handle file double-click — unified routing through FileViewer (Requirement 9.1)
  // `autoDiff` (optional) opens the file directly on its diff view — set here in
  // the SAME setState as the rest of fileViewerFile so there is no post-await
  // read-stale-`prev` race (handleFileDoubleClick is sync; a follow-up
  // setFileViewerFile(prev=>…) would read the pre-flush prev and drop the flag).
  const handleFileDoubleClick = useCallback(async (file: FileTreeItem, autoDiff?: boolean) => {
    if (file.isSwarmWorkspace) {
      setSwarmWarning({ isOpen: true, pendingFile: file });
      return;
    }

    // All file types route through the unified FileViewer panel.
    // FileViewer internally classifies and picks the right renderer.
    setFileViewerFile({
      filePath: file.path,
      fileName: file.name,
      gitStatus: file.gitStatus,
      workspaceId: file.workspaceId,
      autoDiff: autoDiff || undefined,
    });
    // Reset modal mode — FileViewer always starts in panel
    setEditorMode('panel');
  }, []);

  // Assign ref now that handleFileDoubleClick is defined
  handleFileDoubleClickRef.current = handleFileDoubleClick;

  // Listen for swarm:open-file events from clickable file paths in chat.
  // Paths from chat may be relative to source repos, not the workspace root.
  // We call /workspace/file/resolve first to find the actual workspace path.
  useEffect(() => {
    let mounted = true;

    const handleOpenFileEvent = async (e: Event) => {
      const { path: filePath, autoDiff } = (e as CustomEvent<{ path: string; autoDiff?: boolean }>).detail ?? {};
      if (!filePath) return;

      let resolvedPath = filePath;
      try {
        // Resolve partial/codebase-relative paths to workspace-relative paths
        const resp = await api.get<{ resolved_path: string }>(
          '/workspace/file/resolve',
          { params: { path: filePath } },
        );
        if (!mounted) return;
        resolvedPath = resp.data.resolved_path;
      } catch (err: unknown) {
        if (!mounted) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 400) {
          // Path traversal or truly invalid — don't fall through
          addToast({ severity: 'warning', message: `Cannot open file: ${filePath}`, autoDismiss: true });
          return;
        }
        // 404 = not found in workspace, fall through to try the raw path.
        // Non-404 errors (network timeout, 500) are logged for debugging.
        if (status !== undefined && status !== 404) {
          console.warn('[swarm:open-file] resolve failed:', status, err);
        }
      }

      const fileName = resolvedPath.split('/').pop() || resolvedPath;
      const fileItem: FileTreeItem = {
        id: resolvedPath,
        name: fileName,
        type: 'file',
        path: resolvedPath,
        workspaceId: '',
        workspaceName: '',
      };

      // Route through handleFileDoubleClick for proper file type handling
      // (images preview inline, binary files show info modal, text opens editor).
      // autoDiff (Radar ✍ Changes click) is threaded INTO the call so it lands
      // in the single fileViewerFile setState — no post-await stale-prev race.
      try {
        await handleFileDoubleClickRef.current(fileItem, autoDiff);
      } catch {
        if (!mounted) return;
        addToast({
          severity: 'warning',
          message: `Could not open file: ${filePath}`,
          autoDismiss: true,
        });
      }
    };

    document.addEventListener('swarm:open-file', handleOpenFileEvent);
    return () => {
      mounted = false;
      document.removeEventListener('swarm:open-file', handleOpenFileEvent);
    };
  }, [addToast]);

  // Keyboard shortcuts: Cmd+O (open file), Cmd+Shift+C (copy active file path)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMeta = e.metaKey || e.ctrlKey;

      // Cmd+O — Open File dialog
      if (isMeta && e.key === 'o' && !e.shiftKey) {
        e.preventDefault();
        // Trigger OpenFileButton click via custom event (component handles dialog)
        document.dispatchEvent(new CustomEvent('swarm:open-file-dialog'));
      }

      // Cmd+Shift+P — Copy active file path (avoids DevTools conflict with Cmd+Shift+C)
      if (isMeta && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault();
        if (fileViewerFile?.filePath) {
          import('../../utils/clipboard').then(({ copyToClipboard }) => {
            copyToClipboard(fileViewerFile.filePath);
          });
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [fileViewerFile?.filePath]);

  // Handle Swarm workspace warning confirmation
  const handleSwarmWarningConfirm = useCallback(async () => {
    if (swarmWarning.pendingFile) {
      setFileViewerFile({
        filePath: swarmWarning.pendingFile.path,
        fileName: swarmWarning.pendingFile.name,
        gitStatus: swarmWarning.pendingFile.gitStatus,
        workspaceId: swarmWarning.pendingFile.workspaceId,
      });
    }
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, [swarmWarning.pendingFile]);

  const handleSwarmWarningCancel = useCallback(() => {
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, []);

  // Handle file save - Requirement 9.6
  const handleFileSave = useCallback(async (content: string) => {
    if (!fileEditorState) return;

    try {
      await api.put('/workspace/file', { content }, {
        params: { path: fileEditorState.filePath },
      });
      refreshTreeRef.current?.();
    } catch (error) {
      console.error('Failed to save file:', error);
      throw error;
    }
  }, [fileEditorState]);

  // Handle file viewer close - Requirement 9.7
  const handleFileViewerClose = useCallback(() => {
    setFileViewerFile(null);
    setFileEditorState(null); // Clear legacy state too
    setEditorMode('panel'); // Reset to panel for next open
    liveContentRef.current = null;
    refreshTreeRef.current?.();
  }, []);

  // Track live content in a ref (NOT state) so mode toggle preserves edits
  // without triggering re-renders or resetting FileEditorCore's useEffect.
  const liveContentRef = useRef<string | null>(null);

  const handleContentChange = useCallback((newContent: string) => {
    liveContentRef.current = newContent;
  }, []);

  // Toggle between panel and modal mode (preserves file state).
  // Panel→modal: populate fileEditorState from fileViewerFile for FileEditorModal.
  // Modal→panel: clear fileEditorState, let FileViewer take over.
  const handleToggleEditorMode = useCallback(async () => {
    if (editorMode === 'panel' && fileViewerFile) {
      // Panel → Modal: need to populate legacy fileEditorState for FileEditorModal
      try {
        const response = await api.get<{ content: string; path: string; name: string; readonly?: boolean }>(
          '/workspace/file',
          { params: { path: fileViewerFile.filePath } },
        );
        let committedContent: string | undefined;
        try {
          const cResp = await api.get<{ content: string }>(
            '/workspace/file/committed',
            { params: { path: fileViewerFile.filePath } },
          );
          committedContent = cResp.data.content;
        } catch { /* untracked file */ }

        const content = liveContentRef.current ?? response.data.content;
        setFileEditorState({
          isOpen: true,
          filePath: fileViewerFile.filePath,
          fileName: fileViewerFile.fileName,
          workspaceId: fileViewerFile.workspaceId ?? '',
          content,
          isSwarmWorkspace: false,
          gitStatus: fileViewerFile.gitStatus,
          readonly: response.data.readonly,
          committedContent,
        });
      } catch (err) {
        console.error('Failed to switch to modal mode:', err);
        return;
      }
    } else if (editorMode === 'modal' && fileEditorState) {
      // Modal → Panel: snapshot live content, clear legacy state
      if (liveContentRef.current != null) {
        // Content preserved via liveContentRef — FileViewer will re-fetch
      }
      setFileEditorState(null);
    }
    liveContentRef.current = null;
    setEditorMode((prev) => (prev === 'panel' ? 'modal' : 'panel'));
  }, [editorMode, fileViewerFile, fileEditorState]);

  // L2: Auto-diff feedback — inject edit summary into chat input.
  // Accepts fileName as a parameter to avoid stale-closure reads of
  // fileEditorState (which may be nulled if the editor closes during
  // the async diff fetch).
  const handleSaveWithDiff = useCallback((diffSummary: string, savedFileName?: string) => {
    const fileName = savedFileName ?? fileEditorState?.fileName ?? 'file';
    const text = `I edited \`${fileName}\`:\n${diffSummary}\n\nPlease revise the doc to align with these changes.`;
    window.dispatchEvent(new CustomEvent('swarm:inject-chat-input', {
      detail: { text, focus: true },
    }));
  }, [fileEditorState?.fileName]);

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[var(--color-bg)]">
      <ExplorerProvider>
        <RefreshTreeBridge refreshTreeRef={refreshTreeRef} />

        {/* Top bar -- session context, draggable */}
        <TopBar />

        {/* Main layout below top bar — min-h-0 overrides flex auto-min-height
            (which uses intrinsic content size and prevents shrinking). Without this,
            AutoSizer can't get a resolved height from the flex algorithm. */}
        <div className="flex flex-1 overflow-hidden min-h-0">
          <LeftSidebar />
          <WorkspaceExplorer onFileDoubleClick={handleFileDoubleClick} />
          <MainChatPanel>{children}</MainChatPanel>
          {/* Unified File Viewer — resizable side panel for all file types */}
          {fileViewerFile && editorMode === 'panel' && (
            <FileViewerPanel
              initialFile={fileViewerFile}
              onClose={handleFileViewerClose}
              onSaveWithDiff={handleSaveWithDiff}
              onToggleMode={handleToggleEditorMode}
            />
          )}
        </div>

        {/* Integrated terminal panel — flex sibling BELOW chat, ABOVE the status
            bar (Gate-1 C3: shrinks chat by its height, never overlays). ALWAYS
            mounted; it self-hides via display:none when collapsed (see
            TerminalPanel's own panelOpen style). Mounted-not-conditional so
            collapse/reopen preserves xterm scrollback + live PTYs instead of
            unmount → term.dispose() → history loss. The Ctrl/Cmd-` hotkey / ▾
            button flip panelOpen, which drives the self-hide. */}
        <TerminalPanel />

        {/* Bottom status bar */}
        <BottomBar />
      </ExplorerProvider>

      {/* File Editor Modal — fullscreen overlay mode (text files only, via toggle) */}
      {fileEditorState && editorMode === 'modal' && (
        <FileEditorModal
          isOpen={fileEditorState.isOpen}
          filePath={fileEditorState.filePath}
          fileName={fileEditorState.fileName}
          workspaceId={fileEditorState.workspaceId}
          initialContent={fileEditorState.content}
          onSave={handleFileSave}
          onClose={handleFileViewerClose}
          gitStatus={fileEditorState.gitStatus}
          readonly={fileEditorState.readonly}
          committedContent={fileEditorState.committedContent}
          onToggleMode={handleToggleEditorMode}
          onSaveWithDiff={handleSaveWithDiff}
          onContentChange={handleContentChange}
        />
      )}

      {/* Swarm Workspace Warning Dialog */}
      <SwarmWorkspaceWarningDialog
        isOpen={swarmWarning.isOpen}
        action="edit"
        fileName={swarmWarning.pendingFile?.name}
        onConfirm={handleSwarmWarningConfirm}
        onCancel={handleSwarmWarningCancel}
      />

      {/* Management Page Modals */}
      {/* Skills and MCP now integrated into Settings tabs — standalone modals removed */}
      <SettingsModal isOpen={activeModal === 'settings'} onClose={closeModal} initialTab={settingsTab} />
      <WorkspaceSettingsModal
        isOpen={activeModal === 'workspace-settings'}
        onClose={closeModal}
        workspaceId={workspaceSettingsId}
      />
      <EvalModal isOpen={activeModal === 'eval'} onClose={closeModal} />
      {/* Brain Hub demo overlay — self-contained, listens for swarm:show-brain-hub (nav-brain-hub) */}
      <BrainHubDemoOverlay />
    </div>
  );
}

// Main component that wraps with LayoutProvider
export default function ThreeColumnLayout({ children }: ThreeColumnLayoutProps) {
  return (
    <LayoutProvider>
      <TerminalProvider>
        <ThreeColumnLayoutInner>{children}</ThreeColumnLayoutInner>
      </TerminalProvider>
    </LayoutProvider>
  );
}

// Export sub-components for potential reuse
export { TopBar, LeftSidebar, WorkspaceExplorer, MainChatPanel, NavIconButton, GitHubIcon, SwarmAILogo };
export { LEFT_SIDEBAR_WIDTH, MIN_MAIN_CHAT_PANEL_WIDTH };
