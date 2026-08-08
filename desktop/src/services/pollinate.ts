/**
 * Pollinate Content-Asset Gallery API service.
 *
 * ASSET-CENTRIC (sibling of pipelines.ts, but the first-class object is a produced
 * media asset, not a run). Fetches the newest-first content cards + overall rollup
 * from GET /api/pollinate/assets, and one topic's detail from GET /api/pollinate/{run}.
 *
 * Thumbnails: the frontend points <img> at the EXISTING GET /api/workspace/file/raw
 * (workspace-sandboxed FileResponse) — see assetThumbUrl(). No media bytes flow
 * through this service.
 *
 * Exports:
 * - pollinateService — { fetchAssets(), fetchTopicDetail(run) }
 * - assetThumbUrl(path) — absolute /api/workspace/file/raw URL for an <img src>
 * - camelCase types: PollinateAsset, PollinateContentCard, PollinateOverall, ...
 */

import api from './api';
import { getApiBaseUrl } from './tauri';

export interface PollinateAsset {
  platform: string;
  format: string;
  filePath: string;
  fileName: string;
  isImage: boolean;
  publishStatus: 'ready' | 'ready-to-publish' | 'published';
  /** Stable logical id (sha1 of platform/format/fileName) — the key for POST /publish. */
  assetId: string;
  /** Public URL where the asset was posted (set once marked published). */
  postedUrl: string | null;
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

export interface PollinateTopicDetail {
  run: string;
  topic: string;
  domain: string | null;
  status: string;
  createdAt: string | null;
  contentPackage: string | null;
  assets: PollinateAsset[];
}

function assetToCamel(a: Record<string, unknown>): PollinateAsset {
  return {
    platform: (a.platform as string) ?? '',
    format: (a.format as string) ?? 'other',
    filePath: (a.file_path as string) ?? '',
    fileName: (a.file_name as string) ?? '',
    isImage: Boolean(a.is_image),
    publishStatus: (a.publish_status as PollinateAsset['publishStatus']) ?? 'ready',
    assetId: (a.asset_id as string) ?? '',
    postedUrl: (a.posted_url as string | null) ?? null,
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

  async fetchTopicDetail(run: string): Promise<PollinateTopicDetail | null> {
    try {
      const { data } = await api.get(`/pollinate/${encodeURIComponent(run)}`);
      const d = data as Record<string, unknown>;
      return {
        run: d.run as string,
        topic: (d.topic as string) ?? (d.run as string),
        domain: (d.domain as string | null) ?? null,
        status: (d.status as string) ?? 'unknown',
        createdAt: (d.created_at as string | null) ?? null,
        contentPackage: (d.content_package as string | null) ?? null,
        assets: Array.isArray(d.assets) ? (d.assets as Record<string, unknown>[]).map(assetToCamel) : [],
      };
    } catch {
      return null;
    }
  },

  /** Mark ONE asset published/unpublished (P1 write path). Persists to the run's
   *  publish-state.json sidecar. Returns the new publish_status, or null on failure
   *  (caller keeps the old UI state). */
  async markPublished(
    run: string, assetId: string, published: boolean, postedUrl?: string | null,
  ): Promise<{ publishStatus: PollinateAsset['publishStatus']; postedUrl: string | null } | null> {
    try {
      const { data } = await api.post(`/pollinate/${encodeURIComponent(run)}/publish`, {
        asset_id: assetId,
        published,
        posted_url: postedUrl || null,
      });
      const d = data as Record<string, unknown>;
      return {
        publishStatus: (d.publish_status as PollinateAsset['publishStatus']) ?? 'ready',
        postedUrl: (d.posted_url as string | null) ?? null,
      };
    } catch {
      return null;
    }
  },
};
