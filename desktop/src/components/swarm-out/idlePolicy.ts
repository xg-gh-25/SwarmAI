/**
 * A Hive's idle stop in words (Swarm Out › settings, the Resident Hives rows): what the
 * Host does with it, from the effective minutes the Hive list returns
 * (backend routers/hive `_with_idle_stop`: its own value, else the default; 0 = never).
 */
import type { TFunction } from 'i18next';

/** the minute choices of every "Stop when idle for" selector (0, always on, is separate);
 *  the backend accepts this range (routers/hive IDLE_MINUTES_MIN..MAX) */
export const IDLE_MINUTES = [30, 60, 120, 240, 480];

/** "Always on", "Stops after 2 h idle", "Stops after 30 min idle"; null when unknown. */
export function idlePolicyLabel(t: TFunction, effective: number | null | undefined): string | null {
  if (typeof effective !== 'number' || !Number.isFinite(effective) || effective < 0) return null;
  if (effective === 0) return t('swarmOut.settings.alwaysOn');
  if (effective % 60 === 0) return t('swarmOut.settings.stopsAfterHours', { hours: effective / 60 });
  return t('swarmOut.settings.stopsAfterMinutes', { minutes: Math.round(effective) });
}
