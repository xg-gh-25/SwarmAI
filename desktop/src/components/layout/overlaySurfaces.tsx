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
import { lazy, useMemo } from 'react';
import { OpenSessionContext, overlaySessionOpener } from '../swarm-out/openSession';
import { registerOverlay } from './overlayRegistry';
import WorkspaceOverlay from '../workspace-explorer/WorkspaceOverlay';
import { continueInSession } from './continueInSession';
import { OVERLAY_CHUNK } from './overlayChunks';
import { useLayout } from '../../contexts/LayoutContext';
import type { ToDo } from '../../types/todo';
import { useBrainsWithPinned } from '../../services/ddd';
import { useOverlay } from '../../contexts/OverlayContext';

// Every overlay's content is its own chunk (design left-nav-overlay-single-source.md
// S4; the G2 split of run_06c49540 did three). Each lazy component below is built on
// overlayChunks' one import for that id, which the nav also calls on hover, so a
// hovered item's code is loaded before the click. OverlayHost wraps spec.render(ctx)
// in the <Suspense> a lazy component needs. (Measured 2026-10-23: index chunk
// 3,975 kB before this, see the design doc §12 for after.)
const CMBrainContent = lazy(() => OVERLAY_CHUNK.context().then((m) => ({ default: m.CMBrainContent })));
const LibraryContent = lazy(() => OVERLAY_CHUNK.library().then((m) => ({ default: m.LibraryContent })));
const NewBrainContent = lazy(() => OVERLAY_CHUNK['new-brain']().then((m) => ({ default: m.NewBrainContent })));
const HistoryContent = lazy(() => OVERLAY_CHUNK.history().then((m) => ({ default: m.HistoryContent })));
const ArtifactsContent = lazy(() => OVERLAY_CHUNK.artifacts().then((m) => ({ default: m.ArtifactsContent })));
const ToDoContent = lazy(() => OVERLAY_CHUNK.todo().then((m) => ({ default: m.ToDoContent })));
const NeedYouContent = lazy(() => OVERLAY_CHUNK['needs-you']().then((m) => ({ default: m.NeedYouContent })));
const CommunityContent = lazy(() => OVERLAY_CHUNK.community().then((m) => ({ default: m.CommunityContent })));
const JobsRunsContent = lazy(() => OVERLAY_CHUNK.jobs().then((m) => ({ default: m.JobsRunsContent })));
const CapabilitiesContent = lazy(() => OVERLAY_CHUNK.capabilities().then((m) => ({ default: m.CapabilitiesContent })));
const PipelineContent = lazy(() => OVERLAY_CHUNK.pipeline().then((m) => ({ default: m.PipelineContent })));
const PollinateContent = lazy(() => OVERLAY_CHUNK.pollinate().then((m) => ({ default: m.PollinateContent })));
const UsageContent = lazy(() => OVERLAY_CHUNK.usage().then((m) => ({ default: m.UsageContent })));
const HiveFleetContent = lazy(() => OVERLAY_CHUNK.hive().then((m) => ({ default: m.HiveFleetContent })));
const BrainHub = lazy(() => OVERLAY_CHUNK['brain-hub']().then((m) => ({ default: m.BrainHub })));
const SettingsPage = lazy(() => OVERLAY_CHUNK.settings());
const EvalOverlay = lazy(() => OVERLAY_CHUNK.eval());

// Chrome (title, mode, width, zone tint, spout target) lives in navTable.NAV, not here:
// each entry below is only an id + its content.

/** Settings surface content — reads the deep-link tab from LayoutContext (settingsTab
 *  is still LayoutContext state: it's set by nav sub-cards / Capabilities / the
 *  swarm:open-settings deep-link before openOverlay('settings')). OverlayHost renders
 *  inside LayoutProvider, so this hook is valid. */
function SettingsSurface() {
  const { settingsTab } = useLayout();
  return <SettingsPage initialTab={settingsTab} />;
}

/** Brain Hub inside the overlay provider: the General Brain's "Open Library" switches to
 *  the Library overlay the way its nav card does (Library has no show-event); a Hive run
 *  card in a Brain's Runs tab opens its session as Swarm Out's does. */
function BrainHubSurface({ close, dispatchPrompt, openHiveSession }: { close: () => void;
  dispatchPrompt?: (msg: string, opts?: { autoSend?: boolean }) => boolean;
  openHiveSession?: (sessionId: string, title: string) => void }) {
  const { openOverlay } = useOverlay();
  const opener = useMemo(() => overlaySessionOpener(close, openHiveSession), [close, openHiveSession]);
  return (
    <OpenSessionContext.Provider value={opener}>
      <BrainHub onRequestClose={close} onDispatch={dispatchPrompt ?? (() => false)}
        onOpenLibrary={() => openOverlay('library')} />
    </OpenSessionContext.Provider>
  );
}

// ── brain-hub (M2 pilot) ────────────────────────────────────────────────────────────
// Was: BrainHubDemoOverlay + useExclusiveOverlay('swarm:show-brain-hub') + a bespoke
// <Modal fullscreen>. Now: one registry entry; content is the same <BrainHub/>.
registerOverlay({
  id: 'brain-hub',
  render: ({ close, dispatchPrompt, openHiveSession }) => (
    <div className="flex-1 overflow-hidden" data-testid="brain-hub-overlay">
      {/* onRequestClose=close (run_a607f2b0): opening a DDD doc closes this overlay
          BEFORE dispatching swarm:open-file so the Canvas isn't rendered under the
          host — the swarmws z-index precedent.
          onDispatch (item 3): the Distribute sub-tab injects + auto-sends the
          `s_ddd-distribute` command via the ChatPage-owned dispatchPrompt bridge —
          replacing the old clipboard-copy handoff. Same bridge NewBrain/Jobs use. */}
      <BrainHubSurface close={close} dispatchPrompt={dispatchPrompt} openHiveSession={openHiveSession} />
    </div>
  ),
});

// ── swarmws (Workspace) ───────────────────────────────────────────────────────────────
// WorkspaceOverlay (design workspace-working-location.md v5): SwarmWS + declared local
// repos, each standalone. It mounts its own ExplorerProvider, so nothing is fetched
// until the overlay opens. Opening a file closes the overlay first (Gate-1 z-index),
// then dispatches swarm:open-file (handled by ChatPage useCanvasHost).
registerOverlay({
  id: 'swarmws',
  render: ({ close }) => (
    <div className="flex-1 overflow-hidden" data-testid="swarmws-overlay">
      <WorkspaceOverlay close={close} />
    </div>
  ),
});

// ── context (C&M Global Brain) ────────────────────────────────────────────────────────
// Was: CMBrainOverlay + useExclusiveOverlay('swarm:show-context'). Now: CMBrainContent
// (self-contained, fetches its own data) registered as content; host owns chrome.
registerOverlay({
  id: 'context',
  render: () => <CMBrainContent />,
});

// ── library (bookshelf) ───────────────────────────────────────────────────────────────
registerOverlay({
  id: 'library',
  render: () => <LibraryContent />,
});

// ── new-brain (grow a new brain launcher) ────────────────────────────────────────────
// Needs ctx.dispatchPrompt (ChatPage-owned, via the bridge) to land the manifest in a
// chat tab. Fresh birth per open is automatic now (host mounts fresh each open).
registerOverlay({
  id: 'new-brain',
  render: ({ close, dispatchPrompt }) => (
    <NewBrainWithNames close={close} onDispatch={dispatchPrompt ?? (() => false)} />
  ),
});

/** New Brain needs the existing Brain names to refuse a duplicate before dispatch;
 *  it reads the same cached gallery query the Brain Hub uses (no extra fetch when warm). */
function NewBrainWithNames({ close, onDispatch }: {
  close: () => void; onDispatch: (prompt: string, opts?: { autoSend?: boolean }) => boolean;
}) {
  const { data } = useBrainsWithPinned();
  return <NewBrainContent onDispatch={onDispatch} close={close} existingNames={(data?.brains ?? []).map((b) => b.name)} />;
}

// ── history (session browser) ─────────────────────────────────────────────────────────
// DATA-REACTIVE + agent-scoped: self-fetches sessions/agents from the shared query
// cache (stays reactive); takes only handlers + agent scope via the ctx bridge.
registerOverlay({
  id: 'history',
  render: ({ close, resumeSession, deleteSession, agentId }) => (
    <HistoryContent
      agentId={agentId ?? null}
      onResume={(s) => (resumeSession ? resumeSession(s) : false)}
      onDeleteSession={(s) => deleteSession?.(s)}
      close={close}
    />
  ),
});

// ── artifacts (product gallery) ─────────────────────────────────────────────────────────
// History's twin: browses the workspace PRODUCT registry (GET /artifacts/products →
// products.json, Artifacts B′ Run 2) instead of past conversations — a ROLE-grouped gallery
// (Deliverables card grid primary; Knowledge/Pipeline/Activity collapsible below), so a
// gitignored deck the old git-log view could not see now surfaces. Pure SELECTOR — a row
// click dispatches swarm:open-file (→ current-tab Canvas) then closes; no preview pane
// (Canvas is the sole file viewer). width:'l' (was 'm') — the deliverables card grid needs
// the horizontal room a single-column list did not.
registerOverlay({
  id: 'artifacts',
  render: ({ close }) => <ArtifactsContent close={close} />,
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
  render: ({ close, dispatchTodo }) => (
    <ToDoContent onDispatch={(t: ToDo) => (dispatchTodo ? dispatchTodo(t) : false)} close={close} />
  ),
});

// ── needs-you (unified Need You channel, 2026-08-08) ──────────────────────────
// The AlertsPill's fullscreen view. Consumes GET /api/attention (backend
// AttentionAuthority). Action = dispatch the item's message into chat via
// dispatchPrompt (existing inject mechanism — no /act, no new channel).
// width 'xl' like the other overlays: it holds the Cognition flow view (where kept
// knowledge landed on one scale, the six steps side by side, the flow diagram), which a
// narrow column squeezes.
registerOverlay({
  id: 'needs-you',
  render: ({ close, dispatchPrompt }) => (
    <NeedYouContent
      onDispatch={(msg, opts) => (dispatchPrompt ? dispatchPrompt(msg, opts) : false)}
      close={close}
    />
  ),
});

// ── community (run_5165013e) — inbound signals (Feed) + the watched feeds
// (Sources). Work-zone card (spouts teal). width 'l' — a tabbed list surface (like todo),
// wider than a single-column list but not the full dashboard 'xl'.
registerOverlay({
  id: 'community',
  render: ({ close }) => <CommunityContent close={close} />,
});

registerOverlay({
  id: 'jobs',
  render: ({ close, dispatchPrompt }) => (
    <JobsRunsContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

registerOverlay({
  id: 'pipeline',
  render: ({ close, dispatchPrompt, resumeSession }) => (
    <PipelineContent onDispatch={dispatchPrompt ?? (() => false)} close={close}
                     onContinue={(sessionId, prompt) => continueInSession(sessionId, prompt, resumeSession)} />
  ),
});

registerOverlay({
  id: 'pollinate',
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
  render: ({ close, dispatchPrompt }) => (
    <CapabilitiesContent onDispatch={dispatchPrompt ?? (() => false)} close={close} />
  ),
});

// ── SYSTEM MODALS folded into the host (M3-tail) ──────────────────────────────────────
// Settings + OS Eval were the LAST fullscreen surfaces on the legacy useLayout.activeModal
// + Modal-fullscreen path (chatAreaBounds/navSource geometry — the D5 disease). Folding
// them here makes activeOverlay the SOLE fullscreen authority, unblocking M5's deletion of
// the legacy trio. settingsTab stays LayoutContext state (the deep-link contract);
// The Eval overlay shows its own header, so no dynamic title needed here.
// (WorkspaceSettings + file-editor stay on activeModal — they are NOT fullscreen and carry
// no legacy geometry.)

registerOverlay({
  id: 'settings',
  render: () => <SettingsSurface />,
});

registerOverlay({
  id: 'eval',
  render: ({ close, dispatchPrompt, resumeSession }) => (
    <EvalOverlay close={close} dispatchPrompt={dispatchPrompt} resumeSession={resumeSession} />
  ),
});

// ── hive (Fleet) — nav-card-only, NOT in ALL_SHOW_EVENTS ──────────────────────────────
// Elevates Hive from a buried Settings tab to a first-class SYSTEM-zone workbench
// (run_b450108e). The fleet of remote AI clones (deploy the Agent OS to your own AWS).
// Deliberately NOT agent-openable (controls AWS credentials + live cloud infra) — same
// security boundary as settings/eval/library. Mutates via hiveService directly, so the
// content takes only `close` (no dispatchPrompt bridge — Gate-1 D5).
registerOverlay({
  id: 'hive',
  render: ({ close }) => (
    <div className="flex-1 overflow-hidden" data-testid="hive-overlay-wrap">
      <HiveFleetContent close={close} />
    </div>
  ),
});

// ── usage — the usage view; nav card + the top bar's token figures ─────────────────────
registerOverlay({
  id: 'usage',
  render: ({ close, resumeSession }) => (
    <div className="flex-1 overflow-hidden" data-testid="usage-overlay">
      <UsageContent close={close} resumeSession={resumeSession} />
    </div>
  ),
});
