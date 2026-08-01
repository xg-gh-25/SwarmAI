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
import { SwarmWSOverlay } from './SwarmWSOverlay';
import { DomainStubOverlays } from './DomainStubOverlays';
import { CMBrainOverlay } from './CMBrainOverlay';
import { setNavSource, clearNavSource } from './navSource';
import { useActiveOverlayEvent, clearActiveOverlayEvent } from './useExclusiveOverlay';
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

// A10 group tint (muted, Radar-like) — Cognitive violet / Work green / System grey.
const A10_GROUP = {
  cognitive: '#5fc99a',  // 认知区专属薄荷绿 (run_b57266d2) — 认知区是核心差异化，绿标记它
  work: '#4a8fb0',       // 青蓝 — 从森林绿改，消除与认知区绿的撞色
  system: '#7c8194',
} as const;

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
  const { activeModal, openModal, closeModal, settingsTab, setSettingsTab } = useLayout();
  const { addToast } = useToast();
  // Which window-event overlay is currently open (or null) — drives the
  // active/selected highlight on the window-event cards (run_ad7b32f6).
  const activeOverlay = useActiveOverlayEvent();
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
      clearActiveOverlayEvent(); // a modal takes over — no window card stays lit
      setSettingsTab(targetTab);
      openModal('settings');
    }
  };

  // Open the LATEST signal digest. The digest is written by a scheduled job, so
  // today's file often doesn't exist yet (esp. early in the day / weekends) —
  // hardcoding `<today>-digest.md` produced a file-not-found (run_a73566c4). We
  // list Knowledge/Signals via the existing tree/expand endpoint and open the
  // newest *-digest.md. Graceful toast on empty/failure — never a dead click.
  const handleSignalsClick = async () => {
    // Opens a file PANEL / toast, not a fullscreen Modal — clear the nav-source so
    // it can't mis-point a later unrelated fullscreen spout (Gate-2 #3).
    clearNavSource();
    clearActiveOverlayEvent(); // a panel takes over — no window card should stay lit
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

  // Open a window-event domain overlay. Also closes any activeModal (Settings/Eval):
  // since the redesigned overlay no longer covers the leftNav, the nav stays
  // clickable while a modal is open, so without this a window overlay would stack
  // on top of a still-open Settings/Eval modal (mirror of the Settings-clears-
  // window-highlight fix; run_ad7b32f6 Gate-1 Finding 2).
  const showOverlay = (event: string) => {
    if (activeModal) closeModal();
    window.dispatchEvent(new CustomEvent(event));
  };

  // Capabilities overlay folds Skills + MCP + jobs (A10). Opening it lands on the
  // Settings modal's skills tab as the concrete surface (Run-2 wiring; a dedicated
  // Capabilities overlay is a later cycle). Engine Metrics → Settings tab (choice A).
  const openCapabilities = () => handleNavClick('skills');

  // Community folds Signals (choice A): the domain card opens the latest signal
  // digest (the community/GitHub surface is external — the card is a soft entry).
  const openCommunity = () => { void handleSignalsClick(); };

  return (
    <aside
      className="bg-[var(--color-bg-chrome)] border-r-2 border-[var(--color-border-strong,var(--color-border))] flex flex-col flex-shrink-0"
      style={{ width: LEFT_SIDEBAR_WIDTH }}
      data-testid="left-sidebar"
    >
      {/* Chat hero — brand logo + label; the primary surface. Click returns to
          chat (closes any open overlay). */}
      <div className="p-2.5 pb-1.5">
        <button
          className="a10-hero relative w-full flex items-center gap-2.5 rounded-xl px-3 py-2.5"
          onClick={() => window.dispatchEvent(new CustomEvent('swarm:back-to-chat'))}
          title="Chat"
          data-testid="chat-hero"
        >
          <span className="flex-shrink-0 w-[26px] h-[26px] rounded-md overflow-hidden flex items-center justify-center">
            <SwarmAILogo />
          </span>
          <span className="text-[13.5px] font-bold text-white flex-1 text-left">Chat</span>
        </button>

        {/* History row — a Chat sub-entry (past conversations), muted vs domain cards. */}
        <button
          className="a10-histrow mt-0.5 w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
          onClick={() => showOverlay('swarm:show-history')}
          title="History"
          data-testid="history-row"
        >
          <span className="w-4 flex items-center justify-center opacity-85"><NavSvgIcon name="history" /></span>
          <span className="flex-1 text-left text-[11.5px] font-mono tracking-wide">History</span>
          <span className="text-[13px] text-[var(--color-text-faint)]">›</span>
        </button>
      </div>

      {/* A10 domain cards. Cognitive = a distinct "green panel" zone (核心差异化 —
          你的大脑); Work/System = plain titled groups. Y/R signal flags only where
          attention is needed (Memory=Y, Brain Hub=Y, OS Eval=R). System titles are
          dimmed (low-frequency OS mechanics, don't compete for attention). */}
      <nav className="flex-1 px-2.5 pb-1 overflow-y-auto" data-testid="nav-icons">
        {/* 认知区 — 绿面板容器：无区头、无 scope 文字，靠视觉 + highlight 分层 */}
        <div className="a10-zone" data-testid="cognition-zone">
          <A10Card icon="layers" label="C&M" tint={A10_GROUP.cognitive} isActive={activeOverlay === 'swarm:show-context'} onClick={() => showOverlay('swarm:show-context')} data-testid="nav-context" />
          <A10Card icon="hub" label="Brain Hub" tint={A10_GROUP.cognitive} flag="y" highlight isActive={activeOverlay === 'swarm:show-brain-hub'} onClick={() => showOverlay('swarm:show-brain-hub')} data-testid="nav-brain-hub" />
          <button
            className="a10-newbrain w-full flex items-center gap-2.5 rounded-[10px] py-1.5 pl-3 pr-2.5 transition-colors"
            style={{ '--ac': A10_GROUP.cognitive } as CSSProperties}
            onClick={() => { clearNavSource(); showOverlay('swarm:show-brain-hub'); }}
            title="New Brain — 建一个新大脑"
            data-testid="nav-new-brain"
          >
            <span className="a10-plus flex-shrink-0 w-[22px] h-[22px] rounded-[7px] flex items-center justify-center">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
            </span>
            <span className="flex-1 text-left text-[12px] font-semibold whitespace-nowrap">New Brain</span>
          </button>
        </div>

        {/* WORK zone (A4): daily-common pair (ToDo + Workspace) first and
            `highlight`ed (resting-brighter) to lift them above the power pair
            (Pipeline + Pollinate). Reuses the existing highlight prop — NOT a 2nd
            green panel: cognition's green zone stays the sole differentiated
            surface (P1). "Workspace" is the de-jargoned label for the SwarmWS
            workspace (testid nav-swarmws + explorer brand title keep SwarmWS). */}
        <A10Group label="Work" tint={A10_GROUP.work}>
          <A10Card icon="todo" label="ToDo" tint={A10_GROUP.work} highlight isActive={activeOverlay === 'swarm:show-todo'} onClick={() => showOverlay('swarm:show-todo')} data-testid="nav-todo" />
          <A10Card icon="folder" label="Workspace" tint={A10_GROUP.work} highlight isActive={activeOverlay === 'swarm:show-swarmws'} onClick={() => showOverlay('swarm:show-swarmws')} data-testid="nav-swarmws" />
          <A10Card icon="pipeline" label="Pipeline" tint={A10_GROUP.work} isActive={activeOverlay === 'swarm:show-pipeline'} onClick={() => showOverlay('swarm:show-pipeline')} data-testid="nav-pipeline" />
          <A10Card icon="hive" label="Pollinate" tint={A10_GROUP.work} isActive={activeOverlay === 'swarm:show-pollinate'} onClick={() => showOverlay('swarm:show-pollinate')} data-testid="nav-pollinate" />
        </A10Group>

        <A10Group label="System" tint={A10_GROUP.system} dimCards>
          <A10Card icon="extension" label="Capabilities" tint={A10_GROUP.system} onClick={openCapabilities} data-testid="nav-capabilities" />
          <A10Card icon="heartbeat" label="OS Eval" tint={A10_GROUP.system} flag="r" isActive={activeModal === 'eval'} onClick={() => { if (activeModal === 'eval') { clearNavSource(); closeModal(); } else { clearActiveOverlayEvent(); openModal('eval'); } }} data-testid="nav-eval" />
          <A10Card icon="gear" label="Settings" tint={A10_GROUP.system} isActive={activeModal === 'settings' && !settingsTab} onClick={() => { if (activeModal === 'settings') { clearNavSource(); closeModal(); } else { clearActiveOverlayEvent(); setSettingsTab(undefined); openModal('settings'); } }} data-testid="nav-settings" />
          <A10Card icon="public" label="Community" tint={A10_GROUP.system} onClick={openCommunity} data-testid="nav-community" />
        </A10Group>
      </nav>

      {/* Footer — GitHub (left) + Terminal (right). */}
      <div className="px-2.5 pt-1.5 pb-2 border-t border-[var(--color-border)] grid grid-cols-2 gap-1.5">
        <a
          href="https://github.com/xg-gh-25/SwarmAI.git"
          title="GitHub"
          className="a10-fcard flex items-center justify-center gap-1.5 rounded-lg py-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] cursor-pointer"
          data-testid="github-link"
          onClick={(e) => { e.preventDefault(); openExternal('https://github.com/xg-gh-25/SwarmAI.git'); }}
        >
          <GitHubIcon className="w-4 h-4" />
        </a>
        <button
          className="a10-fcard flex items-center justify-center gap-1.5 rounded-lg py-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          title="Terminal (⌘`)"
          onClick={toggleTerminal}
          aria-pressed={terminalPanelOpen}
          data-testid="nav-terminal"
        >
          <NavSvgIcon name="terminal" />
        </button>
      </div>
    </aside>
  );
}

// SwarmAI Logo — inline S-monogram honeycomb (matches desktop/src/assets/
// swarm-avatar.svg). Inlined (not <img src>) so it renders with zero network
// dependency and is assertable in tests (a bare <img> is not an <svg>).
function SwarmAILogo() {
  return (
    <span
      className="w-[26px] h-[26px] rounded-md flex items-center justify-center overflow-hidden"
      title="SwarmAI"
      data-testid="swarm-logo"
    >
      <svg viewBox="0 0 200 200" width="100%" height="100%" aria-label="SwarmAI">
        <defs>
          <linearGradient id="swarmHexNav" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#fde047" />
            <stop offset="55%" stopColor="#f59e0b" />
            <stop offset="100%" stopColor="#d97706" />
          </linearGradient>
        </defs>
        <polygon points="100,30 162,65 162,135 100,170 38,135 38,65" fill="url(#swarmHexNav)" />
        <path
          d="M128 74 C128 60 114 54 100 54 C84 54 73 63 73 76 C73 88 84 92 100 95 C116 98 127 102 127 114 C127 127 115 133 100 133 C86 133 73 127 72 114"
          fill="none"
          stroke="#101527"
          strokeWidth="15"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

// Navigation icon button component

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
    case 'layers':
      // Context — stacked layers (what's loaded into the prompt right now).
      return (
        <svg {...svgProps} aria-hidden="true">
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
      );
    case 'hub':
      // Brain Hub — central node with 3 linked satellites (DDD brains).
      return (
        <svg {...svgProps} aria-hidden="true">
          <circle cx="12" cy="12" r="2.5" />
          <circle cx="12" cy="4" r="1.9" />
          <circle cx="5" cy="18" r="1.9" />
          <circle cx="19" cy="18" r="1.9" />
          <line x1="12" y1="6.5" x2="12" y2="9.5" />
          <line x1="10.3" y1="13.6" x2="6.5" y2="16.4" />
          <line x1="13.7" y1="13.6" x2="17.5" y2="16.4" />
        </svg>
      );
    case 'todo':
      // ToDo — a checklist: a card with a check mark (queued work you own).
      return (
        <svg {...svgProps} aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="2.5" />
          <path d="M8 12l2.5 2.5L16 9" />
        </svg>
      );
    case 'pipeline':
      // Pipeline — two stages linked by a flow arrow (code delivery).
      return (
        <svg {...svgProps} aria-hidden="true">
          <rect x="2" y="4" width="7" height="7" rx="1.5" />
          <rect x="15" y="13" width="7" height="7" rx="1.5" />
          <path d="M5.5 11v3a2.5 2.5 0 0 0 2.5 2.5h7" />
        </svg>
      );
    case 'hive':
      // Pollinate — hexagon honeycomb cell with a center (media out).
      return (
        <svg {...svgProps} aria-hidden="true">
          <polygon points="12 2 20 7 20 17 12 22 4 17 4 7 12 2" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case 'folder':
      // SwarmWS — workspace folder.
      return (
        <svg {...svgProps} aria-hidden="true">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
        </svg>
      );
    case 'extension':
      // Capabilities — puzzle/extension piece (skills + MCP + jobs).
      return (
        <svg {...svgProps} aria-hidden="true">
          <path d="M6 4h5V3a2 2 0 0 1 4 0v1h3a1 1 0 0 1 1 1v3h1a2 2 0 0 1 0 4h-1v3a1 1 0 0 1-1 1h-3v1a2 2 0 0 1-4 0v-1H6a1 1 0 0 1-1-1v-3H4a2 2 0 0 1 0-4h1V5a1 1 0 0 1 1-1z" />
        </svg>
      );
    case 'public':
      // Community — globe (external GitHub domain).
      return (
        <svg {...svgProps} aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
        </svg>
      );
    case 'history':
      // History — clock with a counter-clockwise arrow (past conversations).
      return (
        <svg {...svgProps} aria-hidden="true">
          <path d="M3 3v5h5" />
          <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
          <path d="M12 7v5l4 2" />
        </svg>
      );
    default:
      // Fallback to material-symbols for unknown icons
      return <span className="material-symbols-outlined text-[18px]">{name}</span>;
  }
}

// ── A10 row-card nav (run_1aab916c) ──────────────────────────────────────────

/** Titled, color-coded group divider: ── Label ── + a muted accent spine.
 *  `tint` colors the label + the right-edge spine per region.
 *  `dimCards` mutes the card titles/icons in this group (System — low-frequency OS
 *  mechanics that shouldn't compete with Chat/Cognitive/Work for attention). */
function A10Group({ label, tint, dimCards, children }: { label: string; tint: string; dimCards?: boolean; children: ReactNode }) {
  return (
    <div className={`a10-group relative pb-2${dimCards ? ' a10-group--dim' : ''}`} style={{ '--gc': tint } as CSSProperties}>
      <div
        className="flex items-center gap-2 px-1 pt-2.5 pb-1.5 text-[8.5px] font-bold font-mono uppercase tracking-[0.18em]"
        style={{ color: tint }}
        data-testid="navgroup-label"
      >
        <span className="flex-1 h-px" style={{ background: `linear-gradient(90deg,transparent,${tint},transparent)`, opacity: 0.4 }} />
        {label}
        <span className="flex-1 h-px" style={{ background: `linear-gradient(90deg,transparent,${tint},transparent)`, opacity: 0.4 }} />
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

interface A10CardProps {
  icon: string;
  label: string;
  tint: string;
  flag?: 'y' | 'r';
  isActive?: boolean;
  /** Highlight the card in its resting state (brighter than siblings) — marks a
   *  primary entry like Brain Hub within the cognition zone. */
  highlight?: boolean;
  onClick?: () => void;
  'data-testid'?: string;
}

/** A10 domain row-card: [chip icon] label …… [Y/R flag]. Title never truncates;
 *  the attention flag is a corner badge (never eats the title). */
function A10Card({ icon, label, tint, flag, isActive, highlight, onClick, 'data-testid': testId }: A10CardProps) {
  // Publish THIS card's on-screen position as the shared spit-out origin BEFORE
  // delegating — so both the window-event overlays and the activeModal modals
  // open from this card (single injection point, run_2e6d6029 / Gate-1).
  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    setNavSource(e.currentTarget.getBoundingClientRect(), tint);
    onClick?.();
  };
  return (
    <button
      onClick={handleClick}
      title={label}
      data-testid={testId}
      aria-pressed={isActive}
      style={{ '--ac': tint } as CSSProperties}
      className={`a10-card${isActive ? ' a10-card--active' : ''}${highlight ? ' a10-card--hilite' : ''} relative w-full flex items-center gap-2.5 rounded-[11px] pl-2 pr-2.5 py-2`}
    >
      <span className="a10-chip flex-shrink-0 w-[29px] h-[29px] rounded-[9px] flex items-center justify-center">
        <NavSvgIcon name={icon} />
      </span>
      <span className="flex-1 text-left text-[12.5px] font-semibold text-[var(--color-text)] leading-tight whitespace-nowrap">{label}</span>
      {flag === 'y' && (
        <span className="a10-flag a10-flag--y" data-testid="flag-y" aria-label="needs attention">Y</span>
      )}
      {flag === 'r' && (
        <span className="a10-flag a10-flag--r" data-testid="flag-r" aria-label="action required">R</span>
      )}
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
          {/* A10 redesign: the workspace explorer is no longer an always-on
              column — it opens on demand as SwarmWSOverlay (below). */}
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

        {/* SwarmWS explorer overlay — on-demand fullscreen (A10 redesign).
            Kept inside ExplorerProvider so it reads the same live tree state
            (the 30s ETag poll lives in the provider, runs whether or not the
            overlay is open). Opening a file self-closes the overlay first
            (Gate-1 z-index fix) then delegates to handleFileDoubleClick. */}
        <SwarmWSOverlay onFileDoubleClick={handleFileDoubleClick} />

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

      {/* C&M Global Brain overlay — real surface for the Context nav card
          (swarm:show-context). Replaces the former Context stub (run_5f7d4fe1). */}
      <CMBrainOverlay />

      {/* A10 domain stub overlays — Pipeline / Pollinate open labeled
          placeholders (real surfaces land in later per-card cycles). */}
      <DomainStubOverlays />
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
export { TopBar, LeftSidebar, WorkspaceExplorer, MainChatPanel, GitHubIcon, SwarmAILogo };
export { LEFT_SIDEBAR_WIDTH, MIN_MAIN_CHAT_PANEL_WIDTH };
