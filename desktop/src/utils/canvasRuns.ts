/**
 * Open a chat tab's Canvas on its Runs tab (the runs that tab's session dispatched;
 * design "the runs this session dispatched, visible in the dispatching tab" r2 §3).
 *
 * A user action only (the tab's runs mark, a dispatch card's "View in Canvas"); the
 * agent has no command for it. useCanvasHost handles the event: the landing tab's Canvas
 * opens unfolded on Runs, for the session given (the tab's own when absent). Without a
 * tab id it lands on the active tab, which is the tab the click happened in.
 *
 * @exports OPEN_CANVAS_RUNS_EVENT, OpenCanvasRunsDetail, openCanvasRuns
 */
export const OPEN_CANVAS_RUNS_EVENT = 'swarm:open-canvas-runs';

export interface OpenCanvasRunsDetail {
  tabId?: string;
  sessionId?: string;
}

export function openCanvasRuns(detail: OpenCanvasRunsDetail = {}): void {
  window.dispatchEvent(new CustomEvent<OpenCanvasRunsDetail>(OPEN_CANVAS_RUNS_EVENT, { detail }));
}
