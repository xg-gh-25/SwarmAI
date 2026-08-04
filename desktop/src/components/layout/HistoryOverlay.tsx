/**
 * HistoryOverlay — the left-nav "History" surface: a READ-ONLY browser.
 *
 * Opens on `swarm:show-history` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). Two panes inside the fullscreen Modal:
 *   • LEFT  — the searchable session list (HistoryView; content-FTS search box,
 *             empty query → time-grouped fallback).
 *   • RIGHT — a READ-ONLY preview of the selected session's messages.
 *
 * Clicking a list row PREVIEWS the session in-place — it does NOT occupy a chat
 * tab, switch agent, or touch the active (possibly streaming) tab. The preview
 * uses pure local state (never messageStoreRegistry — that's tabId-keyed).
 *
 * "Resume in tab" is the ONLY tab-occupying action. It delegates the landing
 * decision + execution to `onResume` (ChatPage), which focuses an already-open
 * tab / opens a new tab / reuses an idle tab / or — when all tabs are busy —
 * toasts and stays put. onResume returns whether it actually landed; the overlay
 * closes only when it did.
 *
 * @exports HistoryOverlay
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { HistoryView } from '../../pages/chat/components/RightSidebar/HistoryView';
import { MessageBubble } from '../../pages/chat/components/MessageBubble';
import type { ChatSession, Message } from '../../types';
import { groupSessionsByTime, toDisplayMessage } from '../../pages/chat/utils';
import { searchService } from '../../services/search';
import { chatService } from '../../services/chat';
import { agentsService } from '../../services/agents';

const DEBOUNCE_MS = 250;
/** Same limit ChatPage uses for a tab load — keeps the shared, sessionId-keyed
 *  ETag cache consistent between a preview fetch and a real tab load. */
const PREVIEW_MESSAGE_LIMIT = 200;

export interface HistoryContentProps {
  /** Agent scope for the session list (ChatPage's selectedAgentId, via bridge). */
  agentId: string | null;
  /** Delete a session (ChatPage's delete-confirm flow, via bridge). */
  onDeleteSession: (session: ChatSession) => void;
  /**
   * Resume the session in a chat tab. Returns `true` if it landed → overlay closes;
   * `false` (all tabs busy) → stays open. ChatPage's handleResumeSession, via bridge.
   */
  onResume: (session: ChatSession) => boolean;
  /** Close the surface (host's closeOverlay). */
  close: () => void;
}

/**
 * HistoryContent — the History browser surface (M3: migrated to OverlayHost registry).
 * Unlike the other surfaces it is DATA-REACTIVE + agent-scoped, so it self-fetches
 * `sessions`/`agents` from the shared TanStack Query cache (staying reactive to live
 * updates — a ref-bridge would go stale) and takes only the ChatPage-owned HANDLERS
 * (resume/delete) + the agent scope via the ctx bridge. Host owns the chrome + fresh
 * mount per open (so the former reset-on-close effect is gone).
 */
export function HistoryContent({ agentId, onDeleteSession, onResume, close }: HistoryContentProps) {
  // Self-fetch, same query keys ChatPage uses → shares the cache, stays reactive.
  const { data: sessions = [] } = useQuery({
    queryKey: ['chatSessions', agentId],
    queryFn: () => chatService.listSessions(agentId || undefined),
    enabled: !!agentId,
  });
  const { data: agents = [] } = useQuery({ queryKey: ['agents'], queryFn: agentsService.list });
  const groupedSessions = useMemo(() => groupSessionsByTime(sessions), [sessions]);

  const [searchText, setSearchText] = useState('');
  const [searchResults, setSearchResults] = useState<ChatSession[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const requestSeq = useRef(0);

  // Preview pane state (pure local — never MessageStore).
  const [previewSession, setPreviewSession] = useState<ChatSession | null>(null);
  const [previewMessages, setPreviewMessages] = useState<Message[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewSeq = useRef(0);

  // Debounced backend FTS. Empty query → clear results → grouped fallback.
  useEffect(() => {
    const q = searchText.trim();
    if (!q) {
      requestSeq.current++;
      setSearchResults(null);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    const seq = ++requestSeq.current;
    const timer = setTimeout(async () => {
      try {
        const results = await searchService.searchSessions(q);
        if (seq === requestSeq.current) {
          setSearchResults(results);
          setIsSearching(false);
        }
      } catch {
        if (seq === requestSeq.current) {
          setSearchResults([]);
          setIsSearching(false);
        }
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchText]);

  // Load the preview pane's messages when the selected session changes.
  useEffect(() => {
    if (!previewSession) {
      setPreviewMessages([]);
      setPreviewLoading(false);
      return;
    }
    const seq = ++previewSeq.current;
    setPreviewLoading(true);
    (async () => {
      try {
        const msgs = await chatService.getSessionMessagesPaginated(previewSession.id, PREVIEW_MESSAGE_LIMIT);
        if (seq === previewSeq.current) {
          setPreviewMessages(msgs.map(toDisplayMessage));
          setPreviewLoading(false);
        }
      } catch {
        if (seq === previewSeq.current) {
          setPreviewMessages([]);
          setPreviewLoading(false);
        }
      }
    })();
  }, [previewSession]);

  // (Former reset-on-close effect removed — the host mounts this fresh on each open
  //  and unmounts on close, so transient state starts empty every time.)

  // If the previewed session disappears (deleted while previewed), clear the
  // pane so it doesn't show stale content with a dead Resume button.
  useEffect(() => {
    if (!previewSession) return;
    const inGrouped = groupedSessions.some((g) => g.sessions.some((s) => s.id === previewSession.id));
    const inResults = searchResults?.some((s) => s.id === previewSession.id) ?? false;
    // When a search is active, existence is authoritative from results; else from the grouped list.
    const stillExists = searchResults != null ? inResults : inGrouped;
    if (!stillExists) {
      setPreviewSession(null);
      setPreviewMessages([]);
      previewSeq.current++;
    }
  }, [groupedSessions, searchResults, previewSession]);

  const handleResume = () => {
    if (!previewSession) return;
    const landed = onResume(previewSession);
    if (landed) close(); // all-busy → executor toasts, stay open
  };

  return (
      <div className="flex-1 min-h-0 flex" data-testid="history-overlay">
        {/* LEFT — session list */}
        <div className="w-80 shrink-0 border-r border-[var(--color-border)] flex flex-col min-h-0">
          <HistoryView
            groupedSessions={groupedSessions}
            agents={agents}
            onSelectSession={() => { /* preview-mode: row-click uses onPreview */ }}
            onDeleteSession={onDeleteSession}
            onBack={close}
            hideHeader
            searchText={searchText}
            onSearchTextChange={setSearchText}
            searchResults={searchResults}
            isSearching={isSearching}
            onPreview={setPreviewSession}
            selectedSessionId={previewSession?.id}
          />
        </div>

        {/* RIGHT — read-only preview. min-w floor so the pane can't collapse to
            0 when the modal clamps narrow (list is a fixed 320px). */}
        <div className="flex-1 flex flex-col min-h-0 min-w-[280px]" data-testid="history-preview">
          {!previewSession ? (
            <div className="flex-1 flex items-center justify-center text-[12.5px] text-[var(--color-text-muted)]">
              Select a conversation to preview
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 px-4 py-2 border-b border-[var(--color-border)]">
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium truncate text-[var(--color-text)]">{previewSession.title}</p>
                  <p className="text-[10.5px] text-[var(--color-text-muted)]">Read-only preview</p>
                </div>
                <button
                  onClick={handleResume}
                  disabled={previewLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md
                    bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 transition-colors shrink-0
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="material-symbols-outlined text-sm">open_in_new</span>
                  Resume in tab
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3">
                {previewLoading ? (
                  <p className="text-[10.5px] text-[var(--color-text-muted)] text-center py-6">Loading…</p>
                ) : previewMessages.length === 0 ? (
                  <p className="text-[10.5px] text-[var(--color-text-muted)] text-center py-6">No messages</p>
                ) : (
                  previewMessages.map((m) => <MessageBubble key={m.id} message={m} readOnly />)
                )}
              </div>
            </>
          )}
        </div>
      </div>
  );
}
