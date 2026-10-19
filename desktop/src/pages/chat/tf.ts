/**
 * tf — translate with an English fallback and `{{var}}` interpolation.
 *
 * Shared by the turn-view surfaces (TurnZones, NextSteps, TabView pill). When
 * `t` returns a non-string or the bare key (a test mock, a missing key), the
 * fallback itself is interpolated, so the UI never shows a raw key.
 *
 * @exports tf
 */
import type { TFunction } from 'i18next';

export function tf(
  t: TFunction | ((k: string, o?: unknown) => unknown),
  key: string,
  fallback: string,
  vars: Record<string, string | number> = {},
): string {
  const out = (t as (k: string, o?: unknown) => unknown)(key, { ...vars, defaultValue: fallback });
  const base = typeof out === 'string' && out !== key ? out : fallback;
  return base.replace(/\{\{(\w+)\}\}/g, (_, v: string) => String(vars[v] ?? ''));
}

