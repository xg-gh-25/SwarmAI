/**
 * Barrel export for the RightSidebar directory.
 *
 * Exposes ``RadarSidebar`` as the single public API of this directory.
 * Internal components (RadarView, HistoryView, sections, shared primitives)
 * are implementation details and should not be imported directly.
 */

export { RadarSidebar } from './RadarSidebar';
export type { RadarSidebarProps } from './types';
