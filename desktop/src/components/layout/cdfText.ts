/**
 * Small text helpers shared by the Cognition flow view (CognitiveDistillationFlow,
 * CdfUnitFeed, CdfFlowDetails): bold-in-sentence interpolation, number, byte and file
 * name formatting.
 */
import { Fragment, createElement, type ReactNode } from 'react';
import type { TFunction } from 'i18next';

/** A translated sentence with React nodes in place of `{{name}}`. */
export function rich(t: TFunction, key: string, values: Record<string, ReactNode>): ReactNode {
  const marks = Object.fromEntries(Object.keys(values).map((name) => [name, `\u0001${name}\u0001`]));
  const parts = String(t(key, marks)).split('\u0001');
  return parts.map((part, index) => (index % 2 === 1 && part in values
    ? createElement(Fragment, { key: index }, values[part]) : part));
}

export function fmt(value: number): string {
  return value.toLocaleString();
}

export function bytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${Math.round(value / 1024 ** 2)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

export function fileName(path: string | null | undefined): string {
  if (!path) return '';
  const trimmed = path.replace(/\/+$/, '');
  return trimmed.slice(trimmed.lastIndexOf('/') + 1) || trimmed;
}
