/**
 * Barrel export for shared Briefing Hub components.
 *
 * Consumed by WelcomeScreen (Focus + Working + Learning). Signals / Stocks /
 * Swarm Output sections were removed 2026-08-05 per XG. (JobsBar removed
 * 2026-07-02 — job/run status lives in RadarSidebar's Jobs & Runs section.)
 */

export { WorkingSection } from './WorkingSection';
export { buildWorkingContext } from './BriefingUtils';
