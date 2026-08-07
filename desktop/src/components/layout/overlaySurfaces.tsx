/**
 * overlaySurfaces — registers each fullscreen surface with the overlayRegistry
 * (OverlayHost subsystem, design 2026-08-04). Importing this module for its side
 * effect (done once by ThreeColumnLayout) populates the registry before OverlayHost
 * first reads it.
 *
 * M2 registers ONE pilot — brain-hub — to prove the host end-to-end (live zoom
 * verify). M3 adds the remaining non-workbench surfaces; M4 adds the workbench four
 * via OverlayShell. Each entry is pure data + a content render fn; the host owns all
 * chrome/geometry.
 */
import { lazy } from 'react';
import { registerOverlay } from './overlayRegistry';
import { WorkspaceExplorer } from '../workspace-explorer';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { CMBrainContent } from './CMBrainOverlay';
import { LibraryContent } from './LibraryOverlay';
import { NewBrainContent } from './NewBrainOverlay';
import { HistoryContent } from './HistoryOverlay';
import { ToDoContent } from './ToDoOverlay';
import { NeedYouContent } from './NeedYouOverlay';
import { JobsRunsContent } from './JobsRunsOverlay';
import { CapabilitiesContent } from './CapabilitiesOverlay';
import { PipelineContent } from './PipelineOverlay';
import { PollinateContent } from './PollinateOverlay';
import { HiveFleetContent } from './HiveFleetOverlay';
import { useLayout } from '../../contexts/LayoutContext';
import type { ToDo } from '../../types/todo';

// G2 code-split (run_06c49540, MEASURED): the initial bundle was one 3,858 kB
// (1,102 kB gzip) index chunk with all 12 surfaces eager. These three are the heavy,
// rarely-first-open surfaces — lazy() moves each into its own chunk fetched on first
// open. OverlayHost wraps spec.render(ctx) in a <Suspense> boundary (required — a
// lazy component without one throws "suspended on synchronous input"). The other 9
// surfaces stay eager (light / opened often). BrainHub (1,125 lines) + EvalDashboard
// (2,603 lines) + SettingsPage→SettingsTabs (11-tab tree) are the measured-heavy set.
const BrainHub = lazy(() => import('./BrainHub').then((m) => ({ default: m.BrainHub })));
const SettingsPage = lazy(() => import('../../pages/SettingsPage'));
const EvalDashboard = lazy(() => import('../../pages/EvalDashboard'));

// Region tints (mirror ThreeColumnLayout A10_GROUP) — the workbench surfaces spout
// with their card's zone accent (work = 青蓝, system = slate).
const TINT_WORK = '#4a8fb0';
const TINT_SYSTEM = '#7c8194';

/** Settings surface content — reads the deep-link tab from LayoutContext (settingsTab
 *  is still LayoutContext state: it's set by nav sub-cards / Capabilities / the
 *  swarm:open-settings deep-link before openOverlay('settings')). OverlayHost renders
 *  inside LayoutProvider, so this hook is valid. */
function SettingsSurface() {
  const { settingsTab } = useLayout();
  return <SettingsPage initialTab={settingsTab} />;
}

// ── brain-hub (M2 pilot) ────────────────────────────────────────────────────────────
// Was: BrainHubDemoOverlay + useExclusiveOverlay('swarm:show-brain-hub') + a bespoke
// <Modal fullscreen>. Now: one registry entry; content is the same <BrainHub/>.
registerOverlay({
  id: 'brain-hub',
  title: 'Brain Hub — phase-1 · read-only lens',
  mode: 'BRAIN',
  width: 'xl',
  sourceCardTestId: 'nav-brain-hub',
  render: ({ close }) => (
    <div className="flex-1 overflow-hidden" data-testid="brain-hub-overlay">
      {/* onRequestClose=close (run_a607f2b0): opening a DDD doc closes this overlay
          BEFORE dispatching swarm:open-file so the Canvas isn't rendered under the
          host — the swarmws z-index precedent. */}
      <BrainHub onRequestClose={close} />
    </div>
  ),
});

// ── swarmws (workspace explorer) ──────────────────────────────────────────────────────
// Was: SwarmWSOverlay + useExclusiveOverlay('swarm:show-swarmws'), onFileDoubleClick a
// parent callback that closed the overlay then dispatched swarm:open-file. Now: the
// file-open is app-level (swarm:open-file, handled by ChatPage useCanvasHost) — no
// bridge needed. The Gate-1 z-index close-first is preserved: close() THEN dispatch.
// OverlayHost is inside <ExplorerProvider> (ThreeColumnLayout), so the tree context is
// available. isSwarmWorkspace warning stays owned by ThreeColumnLayout's own dialog
// (dispatched via the same swarm:open-file path it already guards).
registerOverlay({
  id: 'swarmws',
  title: 'SwarmWS — workspace explorer',
  mode: 'WORKSPACE',
  // File tree = narrow single-column content → 'm' (max 760px), not 'xl' (max
  // 1200px). 'xl' left a large right-side whitespace gap (the tree only fills
  // ~45% of an xl panel). Sized to content, not to the widest tier.
  width: 'm',
  sourceCardTestId: 'nav-swarmws',
  render: ({ close }) => (
    <div className="flex-1 overflow-hidden" data-testid="swarmws-overlay">
      <WorkspaceExplorer
        onFileDoubleClick={(file: FileTreeItem) => {
          // Gate-1 z-index fix (preserved): close the overlay BEFORE opening the file
          // so the Canvas/FileViewer is not rendered under the host.
          close();
          document.dispatchEvent(new CustomEvent('swarm:open-file', {
            detail: { path: file.path, gitStatus: file.gitStatus, workspaceId: file.workspaceId },
          }));
        }}
      />
    </div>
  ),
});

// ── context (C&M Global Brain) ────────────────────────────────────────────────────────
// Was: CMBrainOverlay + useExclusiveOverlay('swarm:show-context'). Now: CMBrainContent
// (self-contained, fetches its own data) registered as content; host owns chrome.
registerOverlay({
  id: 'context',
  title: 'C&M · Global Brain',
  mode: 'BRAIN',
  width: 'xl',
  sourceCardTestId: 'nav-context',
  render: () => <CMBrainContent />,
});

// ── library (bookshelf) ───────────────────────────────────────────────────────────────
registerOverlay({
  id: 'library',
  title: 'Library · Bookshelf',
  mode: 'LIBRARY',
  width: 'xl',
  sourceCardTestId: 'nav-library',
  render: () => <LibraryContent />,
});

// ── new-brain (grow a new brain launcher) ────────────────────────────────────────────
// Needs ctx.dispatchPrompt (ChatPage-owned, via the bridge) to land the manifest in a
// chat tab. Fresh birth per open is automatic now (host mounts fresh each open).
registerOverlay({
  id: 'new-brain',
  title: 'Grow a new brain',
  mode: 'BRAIN',
  width: 'l',
  sourceCardTestId: 'nav-new-brain',
  render: ({ close, dispatchPrompt }) => (
    <NewBrainContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

// ── history (session browser) ─────────────────────────────────────────────────────────
// DATA-REACTIVE + agent-scoped: self-fetches sessions/agents from the shared query
// cache (stays reactive); takes only handlers + agent scope via the ctx bridge.
registerOverlay({
  id: 'history',
  title: 'History',
  mode: 'HISTORY',
  width: 'xl',
  sourceCardTestId: 'history-row',
  render: ({ close, resumeSession, deleteSession, agentId }) => (
    <HistoryContent
      agentId={agentId ?? null}
      onResume={(s) => (resumeSession ? resumeSession(s) : false)}
      onDeleteSession={(s) => deleteSession?.(s)}
      close={close}
    />
  ),
});

// ── WORKBENCH FOUR (M4) ───────────────────────────────────────────────────────────────
// The 4 D3 "mirror" overlays, migrated off useExclusiveOverlay+<Modal fullscreen> to the
// registry. Each keeps its own views/board/drawer/forms; the shared frame (toolbar +
// right-drawer + fmtTs) is overlayShell. All route writes through the ctx bridge:
//   • ToDo dispatches a ToDo work-packet (dispatchTodo);
//   • Jobs/Pipeline/Pollinate dispatch a chat prompt (dispatchPrompt).
// Fresh-mount-per-open (host) gives them fetch-on-mount + clean transient state.

registerOverlay({
  id: 'todo',
  title: 'ToDo',
  mode: 'TODO',
  width: 'l',
  sourceCardTestId: 'nav-todo',
  tint: TINT_WORK,
  render: ({ close, dispatchTodo }) => (
    <ToDoContent onDispatch={(t: ToDo) => (dispatchTodo ? dispatchTodo(t) : false)} close={close} />
  ),
});

// ── needs-you (unified Need You channel, 2026-08-08) ──────────────────────────
// The AlertsPill's fullscreen view. Consumes GET /api/attention (backend
// AttentionAuthority). Action = dispatch the item's message into chat via
// dispatchPrompt (existing inject mechanism — no /act, no new channel).
// width 'm' — narrow single-column list, same as swarmws (NOT xl: attention
// cards are narrower than a file tree; xl leaves a large right-side void).
registerOverlay({
  id: 'needs-you',
  title: 'Need You',
  mode: 'ATTENTION',
  width: 'm',
  sourceCardTestId: 'sidebar-alerts-slot',
  render: ({ close, dispatchPrompt }) => (
    <NeedYouContent
      onDispatch={(msg: string) => (dispatchPrompt ? dispatchPrompt(msg) : false)}
      close={close}
    />
  ),
});

registerOverlay({
  id: 'jobs',
  title: 'Jobs & Runs',
  mode: 'JOBS',
  width: 'xl',
  sourceCardTestId: 'nav-jobs',
  tint: TINT_SYSTEM,
  render: ({ close, dispatchPrompt }) => (
    <JobsRunsContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

registerOverlay({
  id: 'pipeline',
  title: 'Pipeline',
  mode: 'PIPELINE',
  width: 'xl',
  sourceCardTestId: 'nav-pipeline',
  tint: TINT_WORK,
  render: ({ close, dispatchPrompt }) => (
    <PipelineContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

registerOverlay({
  id: 'pollinate',
  title: 'Pollinate',
  mode: 'POLLINATE',
  width: 'xl',
  sourceCardTestId: 'nav-pollinate',
  tint: TINT_WORK,
  render: ({ close, dispatchPrompt }) => (
    <PollinateContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

// ── capabilities ("what your AI can do") — Work zone (run_b5d98151) ───────────────────
// Promotes Capabilities from a Settings tab to a first-class user-facing domain. Skills
// (browse by category + heroes) | Connections (status-first MCP). Agent-openable
// (swarm:show-capabilities). dispatchPrompt lands the "teach a new skill" chat flow.
registerOverlay({
  id: 'capabilities',
  title: 'Capabilities — what your AI can do',
  mode: 'CAPABILITIES',
  width: 'xl',
  sourceCardTestId: 'nav-capabilities',
  tint: TINT_WORK,
  render: ({ close, dispatchPrompt }) => (
    <CapabilitiesContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

// ── SYSTEM MODALS folded into the host (M3-tail) ──────────────────────────────────────
// Settings + OS Eval were the LAST fullscreen surfaces on the legacy useLayout.activeModal
// + Modal-fullscreen path (chatAreaBounds/navSource geometry — the D5 disease). Folding
// them here makes activeOverlay the SOLE fullscreen authority, unblocking M5's deletion of
// the legacy trio. settingsTab stays LayoutContext state (the deep-link contract);
// EvalDashboard shows its own score header, so no dynamic title needed here.
// (WorkspaceSettings + file-editor stay on activeModal — they are NOT fullscreen and carry
// no legacy geometry.)

registerOverlay({
  id: 'settings',
  title: 'Settings',
  mode: 'SETTINGS',
  width: 'xl',
  sourceCardTestId: 'nav-settings',
  tint: TINT_SYSTEM,
  render: () => <SettingsSurface />,
});

registerOverlay({
  id: 'eval',
  title: 'OS Eval',
  mode: 'EVAL',
  width: 'xl',
  sourceCardTestId: 'nav-eval',
  tint: TINT_SYSTEM,
  render: () => <EvalDashboard />,
});

// ── hive (Fleet) — nav-card-only, NOT in ALL_SHOW_EVENTS ──────────────────────────────
// Elevates Hive from a buried Settings tab to a first-class SYSTEM-zone workbench
// (run_b450108e). The fleet of remote AI clones (deploy the Agent OS to your own AWS).
// Deliberately NOT agent-openable (controls AWS credentials + live cloud infra) — same
// security boundary as settings/eval/library. Mutates via hiveService directly, so the
// content takes only `close` (no dispatchPrompt bridge — Gate-1 D5).
registerOverlay({
  id: 'hive',
  title: 'Hive · Fleet',
  mode: 'HIVE',
  width: 'xl',
  sourceCardTestId: 'nav-hive',
  tint: TINT_SYSTEM,
  render: ({ close }) => (
    <div className="flex-1 overflow-hidden" data-testid="hive-overlay-wrap">
      <HiveFleetContent close={close} />
    </div>
  ),
});
