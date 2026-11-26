/**
 * Pollinate API service: the overlay's three reads (backend core/pollinate_works.py).
 *
 * - GET /api/pollinate/works — one row per registered work, newest first: its open
 *   revision, version summary, Brain, size, slide count (a video: duration, scenes).
 * - GET /api/pollinate/works/view?path= — one work's big card: versions with their
 *   guard result and failed checks, the newest version's findings, the brief, outputs,
 *   slide images, a video's scenes and the actions its state allows (decided by the backend).
 * - GET /api/pollinate/eval — the Eval tab: first-pass revisions over 15 days against the
 *   15 before, daily bars, four numbers, check hits and guard blocks.
 *
 * Images: <img> points at the EXISTING GET /api/workspace/file/raw (assetThumbUrl). The
 * pre-v2 run gallery (/assets) is gone with no backward compatibility.
 *
 * Exports: pollinateService { fetchWorks, fetchWorkView, fetchEval }, assetThumbUrl, the
 * camelCase types below.
 */

import api, { ApiError } from './api';
import { getApiBaseUrl } from './tauri';

type Raw = Record<string, unknown>;

export interface PollinateOpenRevision {
  scope: string[];
  remove: string[];
  add: number;
  note: string | null;
  at: string | null;
  base: string | null;
  /** the chat session that opened it (Open session) */
  session: string | null;
}

export interface PollinateVersion {
  id: string;
  by: 'user' | 'agent';
  status: 'ok' | 'failed' | null;
  at: string | null;
  scope: string[];
  base: string | null;
  note: string | null;
  /** a hand edit absorbed by work.py open */
  foundOnDisk: boolean;
  session: string | null;
}

/** One registered work. Paths are workspace-relative when `inWorkspace`, absolute
 *  otherwise (Canvas and the raw-file endpoint cannot open those). */
export interface PollinateWork {
  path: string;
  inWorkspace: boolean;
  kind: string;
  title: string;
  brain: string;
  style: string | null;
  width: number | null;
  height: number | null;
  slideCount: number | null;
  /** The last version that passed; null before one did. */
  current: string | null;
  /** The newest version (the last cut, passed or not). */
  latest: string | null;
  status: 'ok' | 'failed' | null;
  updated: string | null;
  open: PollinateOpenRevision | null;
  versions: PollinateVersion[];
  thumbnail: string | null;
  /** The file to open in Canvas; null when nothing is rendered yet. */
  mainOutput: string | null;
  durationS: number | null;
  sceneCount: number | null;
}

export interface PollinateWorksResponse {
  works: PollinateWork[];
  /** Registry entries left out because a file was missing or broken. */
  skipped: number;
}

export interface PollinateVersionDetail extends PollinateVersion {
  guardOk: boolean | null;
  failCounts: Record<string, number>;
}

export interface PollinateFinding {
  check: string;
  level: 'fail' | 'warn';
  slide: string | null;
  msg: string | null;
  /** the slide already had it before this version */
  carried: boolean;
}

export interface PollinateOutput { path: string | null; name: string; bytes: number | null; files: number | null }
export interface PollinateSlide { n: number; id: string; image: string | null; changed: boolean }
export interface PollinateScene { id: string; title: string | null; startS: number; durationS: number | null; image: string | null }
export type PollinateActionKey = 'open_canvas' | 'open_session' | 'continue_in_chat';
export interface PollinateAction { key: PollinateActionKey; primary: boolean; path: string | null; session: string | null }

export interface PollinateWorkView {
  work: PollinateWork;
  versions: PollinateVersionDetail[];
  findings: PollinateFinding[];
  guard: { ok: boolean; violations: { slide: string | null; kind: string | null; detail: string | null }[] } | null;
  brief: string | null;
  outputs: PollinateOutput[];
  slides: PollinateSlide[];
  scenes: PollinateScene[];
  session: string | null;
  actions: PollinateAction[];
}

export interface PollinateRate { passed: number; revisions: number; pct: number }
export interface PollinatePair { cur: number | null; prev: number | null }
export interface PollinateEval {
  days: number;
  rate: { cur: PollinateRate | null; prev: PollinateRate | null };
  daily: { date: string; firstPass: number; later: number; notPassed: number }[];
  worksChanged: PollinatePair;
  versionsMedian: PollinatePair;
  personVersions: PollinatePair;
  guardBlocks: PollinatePair;
  checkHits: { check: string; count: number; topTitle: string; topCount: number }[];
  guardList: { path: string; title: string; version: string; at: string | null; violations: string[] }[];
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const list = (v: unknown): Raw[] => (Array.isArray(v) ? v.filter((x): x is Raw => !!x && typeof x === 'object') : []);
const status = (v: unknown): 'ok' | 'failed' | null => (v === 'ok' || v === 'failed' ? v : null);

function versionToCamel(v: Raw): PollinateVersion {
  return {
    id: str(v.id) ?? '',
    by: v.by === 'user' ? 'user' : 'agent',
    status: status(v.status),
    at: str(v.at),
    scope: strs(v.scope),
    base: str(v.base),
    note: str(v.note),
    foundOnDisk: Boolean(v.found_on_disk),
    session: str(v.session),
  };
}

function openToCamel(o: unknown): PollinateOpenRevision | null {
  if (!o || typeof o !== 'object') return null;
  const r = o as Raw;
  return {
    scope: strs(r.scope), remove: strs(r.remove), add: num(r.add) ?? 0,
    note: str(r.note), at: str(r.at), base: str(r.base), session: str(r.session),
  };
}

export function workToCamel(w: Raw): PollinateWork {
  return {
    path: str(w.path) ?? '',
    inWorkspace: Boolean(w.in_workspace),
    kind: str(w.kind) ?? 'unknown',
    title: str(w.title) ?? str(w.path) ?? '',
    brain: str(w.brain) ?? 'General',
    style: str(w.style),
    width: num(w.width),
    height: num(w.height),
    slideCount: num(w.slide_count),
    current: str(w.current),
    latest: str(w.latest),
    status: status(w.status),
    updated: str(w.updated),
    open: openToCamel(w.open),
    versions: list(w.versions).map(versionToCamel),
    thumbnail: str(w.thumbnail),
    mainOutput: str(w.main_output),
    durationS: num(w.duration_s),
    sceneCount: num(w.scene_count),
  };
}

function viewToCamel(d: Raw): PollinateWorkView {
  const g = d.guard && typeof d.guard === 'object' ? (d.guard as Raw) : null;
  return {
    work: workToCamel((d.work as Raw) ?? {}),
    versions: list(d.versions).map((v) => ({
      ...versionToCamel(v),
      guardOk: typeof v.guard_ok === 'boolean' ? v.guard_ok : null,
      failCounts: v.fail_counts && typeof v.fail_counts === 'object' ? (v.fail_counts as Record<string, number>) : {},
    })),
    findings: list(d.findings).map((f) => ({
      check: str(f.check) ?? '', level: f.level === 'fail' ? 'fail' : 'warn', slide: str(f.slide),
      msg: str(f.msg), carried: Boolean(f.carried),
    })),
    guard: g ? {
      ok: Boolean(g.ok),
      violations: list(g.violations).map((v) => ({ slide: str(v.slide), kind: str(v.kind), detail: str(v.detail) })),
    } : null,
    brief: str(d.brief),
    outputs: list(d.outputs).map((o) => ({ path: str(o.path), name: str(o.name) ?? '', bytes: num(o.bytes), files: num(o.files) })),
    slides: list(d.slides).map((s) => ({ n: num(s.n) ?? 0, id: str(s.id) ?? '', image: str(s.image), changed: Boolean(s.changed) })),
    scenes: list(d.scenes).map((s) => ({
      id: str(s.id) ?? '', title: str(s.title), startS: num(s.start_s) ?? 0, durationS: num(s.duration_s), image: str(s.image),
    })),
    session: str(d.session),
    actions: list(d.actions)
      .filter((a) => ['open_canvas', 'open_session', 'continue_in_chat'].includes(String(a.key)))
      .map((a) => ({ key: a.key as PollinateActionKey, primary: Boolean(a.primary), path: str(a.path), session: str(a.session) })),
  };
}

const pair = (v: unknown): PollinatePair => {
  const r = (v && typeof v === 'object' ? v : {}) as Raw;
  return { cur: num(r.cur), prev: num(r.prev) };
};
const rate = (v: unknown): PollinateRate | null => {
  if (!v || typeof v !== 'object') return null;
  const r = v as Raw;
  return { passed: num(r.passed) ?? 0, revisions: num(r.revisions) ?? 0, pct: num(r.pct) ?? 0 };
};

function evalToCamel(d: Raw): PollinateEval {
  const r = (d.rate && typeof d.rate === 'object' ? d.rate : {}) as Raw;
  return {
    days: num(d.days) ?? 15,
    rate: { cur: rate(r.cur), prev: rate(r.prev) },
    daily: list(d.daily).map((x) => ({
      date: str(x.date) ?? '', firstPass: num(x.first_pass) ?? 0, later: num(x.later) ?? 0, notPassed: num(x.not_passed) ?? 0,
    })),
    worksChanged: pair(d.works_changed),
    versionsMedian: pair(d.versions_median),
    personVersions: pair(d.person_versions),
    guardBlocks: pair(d.guard_blocks),
    checkHits: list(d.check_hits).map((h) => ({
      check: str(h.check) ?? '', count: num(h.count) ?? 0, topTitle: str(h.top_title) ?? '', topCount: num(h.top_count) ?? 0,
    })),
    guardList: list(d.guard_list).map((g) => ({
      path: str(g.path) ?? '', title: str(g.title) ?? '', version: str(g.version) ?? '', at: str(g.at), violations: strs(g.violations),
    })),
  };
}

/** Absolute /api/workspace/file/raw URL for a workspace-relative path (an <img src>). */
export function assetThumbUrl(relativePath: string): string {
  return `${getApiBaseUrl()}/api/workspace/file/raw?path=${encodeURIComponent(relativePath)}`;
}

export const pollinateService = {
  async fetchWorks(): Promise<PollinateWorksResponse> {
    const { data } = await api.get('/pollinate/works');
    const d = data as Raw;
    return { works: list(d.works).map(workToCamel), skipped: num(d.skipped) ?? 0 };
  },

  /** One work's big card; null when the path is not a registered work (404). */
  async fetchWorkView(path: string): Promise<PollinateWorkView | null> {
    try {
      const { data } = await api.get('/pollinate/works/view', { params: { path } });
      return viewToCamel(data as Raw);
    } catch (e) {
      // the api interceptor rejects with ApiError, whose HTTP code is statusCode
      if (e instanceof ApiError && e.statusCode === 404) return null;
      throw e;
    }
  },

  /** Days are the person's local days: tz is their offset in minutes east of UTC. */
  async fetchEval(days = 15, tz = -new Date().getTimezoneOffset()): Promise<PollinateEval> {
    const { data } = await api.get('/pollinate/eval', { params: { days, tz } });
    return evalToCamel(data as Raw);
  },
};
