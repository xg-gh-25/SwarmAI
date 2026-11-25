/**
 * Pollinate API service: the overlay's two reads.
 *
 * - GET /api/pollinate/works — the registered works (Pollinate v2): one row per work,
 *   newest first, with its thumbnail and the main output to open in Canvas.
 * - GET /api/pollinate/assets — the older run gallery: newest-first content cards +
 *   overall rollup (ASSET-CENTRIC: the first-class object is a produced media asset).
 *
 * Thumbnails: the frontend points <img> at the EXISTING GET /api/workspace/file/raw
 * (workspace-sandboxed FileResponse) — see assetThumbUrl(). No media bytes flow
 * through this service.
 *
 * Exports:
 * - pollinateService — { fetchWorks(), fetchAssets(), fetchAssetBody(path) }
 * - assetThumbUrl(path) — absolute /api/workspace/file/raw URL for an <img src>
 * - camelCase types: PollinateWork, PollinateAsset, PollinateContentCard, PollinateOverall, ...
 */

import api from './api';
import { getApiBaseUrl } from './tauri';

export interface PollinateAsset {
  platform: string;
  format: string;
  filePath: string;
  fileName: string;
  isImage: boolean;
  /** From a publish-kit.md frontmatter; 'ready' when none says otherwise. */
  publishStatus: 'ready' | 'ready-to-publish' | 'published';
}

export interface PollinateContentCard {
  run: string;
  topic: string;
  domain: string | null;
  status: string;
  createdAt: string | null;
  hasRunJson: boolean;
  assetCount: number;
  platforms: string[];
  formats: string[];
  publishedCount: number;
  readyCount: number;
  assets: PollinateAsset[];
}

export interface PollinateOverall {
  cardCount: number;
  assetCount: number;
  platformDist: Record<string, number>;
  formatDist: Record<string, number>;
  domainDist: Record<string, number>;
  published: number;
  ready: number;
  inProgress: number;
  /** Known-channel universe (server SSOT) — lets Insights grey out fully-neglected channels. */
  knownChannels: string[];
}

export interface PollinateAssetsResponse {
  overall: PollinateOverall;
  cards: PollinateContentCard[];
}

/** One registered work (a folder with a work.json). Paths are workspace-relative when
 *  `inWorkspace`, absolute otherwise (Canvas and the raw-file endpoint cannot open those). */
export interface PollinateWork {
  path: string;
  inWorkspace: boolean;
  kind: string;
  title: string;
  /** Current version id (e.g. "v003"): the last version that passed; null before one did. */
  current: string | null;
  /** The newest version id (the last cut, passed or not). */
  latest: string | null;
  /** The NEWEST version's check result (a failed cut leaves `current` on the last
   *  passing version); null when unknown. */
  status: 'ok' | 'failed' | null;
  updated: string | null;
  thumbnail: string | null;
  /** The file to open in Canvas; null when nothing is rendered yet. */
  mainOutput: string | null;
}

export interface PollinateWorksResponse {
  works: PollinateWork[];
  /** Registry entries left out because a file was missing or broken. */
  skipped: number;
}

function workToCamel(w: Record<string, unknown>): PollinateWork {
  const status = w.status === 'ok' || w.status === 'failed' ? w.status : null;
  return {
    path: (w.path as string) ?? '',
    inWorkspace: Boolean(w.in_workspace),
    kind: (w.kind as string) ?? 'unknown',
    title: (w.title as string) ?? ((w.path as string) ?? ''),
    current: (w.current as string | null) ?? null,
    latest: (w.latest as string | null) ?? null,
    status,
    updated: (w.updated as string | null) ?? null,
    thumbnail: (w.thumbnail as string | null) ?? null,
    mainOutput: (w.main_output as string | null) ?? null,
  };
}

function assetToCamel(a: Record<string, unknown>): PollinateAsset {
  return {
    platform: (a.platform as string) ?? '',
    format: (a.format as string) ?? 'other',
    filePath: (a.file_path as string) ?? '',
    fileName: (a.file_name as string) ?? '',
    isImage: Boolean(a.is_image),
    publishStatus: (a.publish_status as PollinateAsset['publishStatus']) ?? 'ready',
  };
}

function cardToCamel(c: Record<string, unknown>): PollinateContentCard {
  return {
    run: c.run as string,
    topic: (c.topic as string) ?? (c.run as string),
    domain: (c.domain as string | null) ?? null,
    status: (c.status as string) ?? 'unknown',
    createdAt: (c.created_at as string | null) ?? null,
    hasRunJson: Boolean(c.has_run_json),
    assetCount: (c.asset_count as number) ?? 0,
    platforms: (c.platforms as string[]) ?? [],
    formats: (c.formats as string[]) ?? [],
    publishedCount: (c.published_count as number) ?? 0,
    readyCount: (c.ready_count as number) ?? 0,
    assets: Array.isArray(c.assets) ? (c.assets as Record<string, unknown>[]).map(assetToCamel) : [],
  };
}

function overallToCamel(o: Record<string, unknown>): PollinateOverall {
  return {
    cardCount: (o.card_count as number) ?? 0,
    assetCount: (o.asset_count as number) ?? 0,
    platformDist: (o.platform_dist as Record<string, number>) ?? {},
    formatDist: (o.format_dist as Record<string, number>) ?? {},
    domainDist: (o.domain_dist as Record<string, number>) ?? {},
    published: (o.published as number) ?? 0,
    ready: (o.ready as number) ?? 0,
    inProgress: (o.in_progress as number) ?? 0,
    knownChannels: (o.known_channels as string[]) ?? [],
  };
}

/** Absolute /api/workspace/file/raw URL for a workspace-relative asset path. Used
 *  as an <img src> — mirrors MarkdownRenderer's image-URL construction. */
export function assetThumbUrl(relativePath: string): string {
  return `${getApiBaseUrl()}/api/workspace/file/raw?path=${encodeURIComponent(relativePath)}`;
}

export const pollinateService = {
  async fetchWorks(): Promise<PollinateWorksResponse> {
    const { data } = await api.get('/pollinate/works');
    const d = data as Record<string, unknown>;
    return {
      works: Array.isArray(d.works) ? (d.works as Record<string, unknown>[]).map(workToCamel) : [],
      skipped: typeof d.skipped === 'number' ? d.skipped : 0,
    };
  },

  async fetchAssets(): Promise<PollinateAssetsResponse> {
    const { data } = await api.get('/pollinate/assets');
    const d = data as Record<string, unknown>;
    return {
      overall: overallToCamel((d.overall as Record<string, unknown>) ?? {}),
      cards: Array.isArray(d.cards) ? (d.cards as Record<string, unknown>[]).map(cardToCamel) : [],
    };
  },

  /** Lazily read ONE text asset's body (caption/narrative) on drawer-open — via the
   *  existing workspace file endpoint (returns {content, encoding}). Kept OUT of
   *  fetchAssets so the gallery load stays fast (the 87ms baseline is untouched). */
  async fetchAssetBody(path: string): Promise<string | null> {
    try {
      const { data } = await api.get('/workspace/file', { params: { path } });
      const d = data as Record<string, unknown>;
      if (d.encoding === 'utf-8' && typeof d.content === 'string') return d.content;
      return null; // binary/other → not renderable as caption text
    } catch {
      return null;
    }
  },
};
