/**
 * canvasChanges — the Canvas "Changes" tab's API (backend routers/canvas_changes,
 * core/repo_changes): each bound code repo's local state, read-only.
 *
 * @exports canvasChangesService, ChangesRepo, ChangesCommit, ChangesWorktree, CommitFile
 */
import api from './api';

export interface ChangesCommit {
  sha: string;
  short: string;
  subject: string;
  at: string;
  files: number;
  insertions: number;
  deletions: number;
}

export interface ChangesWorktree {
  path: string;
  branch: string | null;
  prunable: boolean;
  detached: boolean;
}

export interface ChangesRepo {
  name: string;
  project: string | null;
  root: string;
  branch?: string;
  upstream?: string | null;
  /** null: the branch has no upstream. */
  ahead?: number | null;
  dirty_count?: number;
  dirty?: Array<{ path: string; status: string }>;
  worktrees?: ChangesWorktree[];
  commits?: ChangesCommit[];
  /** Set instead of the fields above when git failed for this repo. */
  error?: string;
}

export interface CommitFile {
  path: string;
  insertions: number | null;
  deletions: number | null;
}

export const canvasChangesService = {
  async repos(): Promise<ChangesRepo[]> {
    const r = await api.get<{ repos: ChangesRepo[] }>('/canvas/changes');
    return r.data?.repos ?? [];
  },
  async commitFiles(root: string, sha: string): Promise<CommitFile[]> {
    const r = await api.get<{ files: CommitFile[] }>('/canvas/changes/commit', { params: { root, sha } });
    return r.data?.files ?? [];
  },
  async commitDiff(root: string, sha: string, path: string): Promise<{ diff: string; truncated: boolean }> {
    const r = await api.get<{ diff: string; truncated: boolean }>('/canvas/changes/diff', { params: { root, sha, path } });
    return r.data ?? { diff: '', truncated: false };
  },
};
