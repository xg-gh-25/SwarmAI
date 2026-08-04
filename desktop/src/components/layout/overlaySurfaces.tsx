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
import { registerOverlay } from './overlayRegistry';
import { BrainHub } from './BrainHub';
import { WorkspaceExplorer } from '../workspace-explorer';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { CMBrainContent } from './CMBrainOverlay';
import { LibraryContent } from './LibraryOverlay';
import { NewBrainContent } from './NewBrainOverlay';
import { HistoryContent } from './HistoryOverlay';
import { ToDoContent } from './ToDoOverlay';
import { JobsRunsContent } from './JobsRunsOverlay';
import { PipelineContent } from './PipelineOverlay';
import { PollinateContent } from './PollinateOverlay';
import type { ToDo } from '../../types/todo';

// Region tints (mirror ThreeColumnLayout A10_GROUP) — the workbench surfaces spout
// with their card's zone accent (work = 青蓝, system = slate).
const TINT_WORK = '#4a8fb0';
const TINT_SYSTEM = '#7c8194';

// ── brain-hub (M2 pilot) ────────────────────────────────────────────────────────────
// Was: BrainHubDemoOverlay + useExclusiveOverlay('swarm:show-brain-hub') + a bespoke
// <Modal fullscreen>. Now: one registry entry; content is the same <BrainHub/>.
registerOverlay({
  id: 'brain-hub',
  title: 'Brain Hub — phase-1 · read-only lens',
  mode: 'BRAIN',
  width: 'xl',
  sourceCardTestId: 'nav-brain-hub',
  render: () => (
    <div className="flex-1 overflow-hidden" data-testid="brain-hub-overlay">
      <BrainHub />
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
  width: 'xl',
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
