/**
 * overlayQueries — the one place an overlay's opening data is defined (design
 * docs/design/left-nav-overlay-single-source.md S3).
 *
 * Each entry is a TanStack Query option set (queryKey + queryFn). The overlay reads
 * it with `useQuery(...)`; the nav prefetches the SAME options on hover/focus
 * (`prefetchNav`). One key, one fetcher, so a reopen inside `OVERLAY_STALE_MS` shows
 * the cached data at once and a hovered item is already loaded when clicked.
 *
 * Writes do not refetch by hand: they call `invalidateOverlay(qc, id)`.
 *
 * Key exports:
 * - `OVERLAY_STALE_MS`   the one freshness window (30 s)
 * - `overlayQuery`       the option builders, per overlay
 * - `NAV_PREFETCH`       what to prefetch for a nav item
 * - `prefetchNav(qc, id)` / `invalidateOverlay(qc, id)`
 */
import type { QueryClient } from '@tanstack/react-query';
import { todosService } from '../../services/todos';
import { pollinateService } from '../../services/pollinate';
import { pipelinesService } from '../../services/pipelines';
import { jobsService } from '../../services/jobs';
import { hiveService } from '../../services/hive';
import { skillsService } from '../../services/skills';
import { expertsService } from '../../services/experts';
import { mcpConfigService, type ConfigEntry } from '../../services/mcpConfig';
import type { SkillHealthMap, ExpertsByDomain } from '../../types';
import { communityService } from '../../services/community';
import api from '../../services/api';
import { chatService } from '../../services/chat';
import { agentsService } from '../../services/agents';
import { radarService } from '../../services/radar';
import { brainsWithPinnedQuery } from '../../services/ddd';
import { attentionService } from '../../services/attention';
import { workspaceLocationsService } from '../../services/workspace';

/** A JSON GET whose body is the data (typed by the overlay that reads it). */
const getJson = <T,>(url: string) => async (): Promise<T> => (await api.get<T>(url)).data;

/** The workspace Artifacts reads (one workspace today). */
export const ARTIFACTS_WORKSPACE = 'default';

/** Community's parts (one query each; the Inbound tab shows feed + hot topics). */
export type CommunityPart = 'feed' | 'hotTopics' | 'sources' | 'engagement';
const COMMUNITY_FETCH: Record<CommunityPart, () => Promise<unknown>> = {
  feed: () => communityService.fetchFeed(),
  hotTopics: () => communityService.fetchHotTopics(),
  sources: () => communityService.fetchSources(),
  engagement: () => communityService.fetchEngagement(),
};
import type { OverlayId } from './overlayIds';
import { loadOverlayChunk } from './overlayChunks';

/** The Workspace location shown last (this app run): the overlay reopens on it and
 *  the nav prefetches only it. */
let lastWorkspaceLocation = 'swarmws';
export const getLastWorkspaceLocation = (): string => lastWorkspaceLocation;
export const setLastWorkspaceLocation = (key: string): void => { lastWorkspaceLocation = key; };

/** One freshness window for every overlay: older data is still shown at once, then
 *  refreshed in the background. */
export const OVERLAY_STALE_MS = 30_000;

/** ToDo: the default range and the per-leg fetch cap (a leg at the cap is truncated). */
export const TODO_DEFAULT_DAYS = 30;
export const TODO_FETCH_CAP = 1000;

export const overlayQuery = {
  todo: (windowDays: number | null) => ({
    queryKey: ['overlay', 'todo', windowDays] as const,
    queryFn: async () => {
      // active + windowed terminal, fetched together (the overlay merges them)
      const [list, hist] = await Promise.all([
        todosService.list('swarmws', undefined, TODO_FETCH_CAP),
        todosService.history(TODO_FETCH_CAP, windowDays ?? undefined),
      ]);
      return { list, history: hist.todos };
    },
    staleTime: OVERLAY_STALE_MS,
  }),
  pollinate: () => ({
    queryKey: ['overlay', 'pollinate'] as const,
    queryFn: () => pollinateService.fetchAssets(),
    staleTime: OVERLAY_STALE_MS,
  }),
  pipelineAnalytics: (window: '30d') => ({
    queryKey: ['overlay', 'pipeline', 'analytics', window] as const,
    queryFn: () => pipelinesService.fetchAnalytics(window),
    staleTime: OVERLAY_STALE_MS,
  }),
  pipelineRunning: () => ({
    queryKey: ['overlay', 'pipeline', 'running'] as const,
    // a light, window-independent read: how many runs are running now
    queryFn: async () => (await pipelinesService.fetchActivePipelines()).filter((r) => r.status === 'running').length,
    staleTime: OVERLAY_STALE_MS,
  }),
  jobsRoster: () => ({
    queryKey: ['overlay', 'jobs', 'roster'] as const,
    queryFn: () => jobsService.fetchRoster(),
    staleTime: OVERLAY_STALE_MS,
  }),
  jobsOverview: () => ({
    queryKey: ['overlay', 'jobs', 'overview'] as const,
    queryFn: () => jobsService.fetchOverview(),
    staleTime: OVERLAY_STALE_MS,
  }),
  hiveAccounts: () => ({
    queryKey: ['overlay', 'hive', 'accounts'] as const,
    queryFn: () => hiveService.listAccounts(),
    staleTime: OVERLAY_STALE_MS,
  }),
  hiveInstances: () => ({
    queryKey: ['overlay', 'hive', 'instances'] as const,
    queryFn: () => hiveService.listInstances(),
    staleTime: OVERLAY_STALE_MS,
  }),
  capabilities: () => ({
    queryKey: ['overlay', 'capabilities', 'main'] as const,
    // an MCP list failure is not a panel failure: skills still show
    queryFn: async () => {
      const [skills, mcps] = await Promise.all([
        skillsService.list(),
        mcpConfigService.listAll().catch(() => [] as ConfigEntry[]),
      ]);
      return { skills, mcps };
    },
    staleTime: OVERLAY_STALE_MS,
  }),
  // health / experts never fail the query: a rejection is an empty map (fail-safe)
  capabilitiesHealth: () => ({
    queryKey: ['overlay', 'capabilities', 'health'] as const,
    queryFn: (): Promise<SkillHealthMap> => skillsService.getHealth().catch(() => ({})),
    staleTime: OVERLAY_STALE_MS,
  }),
  capabilitiesExperts: () => ({
    queryKey: ['overlay', 'capabilities', 'experts'] as const,
    queryFn: (): Promise<ExpertsByDomain> => expertsService.list().catch(() => ({})),
    staleTime: OVERLAY_STALE_MS,
  }),
  community: (part: CommunityPart) => ({
    queryKey: ['overlay', 'community', part] as const,
    // a null body is the service's error signal: make it, and a synchronous throw,
    // a rejected query (an empty [] / zero object is a success)
    queryFn: async () => {
      const res = await COMMUNITY_FETCH[part]();
      if (res === null) throw new Error(`community ${part} failed`);
      return res;
    },
    staleTime: OVERLAY_STALE_MS,
  }),
  // The five below keep the keys they already had (other code invalidates them).
  historySessions: (agentId: string | null) => ({
    queryKey: ['chatSessions', agentId] as const,
    queryFn: () => chatService.listSessions(agentId || undefined),
    staleTime: OVERLAY_STALE_MS,
  }),
  agents: () => ({ queryKey: ['agents'] as const, queryFn: () => agentsService.list(), staleTime: OVERLAY_STALE_MS }),
  artifactsProducts: () => ({
    queryKey: ['workspace-products'] as const,
    queryFn: () => radarService.fetchProducts(ARTIFACTS_WORKSPACE),
    staleTime: OVERLAY_STALE_MS,
  }),
  contextLite: <T,>() => ({
    queryKey: ['cm-brain-context-health-lite'] as const,
    // the THIN endpoint (token_block + counts) so the overlay opens instantly
    queryFn: getJson<T>('/eval/context-health/lite'),
    staleTime: OVERLAY_STALE_MS,
  }),
  contextTrend: <T,>() => ({ queryKey: ['cm-brain-trend'] as const, queryFn: getJson<T>('/eval/brain-trend'), staleTime: OVERLAY_STALE_MS }),
  libraryNative: <T,>() => ({ queryKey: ['library-native'] as const, queryFn: getJson<T>('/library/native'), staleTime: OVERLAY_STALE_MS }),
  libraryRecent: <T,>() => ({ queryKey: ['library-recent'] as const, queryFn: getJson<T>('/library/recent'), staleTime: OVERLAY_STALE_MS }),
  libraryMounts: <T,>() => ({ queryKey: ['library-mounts'] as const, queryFn: getJson<T>('/library/mounts'), staleTime: OVERLAY_STALE_MS }),
  /** Need You's queue (GET /attention). ONE cache for the overlay, the Welcome
   *  card and the AlertsPill (useAttentionQueue adds a 30s refetchInterval). */
  // Workspace: each location has its own keys (design workspace-working-location.md §4)
  workspaceLocations: () => ({
    queryKey: ['overlay', 'swarmws', 'locations'] as const,
    queryFn: () => workspaceLocationsService.list(),
    staleTime: OVERLAY_STALE_MS,
  }),
  workspaceSummary: (key: string) => ({
    queryKey: ['overlay', 'swarmws', key, 'summary'] as const,
    queryFn: () => workspaceLocationsService.summary(key),
    staleTime: OVERLAY_STALE_MS,
  }),
  workspaceRepoTree: (key: string) => ({
    queryKey: ['overlay', 'swarmws', key, 'tree'] as const,
    queryFn: () => workspaceLocationsService.tree(key, 1),
    staleTime: OVERLAY_STALE_MS,
  }),
  needsYou: () => ({
    queryKey: ['overlay', 'needs-you'] as const,
    queryFn: () => attentionService.fetchAttention(),
    staleTime: OVERLAY_STALE_MS,
  }),
};

type AnyOptions = { queryKey: readonly unknown[]; queryFn: () => Promise<unknown>; staleTime: number };

/** What prefetching needs from the app: the current agent (History is per agent). */
export interface PrefetchCtx { agentId?: string | null }

/** What a nav item prefetches: the overlay's default (first-render) queries. */
export const NAV_PREFETCH: Partial<Record<OverlayId, (ctx: PrefetchCtx) => AnyOptions[]>> = {
  history: ({ agentId }) => (agentId ? [overlayQuery.historySessions(agentId), overlayQuery.agents()] : []),
  artifacts: () => [overlayQuery.artifactsProducts()],
  context: () => [overlayQuery.contextLite(), overlayQuery.contextTrend()],
  library: () => [overlayQuery.libraryNative(), overlayQuery.libraryRecent(), overlayQuery.libraryMounts()],
  'brain-hub': () => [brainsWithPinnedQuery()],
  todo: () => [overlayQuery.todo(TODO_DEFAULT_DAYS)],
  pollinate: () => [overlayQuery.pollinate()],
  pipeline: () => [overlayQuery.pipelineAnalytics('30d'), overlayQuery.pipelineRunning()],
  jobs: () => [overlayQuery.jobsRoster(), overlayQuery.jobsOverview()],
  hive: () => [overlayQuery.hiveAccounts(), overlayQuery.hiveInstances()],
  capabilities: () => [overlayQuery.capabilities(), overlayQuery.capabilitiesHealth()],
  community: () => [overlayQuery.community('feed'), overlayQuery.community('hotTopics')],
  'needs-you': () => [overlayQuery.needsYou()],
  // the list + the last-shown location's own summary (its tree loads on open)
  swarmws: () => [overlayQuery.workspaceLocations(), overlayQuery.workspaceSummary(lastWorkspaceLocation)],
};

/** Prefetch a nav item's overlay code and data (no-op when loaded / fresh). Never throws. */
export function prefetchNav(qc: QueryClient, id: OverlayId, ctx: PrefetchCtx = {}): void {
  loadOverlayChunk(id);   // the overlay's code (overlayChunks), then its data
  for (const opts of NAV_PREFETCH[id]?.(ctx) ?? []) {
    void qc.prefetchQuery(opts).catch(() => { /* the overlay's own query reports it */ });
  }
}

/** Mark an overlay's data stale after a write; an open overlay refetches. */
export function invalidateOverlay(qc: QueryClient, id: string): Promise<void> {
  return qc.invalidateQueries({ queryKey: ['overlay', id] });
}
