/**
 * FileViewer — Unified file viewer orchestrator for SwarmAI.
 *
 * Replaces the old FileEditorPanel + BinaryPreviewModal split with a single
 * tabbed surface that routes to the correct renderer based on file type.
 *
 * Architecture:
 *   FileViewer
 *     FileViewerTabBar        — horizontal tab strip (top)
 *     Renderer Area           — flex-1 middle area
 *       text/markdown/svg     -> FileEditorCore (existing, strangler-fig wrap)
 *       image/pdf/html/...    -> lazy-loaded per-type renderers
 *       unsupported           -> UnsupportedRenderer
 *     FileViewerStatusBar     — slim info bar (bottom)
 *
 * The text/markdown/svg path delegates to the *existing* FileEditorCore —
 * no editor logic is duplicated here. FileViewer just wraps it with tabs
 * and a status bar.
 */

import {
  memo,
  useState,
  useEffect,
  useCallback,
  useRef,
  lazy,
  Suspense,
} from 'react';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { isEditableType, isBinaryType } from './utils/fileViewTypes';
import { useFileViewerTabs } from './hooks/useFileViewerTabs';
import type { FileTab } from './hooks/useFileViewerTabs';
import FileViewerTabBar from './FileViewerTabBar';
import FileViewerStatusBar from './FileViewerStatusBar';
import FileEditorCore from '../common/FileEditorCore';
import { fileIcon, fileIconColor } from '../../utils/fileUtils';
import api from '../../services/api';

/* ------------------------------------------------------------------ */
/*  Lazy-loaded renderers (code-split per type)                        */
/* ------------------------------------------------------------------ */

const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));
const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const VideoRenderer = lazy(() => import('./renderers/VideoRenderer'));
const AudioRenderer = lazy(() => import('./renderers/AudioRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const UnsupportedRenderer = lazy(() => import('./renderers/UnsupportedRenderer'));

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface FileViewerProps {
  /** Initial file to open (e.g. from Explorer double-click). */
  initialFile?: {
    filePath: string;
    fileName: string;
    gitStatus?: GitStatus;
    workspaceId?: string;
    /** Open directly on the diff view (Radar ✍ Changes click). */
    autoDiff?: boolean;
    /** Git ref to diff AGAINST (run_030dc98e): a source-final row's `<sha>^`, so a
     *  just-committed file's diff baseline is its pre-run parent, not HEAD (== working
     *  tree → empty). Passed to /workspace/file/committed?ref=. Absent → HEAD. */
    baseRef?: string;
  };
  onClose: () => void;
  onAttachToChat?: (item: FileTreeItem) => void;
  onSaveWithDiff?: (diffSummary: string, fileName?: string) => void;
  /** Whether this viewer is mounted as a resizable side panel or fullscreen overlay. */
  variant: 'panel' | 'modal';
  /** Toggle between panel and modal mode. */
  onToggleMode?: () => void;
  /** Chat-tab scope identity. When it CHANGES, FileViewer clears its internal
   *  tab list so one chat tab's open files don't bleed into another — WITHOUT a
   *  remount (a `key` remount would replay the panel's width-reveal animation and
   *  discard the content cache on every tab switch, run_0fb40bbc). Undefined =
   *  no scoping (single-instance callers). */
  tabScopeKey?: string;
}

/** Status-bar info that renderers can publish via onStatusInfo callback. */
export interface StatusBarInfo {
  fileSize: number;
  encoding?: string;
  extraInfo?: Record<string, string>;
}

/** Response shape from GET /workspace/file. */
interface FileResponse {
  content: string;
  encoding?: string;
  mime_type?: string;
  mimeType?: string;
  size?: number;
  name: string;
  path: string;
  readonly?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Loading spinner                                                    */
/* ------------------------------------------------------------------ */

function LoadingFallback() {
  return (
    <div className="flex-1 flex items-center justify-center text-[var(--color-text-secondary)]">
      <span className="material-symbols-outlined animate-spin text-2xl mr-2">progress_activity</span>
      <span className="text-sm">Loading...</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  FileViewer                                                         */
/* ------------------------------------------------------------------ */

function FileViewerImpl({
  initialFile,
  onClose,
  onAttachToChat,
  onSaveWithDiff,
  variant,
  onToggleMode,
  tabScopeKey,
}: FileViewerProps) {
  const {
    tabs,
    activeTab,
    openTab,
    closeTab,
    closeAllTabs,
    switchTab,
    markDirty,
  } = useFileViewerTabs();

  /* ---- Per-tab content cache ---- */
  // Keeps fetched content keyed by filePath so we don't re-fetch on tab switch.
  const contentCache = useRef<
    Record<
      string,
      {
        content: string;
        encoding?: string;
        size: number;
        mimeType?: string;
        readonly?: boolean;
        committedContent?: string;
        /** PHYSICAL absolute path (from /workspace/file/meta, run_405d221c) — what
         *  the OS opener needs for Open-in-System-App / Reveal / Copy-Path. Only
         *  populated for the unsupported-file meta fetch; undefined elsewhere. */
        absolutePath?: string;
      }
    >
  >({}); // React 19 requires initial value

  /* ---- Status bar info (updated by renderers) ---- */
  const [statusBarInfo, setStatusBarInfo] = useState<StatusBarInfo>({
    fileSize: 0,
  });

  /* ---- Content loading state for the active tab ---- */
  const [loadingContent, setLoadingContent] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);

  /* ---- Refetch nonce (bug #1: cache invalidation on rewrite) ---- */
  // Deleting a contentCache entry (a useRef) is invisible to React's effect deps,
  // so the fetch effect below (deps [filePath, viewType, …]) would NOT re-run on a
  // cache delete. Bumping this nonce is what actually forces the re-fetch.
  const [refetchNonce, setRefetchNonce] = useState(0);

  /* ---- Unified close: closeSignal (run_f49d3ff3 R2) ---- */
  // The type-agnostic file-chrome header (below) owns the ONE close affordance for
  // ALL viewTypes. For editor types (text/md/svg → FileEditorCore) the close MUST go
  // through FileEditorCore's existing unsaved-changes guard (handleCloseAttempt) — so
  // instead of calling handleCloseActive directly (which would silently no-op a dirty
  // tab, useFileViewerTabs:107), we bump this counter, passed to FileEditorCore, whose
  // effect runs its guarded close. For non-editor types (read-only, never dirty) the
  // header calls handleCloseActive directly. Gate-1 amendment 1: FileEditorCore is
  // rendered with key={filePath} (below), so a tab switch REMOUNTS it fresh — a stale
  // counter meant for tab A cannot fire on tab B (the new instance treats the current
  // value as its mount baseline and the >0/skip-mount guard in FileEditorCore ignores it).
  const [closeSignal, setCloseSignal] = useState(0);

  /* ---- workspaceId placeholder (FileEditorCore needs it) ---- */
  // The unified viewer does not depend on workspaceId for routing, but
  // FileEditorCore requires it for attach-to-chat. We pass '' as a
  // default; the parent can enrich initialFile if needed.
  const workspaceIdRef = useRef<string>('');

  /* -------------------------------------------------------------- */
  /*  Open initial file when prop changes                            */
  /* -------------------------------------------------------------- */

  const prevInitialFileRef = useRef<string | undefined>(undefined);

  // Chat-tab scope change → clear the internal tab list so tab A's open files
  // don't linger as tabs in tab B (the bleed the old key={activeTabId} remount
  // guarded). Done WITHOUT a remount, so the panel's width-reveal animation +
  // the content cache survive a tab switch (run_0fb40bbc). Reset
  // prevInitialFileRef too, so the incoming tab's initialFile re-opens even if
  // its path matches what the previous tab last showed. Skip the very first
  // render (prev undefined→first key) so a freshly-opened Canvas isn't wiped.
  const prevScopeRef = useRef<string | undefined>(tabScopeKey);
  useEffect(() => {
    if (prevScopeRef.current !== undefined && tabScopeKey !== prevScopeRef.current) {
      closeAllTabs();
      prevInitialFileRef.current = undefined;
    }
    prevScopeRef.current = tabScopeKey;
  }, [tabScopeKey, closeAllTabs]);

  useEffect(() => {
    if (!initialFile) {
      // initialFile cleared (e.g. Canvas file nulled while the panel stays open
      // because it was manually opened) → close the stale surface instead of
      // leaving the last file rendered (Gate-2 HIGH). Guard on prev so we only
      // clear on the null TRANSITION, not on every render while already empty.
      if (prevInitialFileRef.current !== undefined) {
        prevInitialFileRef.current = undefined;
        closeAllTabs();
      }
      return;
    }
    if (prevInitialFileRef.current === initialFile.filePath) return;
    prevInitialFileRef.current = initialFile.filePath;
    // Capture workspaceId for FileEditorCore's attach-to-chat
    if (initialFile.workspaceId) {
      workspaceIdRef.current = initialFile.workspaceId;
    }
    openTab(initialFile.filePath, initialFile.fileName, initialFile.gitStatus);
  }, [initialFile, openTab, closeAllTabs]);

  /* -------------------------------------------------------------- */
  /*  Fetch content for active tab                                   */
  /* -------------------------------------------------------------- */

  useEffect(() => {
    if (!activeTab) return;

    // Already cached
    if (contentCache.current[activeTab.filePath]) {
      const cached = contentCache.current[activeTab.filePath];
      setStatusBarInfo({
        fileSize: cached.size,
        encoding: cached.encoding,
      });
      setContentError(null);
      return;
    }

    let cancelled = false;

    async function fetchFileContent(tab: FileTab) {
      setLoadingContent(true);
      setContentError(null);

      try {
        if (isBinaryType(tab.viewType) && tab.viewType !== 'unsupported') {
          // Media (image/pdf/video/audio) STREAM from /workspace/file/raw inside
          // their renderers (Cycle C, run_b454ce39) — we no longer pull the whole
          // file as base64-in-JSON (+33% over the wire, a decoded copy pinned in
          // this cache until tab close). Fetch metadata ONLY for the status bar;
          // the renderer builds its own raw URL from filePath. (video/audio already
          // ignored the base64 content — this stops fetching it for them too.)
          try {
            const resp = await api.get<{ size: number; mime_type: string }>(
              '/workspace/file/meta',
              { params: { path: tab.filePath } },
            );
            if (cancelled) return;
            contentCache.current[tab.filePath] = {
              content: '',
              size: resp.data.size ?? 0,
              mimeType: resp.data.mime_type,
            };
            setStatusBarInfo({ fileSize: resp.data.size ?? 0 });
          } catch (err) {
            if (cancelled) return;
            // /meta 413s an oversized file (>50 MB) — preserve the friendly
            // "too large, open locally" message the base64 path used to show,
            // instead of silently mounting a renderer that fails to stream.
            const status = (err as { response?: { status?: number; data?: { detail?: string } } })?.response?.status;
            if (status === 413) {
              const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
              setContentError(detail || 'File too large to preview here. Open it in a local app instead.');
              return;
            }
            // Other metadata errors are best-effort — the renderer still streams via raw URL.
            contentCache.current[tab.filePath] = { content: '', size: 0 };
            setStatusBarInfo({ fileSize: 0 });
          }
        } else if (tab.viewType === 'unsupported') {
          // Unsupported — metadata only
          try {
            const resp = await api.get<{ size: number; mime_type: string; absolute_path?: string }>(
              '/workspace/file/meta',
              { params: { path: tab.filePath } },
            );
            if (cancelled) return;
            contentCache.current[tab.filePath] = {
              content: '',
              size: resp.data.size ?? 0,
              mimeType: resp.data.mime_type,
              absolutePath: resp.data.absolute_path,
            };
            setStatusBarInfo({ fileSize: resp.data.size ?? 0 });
          } catch {
            // Metadata fetch is best-effort for unsupported files
            if (cancelled) return;
            contentCache.current[tab.filePath] = { content: '', size: 0 };
            setStatusBarInfo({ fileSize: 0 });
          }
        } else {
          // Text / markdown / svg / html / csv — text fetch
          const resp = await api.get<FileResponse>('/workspace/file', {
            params: { path: tab.filePath },
          });
          if (cancelled) return;

          const d = resp.data;
          let committedContent: string | undefined;

          // Fetch the committed baseline for the diff (editable types). When THIS tab
          // is the opened file AND it carries a baseRef (a source-final row's `<sha>^`,
          // run_030dc98e), diff against that pre-run parent instead of HEAD — else a
          // just-committed file diffs HEAD-vs-working-tree (identical → empty). Only the
          // initialFile's own path uses its baseRef; other tabs keep the HEAD baseline.
          if (isEditableType(tab.viewType)) {
            const baseRef =
              initialFile && tab.filePath === initialFile.filePath
                ? initialFile.baseRef
                : undefined;
            try {
              const cResp = await api.get<{ content: string }>(
                '/workspace/file/committed',
                { params: { path: tab.filePath, ...(baseRef ? { ref: baseRef } : {}) } },
              );
              if (!cancelled) committedContent = cResp.data.content;
            } catch {
              // New/untracked file — no committed version
            }
          }

          if (cancelled) return;
          contentCache.current[tab.filePath] = {
            content: d.content,
            encoding: d.encoding ?? 'utf-8',
            size: d.size ?? new TextEncoder().encode(d.content).length,
            readonly: d.readonly,
            committedContent,
          };
          setStatusBarInfo({
            fileSize: d.size ?? new TextEncoder().encode(d.content).length,
            encoding: d.encoding ?? 'utf-8',
          });
        }
      } catch (err) {
        if (cancelled) return;
        // The api-service interceptor throws an ApiError carrying `.statusCode`
        // (NOT `.response.status` — axios's shape is normalized away before it
        // reaches here). Read statusCode so status-specific handling actually
        // fires (the old `.response?.status` was always undefined for an
        // ApiError → the 413 branch below was dead, run_7f6539b5).
        const status =
          (err as { statusCode?: number })?.statusCode ??
          (err as { response?: { status?: number } })?.response?.status;
        // #4: the backend stat-gates at 50 MB and returns HTTP 413 BEFORE reading
        // the file (workspace_api.get_workspace_file) — so an oversized binary never
        // streams a huge base64 into memory. Surface that as a helpful "open locally"
        // message rather than a raw axios error string.
        if (status === 413) {
          // ApiError exposes the backend detail via a `.detail` getter
          // (response.detail) — NOT `.response.data.detail` (axios's shape is
          // gone by here, so the old read was always undefined → generic
          // fallback only, run_7f6539b5 Gate-2 MED).
          const detail = (err as { detail?: string })?.detail;
          setContentError(detail || 'File too large to preview here. Open it in a local app instead.');
          return;
        }
        // 404: the file is gone (deleted on disk / moved). A filesystem/CLI delete
        // does NOT emit a swarm:file-changed event, so the friendly deleted-notice
        // (see the swarm:file-changed listener below) never fires — the fetch just
        // 404s and the raw interceptor string ("Resource not found") is useless to
        // the user. Show a friendly, actionable notice locally (NOT by editing the
        // shared api.ts 404 interceptor, which 9 other callers depend on).
        if (status === 404) {
          setContentError('This file is no longer available — it may have been moved or deleted. Close this tab.');
          return;
        }
        const msg = err instanceof Error ? err.message : 'Failed to load file';
        setContentError(msg);
      } finally {
        if (!cancelled) setLoadingContent(false);
      }
    }

    fetchFileContent(activeTab);
    return () => {
      cancelled = true;
    };
    // refetchNonce forces a re-run when the open file is rewritten (bug #1) —
    // deleting the cache ref alone is invisible to React's deps.
  }, [activeTab?.filePath, activeTab?.viewType, refetchNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  /* -------------------------------------------------------------- */
  /*  Auto-refresh on rewrite (bug #1, run_a400d951)                 */
  /* -------------------------------------------------------------- */
  // When the agent RE-WRITES a file already open here, the per-filePath
  // contentCache would otherwise serve STALE content forever (the fetch effect's
  // deps don't change on a re-open, and openTab dedups a same-path re-open). We
  // listen for the unified swarm:file-changed and invalidate the matching cache
  // entr(y|ies), then bump refetchNonce to re-run the fetch for the active tab.
  //
  // Scope (Gate-1): text/markdown/svg delegate to FileEditorCore, which has its
  // OWN swarm:file-changed listener (FileEditorCore.tsx:680) that refetches +
  // highlights changed lines while protecting unsaved edits. Handling those here
  // too would double-fetch and fight that logic — so we SKIP them and only act on
  // FileViewer's own contentCache renderers (image/pdf/video/audio/csv/html/unsupported).
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ path: string; operation?: string }>).detail;
      const changedPath = detail?.path;
      if (!changedPath) return;
      // G1 (run_5a7be540): a DELETE of the file open here → don't keep showing stale
      // content (or spin forever trying to refetch a gone file). Drop its cache and
      // surface a clear "deleted" notice. Anchored match (exact / event-abs endsWith
      // stored-rel), never bare basename. Non-active matches are just cache-dropped.
      if (detail?.operation === 'deleted') {
        const isActiveMatch =
          activeTab != null &&
          (changedPath === activeTab.filePath || changedPath.endsWith(`/${activeTab.filePath}`));
        for (const cp of Object.keys(contentCache.current)) {
          if (cp === changedPath || changedPath.endsWith(`/${cp}`)) delete contentCache.current[cp];
        }
        if (isActiveMatch) {
          setContentError('This file was deleted. Close this tab or open another file.');
          // Clear the dirty flag: the file is gone from disk, so its unsaved buffer
          // can't be saved anywhere. Without this, closeTab no-ops on the dirty guard
          // and a multi-tab panel leaves the dead tab stuck open (run_7f6539b5 Gate-2
          // LOW; single-tab was masked by the tabs.length<=1 → onClose fallback).
          markDirty(activeTab.id, false);
        }
        return;
      }
      // The ACTIVE tab, if it's a FileEditorCore type (text/md/svg), self-refreshes
      // via FileEditorCore's own listener — AND its cache entry must NOT be deleted
      // here: FileViewer renders <LoadingFallback> on a cache miss (renderActiveContent
      // `if (!cached)`), which would unmount FileEditorCore into a spinner and kill
      // its in-place refresh. So we leave that one entry alone.
      const activeIsEditorCoreType =
        activeTab != null &&
        (activeTab.viewType === 'text' ||
          activeTab.viewType === 'markdown' ||
          activeTab.viewType === 'svg');
      // Match mirrors FileEditorCore:648 EXACTLY (asymmetric): exact, or the
      // event's (absolute) path ends with the cached (workspace-relative) path.
      // Gate-2 HIGH: an earlier bidirectional form also tested
      // `cachedPath.endsWith('/'+changedPath)`, which FALSE-MATCHES a shorter
      // changedPath ('deep/foo.ts') against an unrelated longer cachedPath
      // ('x/deep/foo.ts') — deleting the wrong file's cache. swarm:file-changed
      // emits RESOLVED ABSOLUTE paths (streaming_orchestrator), so the asymmetric
      // form is sufficient and collision-free.
      const matches = (cachedPath: string) =>
        changedPath === cachedPath || changedPath.endsWith(`/${cachedPath}`);
      // Invalidate ALL matching cache entries (not just the active tab) so a
      // background tab is fresh when switched to (the filePath-dep change on switch
      // triggers its own refetch). EXCEPT the active FileEditorCore-type entry (above).
      let activeMatched = false;
      for (const cachedPath of Object.keys(contentCache.current)) {
        if (!matches(cachedPath)) continue;
        const isActive = cachedPath === activeTab?.filePath;
        if (isActive) {
          activeMatched = true;
          if (activeIsEditorCoreType) continue; // keep it — FileEditorCore refreshes in place
        }
        delete contentCache.current[cachedPath];
      }
      // Only the ACTIVE tab is mounted, so only its refetch needs forcing — and only
      // for FileViewer's own contentCache renderers (not the FileEditorCore types).
      if (activeMatched && activeTab && !activeIsEditorCoreType) {
        setRefetchNonce((n) => n + 1);
      }
    };
    window.addEventListener('swarm:file-changed', handler);
    return () => window.removeEventListener('swarm:file-changed', handler);
  }, [activeTab?.filePath, activeTab?.viewType]);

  /* -------------------------------------------------------------- */
  /*  Save handler for text-editable files                           */
  /* -------------------------------------------------------------- */

  const handleSave = useCallback(
    async (content: string) => {
      if (!activeTab) return;
      await api.put('/workspace/file', { content }, {
        params: { path: activeTab.filePath },
      });
      // Update cache with new content
      const cached = contentCache.current[activeTab.filePath];
      if (cached) {
        cached.content = content;
        cached.size = new TextEncoder().encode(content).length;
      }
      markDirty(activeTab.id, false);
    },
    [activeTab, markDirty],
  );

  /* -------------------------------------------------------------- */
  /*  Close handler (called by tab bar and editor)                   */
  /* -------------------------------------------------------------- */

  const handleCloseTab = useCallback(
    (tabId: string) => {
      closeTab(tabId);
      // Remove cached content
      const tab = tabs.find((t) => t.id === tabId);
      if (tab) {
        delete contentCache.current[tab.filePath];
      }
      // If no tabs remain, close the viewer entirely
      if (tabs.length <= 1) {
        onClose();
      }
    },
    [closeTab, tabs, onClose],
  );

  /** Close the active tab (used by FileEditorCore's onClose). */
  const handleCloseActive = useCallback(() => {
    if (activeTab) {
      handleCloseTab(activeTab.id);
    } else {
      onClose();
    }
  }, [activeTab, handleCloseTab, onClose]);

  /* -------------------------------------------------------------- */
  /*  Content change tracking (marks tab dirty)                      */
  /* -------------------------------------------------------------- */

  const handleContentChange = useCallback(
    (content: string) => {
      if (!activeTab) return;
      const cached = contentCache.current[activeTab.filePath];
      const originalContent = cached?.content ?? '';
      markDirty(activeTab.id, content !== originalContent);
    },
    [activeTab, markDirty],
  );

  /* -------------------------------------------------------------- */
  /*  Status info callback for renderers                             */
  /* -------------------------------------------------------------- */

  /**
   * Adapts the per-renderer onStatusInfo shapes (dimensions, pageInfo,
   * rowColCount, customInfo) into the unified StatusBarInfo.extraInfo map.
   */
  const handleStatusInfo = useCallback(
    (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => {
      const extraInfo: Record<string, string> = {};
      if (info.dimensions) extraInfo['Dimensions'] = info.dimensions;
      if (info.pageInfo) extraInfo['Page'] = info.pageInfo;
      if (info.rowColCount) extraInfo['Size'] = info.rowColCount;
      if (info.customInfo) extraInfo['Info'] = info.customInfo;
      setStatusBarInfo((prev) => ({
        ...prev,
        extraInfo: Object.keys(extraInfo).length > 0 ? extraInfo : prev.extraInfo,
      }));
    },
    [],
  );

  /* -------------------------------------------------------------- */
  /*  Render the appropriate component for the active tab            */
  /* -------------------------------------------------------------- */

  function renderActiveContent() {
    if (!activeTab) {
      return (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-secondary)] text-sm">
          No file open
        </div>
      );
    }

    if (loadingContent) {
      return <LoadingFallback />;
    }

    if (contentError) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-[var(--color-text-secondary)]">
          <span className="material-symbols-outlined text-3xl text-red-400">error</span>
          <span className="text-sm">{contentError}</span>
        </div>
      );
    }

    const cached = contentCache.current[activeTab.filePath];
    if (!cached) return <LoadingFallback />;

    const { viewType, filePath, fileName, gitStatus } = activeTab;

    // --- Text / Markdown / SVG: delegate to existing FileEditorCore ---
    if (viewType === 'text' || viewType === 'markdown' || viewType === 'svg') {
      return (
        <FileEditorCore
          key={filePath}
          filePath={filePath}
          fileName={fileName}
          workspaceId={workspaceIdRef.current}
          initialContent={cached.content}
          committedContent={cached.committedContent}
          onSave={handleSave}
          onClose={handleCloseActive}
          gitStatus={gitStatus}
          onAttachToChat={onAttachToChat}
          readonly={cached.readonly}
          // Auto-show the diff on open ONLY when we actually have a committed
          // (HEAD) baseline to diff against. Without one — an untracked file, or
          // a path git couldn't resolve — forcing the diff view would render an
          // empty/misleading "no changes" panel (originalContent === content).
          // Fail-soft to the normal edit view instead. (#6 fix)
          initialShowDiff={
            !!initialFile?.autoDiff &&
            initialFile.filePath === filePath &&
            !!cached.committedContent
          }
          variant={variant}
          onToggleMode={onToggleMode}
          onSaveWithDiff={onSaveWithDiff}
          onContentChange={handleContentChange}
          // R2: the unified file-chrome header (panel) owns close. In panel variant
          // FileEditorCore suppresses its OWN filename+close (keeps its control cluster),
          // and the header's close bumps closeSignal → FileEditorCore runs its existing
          // guarded close (handleCloseAttempt), so the unsaved-changes dialog is preserved.
          closeSignal={closeSignal}
        />
      );
    }

    // --- Non-text types: lazy-loaded renderers ---
    const rendererProps = {
      filePath,
      fileName,
      content: cached.content,
      encoding: (cached.encoding ?? 'base64') as 'utf-8' | 'base64',
      mimeType: cached.mimeType ?? 'application/octet-stream',
      fileSize: cached.size,
      onStatusInfo: handleStatusInfo,
      // Physical absolute path for OS-level actions (open/reveal/copy). Common to
      // ALL renderers (run_405d221c) — Unsupported uses it today; any future
      // renderer that adds an "open locally" action inherits it for free.
      absolutePath: cached.absolutePath,
    };

    return (
      <Suspense fallback={<LoadingFallback />}>
        {viewType === 'image' && <ImageRenderer {...rendererProps} />}
        {viewType === 'pdf' && <PdfRenderer {...rendererProps} />}
        {viewType === 'html-preview' && <HtmlRenderer {...rendererProps} />}
        {viewType === 'video' && <VideoRenderer {...rendererProps} />}
        {viewType === 'audio' && <AudioRenderer {...rendererProps} />}
        {viewType === 'csv' && <CsvRenderer {...rendererProps} />}
        {viewType === 'unsupported' && (
          <UnsupportedRenderer
            {...rendererProps}
            onAttachToChat={onAttachToChat ? (path: string) => {
              onAttachToChat({
                id: path,
                name: fileName,
                type: 'file',
                path,
                workspaceId: workspaceIdRef.current,
                workspaceName: '',
              });
            } : undefined}
          />
        )}
      </Suspense>
    );
  }

  /* -------------------------------------------------------------- */
  /*  Layout                                                         */
  /* -------------------------------------------------------------- */

  // Which view types delegate to FileEditorCore, which renders its OWN footer
  // (language chip + Save/Close). For these, a FileViewerStatusBar below would be
  // a SECOND footer (the double-footer bug). This is the EXACT set routed to
  // FileEditorCore at renderActiveContent() — NOT isEditableType(), which also
  // includes 'csv' → CsvRenderer (a lazy renderer with NO own footer, so csv
  // still needs the status bar). Gate-1 CRITICAL catch (run_09431085).
  const hasOwnFooter =
    activeTab != null &&
    (activeTab.viewType === 'text' ||
      activeTab.viewType === 'markdown' ||
      activeTab.viewType === 'svg');

  // Editor types (FileEditorCore) are the ONLY ones that can be dirty; their close
  // must run FileEditorCore's unsaved-guard. Non-editor renderers are read-only.
  const isEditorType =
    activeTab != null &&
    (activeTab.viewType === 'text' ||
      activeTab.viewType === 'markdown' ||
      activeTab.viewType === 'svg');

  // The ONE close affordance (run_f49d3ff3 R2, panel variant). Editor type → delegate
  // to FileEditorCore's guarded close via closeSignal; non-editor → close directly.
  const handleUnifiedClose = useCallback(() => {
    // Editor types delegate to FileEditorCore's guarded close via closeSignal —
    // BUT only when FileEditorCore is actually mounted. In the contentError state
    // renderActiveContent() returns an error placeholder and does NOT mount
    // FileEditorCore, so the sole closeSignal consumer is absent and the bump is a
    // no-op → the dead tab could never be closed (run_7f6539b5). A tab in
    // contentError has no live editor and thus no unsaved edits to guard, so close
    // it DIRECTLY. Non-error editor tab → keep the guarded path (unsaved dialog).
    if (isEditorType && !contentError) setCloseSignal((n) => n + 1);
    else handleCloseActive();
  }, [isEditorType, contentError, handleCloseActive]);

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]">
      {/* Tab bar — the Canvas OUTPUTS list is the file selector in panel variant,
          so the redundant horizontal tab strip is only for the modal variant.
          (v6 Canvas redesign, run_09431085.) */}
      {variant === 'modal' && (
        <FileViewerTabBar
          tabs={tabs}
          activeTabId={activeTab?.id ?? null}
          onSwitch={switchTab}
          onClose={handleCloseTab}
        />
      )}

      {/* Unified file-chrome header (run_f49d3ff3 R2) — the ONE type-agnostic close
          affordance for EVERY viewType in the Canvas PANEL. Before this, close was
          scattered: FileEditorCore's own header (text/md/svg), FileViewerStatusBar's
          footer (the reclaimed run_5f5e7675 patch, html/img/pdf/csv), and NO close at
          all in the panel's top chrome. Now every open file — regardless of type —
          closes from here. PANEL-ONLY (Gate-1 amendment 3): the modal variant keeps its
          tab-bar × + FileEditorCore's own header, so this row must NOT stack there. */}
      {variant === 'panel' && activeTab && (
        <div
          className="flex items-center gap-2 px-3 h-9 shrink-0 border-b border-[var(--color-border)] bg-[var(--color-bg)]"
          data-testid="file-chrome-header"
        >
          <span
            className="material-symbols-outlined text-[15px] leading-none shrink-0"
            style={{ color: fileIconColor(activeTab.fileName) }}
            aria-hidden="true"
          >
            {fileIcon(activeTab.fileName)}
          </span>
          <span className="text-[13px] font-medium truncate min-w-0 flex-1 text-[var(--color-text)]" title={activeTab.filePath}>
            {/* Fallback for a nameless / dir-like path so the header never shows an empty
                label (Gate-2 LOW): fileName can be '' for a trailing-slash path. */}
            {activeTab.fileName || activeTab.filePath || 'Untitled'}
          </span>
          {activeTab.isDirty && (
            <span
              className="w-1.5 h-1.5 rounded-full bg-[var(--color-warning)] shrink-0"
              title="Unsaved changes"
              data-testid="file-chrome-dirty"
              aria-label="Unsaved changes"
            />
          )}
          <button
            onClick={handleUnifiedClose}
            className="shrink-0 p-1 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
            title="Close this file"
            aria-label="Close file"
            data-testid="file-chrome-close"
          >
            <span className="material-symbols-outlined text-[16px] leading-none">close</span>
          </button>
        </div>
      )}

      {/* Renderer area */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {renderActiveContent()}
      </div>

      {/* Status bar — ONE footer only. Suppressed for text/markdown/svg because
          FileEditorCore already renders its own footer for those (double-footer
          dedup); kept for binary/csv renderers that have no footer of their own. */}
      {activeTab && !loadingContent && !contentError && !hasOwnFooter && (
        <FileViewerStatusBar
          fileName={activeTab.fileName}
          fileSize={statusBarInfo.fileSize}
          viewType={activeTab.viewType}
          encoding={statusBarInfo.encoding}
          extraInfo={statusBarInfo.extraInfo}
          // Non-text renderers have no footer of their own → surface file ops here
          // (parity with FileEditorCore's header for text/md/svg). copy-path uses
          // filePath; attach dispatches the SAME decoupled window event the
          // Explorer uses (swarm:attach-file → ChatPage → addWorkspaceFiles), so no
          // prop-threading through the Canvas host is needed. Prefer onAttachToChat
          // when the caller wired it (modal/single-instance); else the event.
          filePath={activeTab.filePath}
          onAttach={() => {
            const item: FileTreeItem = {
              id: activeTab.filePath,
              name: activeTab.fileName,
              type: 'file',
              path: activeTab.filePath,
              workspaceId: workspaceIdRef.current,
              workspaceName: '',
            };
            if (onAttachToChat) onAttachToChat(item);
            else window.dispatchEvent(new CustomEvent('swarm:attach-file', { detail: item }));
          }}
          // NOTE (run_f49d3ff3 R2): the status-bar close button (added run_5f5e7675)
          // is REMOVED. Close is now a single type-agnostic affordance in the unified
          // file-chrome header above — the status bar is info + copy/attach only again.
        />
      )}
    </div>
  );
}

// LOAD-BEARING memo (run_4b67c510): during a Canvas resize DRAG, the parent
// FileViewerPanel calls setWidth once per animation frame (rAF-throttled). Without
// this memo, each of those re-renders reconciles the entire FileViewer subtree
// (FileEditorCore ~1847 lines + xterm + HTML iframe renderers) every frame, causing
// visible drag jank with a large file / HTML artifact open. All props from the
// FileViewerPanel call site are referentially stable across a width-only re-render
// (initialFile = useCanvasHost slice.file — a stable ref during a drag, only patch()
// mutates it; onClose = useCallback; tabScopeKey primitive; variant literal), so the
// default shallow compare correctly skips the subtree. Do NOT remove without
// eliminating the per-frame width re-render at its source.
export default memo(FileViewerImpl);
