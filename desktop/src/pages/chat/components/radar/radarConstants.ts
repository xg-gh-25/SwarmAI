/**
 * Shared constants for the Swarm Radar feature.
 *
 * Exports:
 * - ``ARCHIVE_WINDOW_DAYS``      — Number of days completed tasks remain visible (default: 7)
 * - ``TASK_POLLING_INTERVAL_MS`` — Polling interval for task data in milliseconds (default: 30000)
 */

/** Number of days completed tasks remain visible in the Completed zone. */
export const ARCHIVE_WINDOW_DAYS = 7;

/** Polling interval for WIP and completed task data (milliseconds). */
export const TASK_POLLING_INTERVAL_MS = 30_000;
