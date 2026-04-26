/**
 * Barrel export for shared Briefing Hub components.
 *
 * Used by both WelcomeScreen (spacious 2-col) and RadarSidebar (compact list).
 */

export { WorkingSection } from './WorkingSection';
export { SignalsSection } from './SignalsSection';
export { HotNewsSection } from './HotNewsSection';
export { StocksSection } from './StocksSection';
export { SwarmOutputSection } from './SwarmOutputSection';
export { JobsBar } from './JobsBar';
export {
  buildTodoContext,
  buildWorkingContext,
  buildSignalContext,
  buildHotContext,
  openWorkspaceFile,
  formatRelativeTime,
} from './BriefingUtils';
