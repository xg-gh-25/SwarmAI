/**
 * Management Page Modals
 *
 * These modals wrap existing page content for display in the three-column layout.
 * They are opened from the Left Sidebar navigation icons.
 *
 * Note: SkillsModal + MCPSettingsModal removed (2026-03-26) — now Settings tabs.
 * SettingsModal + EvalModal removed (2026-08-04, M3-tail) — Settings + OS Eval
 * migrated to the OverlayHost registry (overlaySurfaces.tsx); they render through
 * the single fullscreen host, not a Modal wrapper. WorkspaceSettings stays a modal.
 */

export { default as WorkspaceSettingsModal } from './WorkspaceSettingsModal';
