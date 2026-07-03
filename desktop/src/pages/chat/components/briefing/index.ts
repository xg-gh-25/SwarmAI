/**
 * Barrel export for shared Briefing Hub components.
 *
 * Consumed by WelcomeScreen (spacious 2-col briefing hub). (JobsBar was removed
 * 2026-07-02 — job/run status now lives in RadarSidebar's Jobs & Runs section,
 * fed by useJobsRuns, not this barrel.)
 */

export { WorkingSection } from './WorkingSection';
export { SignalsSection } from './SignalsSection';
export { StocksSection } from './StocksSection';
export { SwarmOutputSection } from './SwarmOutputSection';
export {
  buildWorkingContext,
  buildSignalContext,
  openWorkspaceFile,
  formatRelativeTime,
} from './BriefingUtils';
