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
import { useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { HistoryView } from '../../pages/chat/components/RightSidebar/HistoryView';
import { MessageBubble } from '../../pages/chat/components/MessageBubble';
import type { Agent, ChatSession, Message } from '../../types';
import type { GroupedSessions } from '../../pages/chat/utils';
import { toDisplayMessage } from '../../pages/chat/utils';
import { searchService } from '../../services/search';
import { chatService } from '../../services/chat';

const DEBOUNCE_MS = 250;
/** Same limit ChatPage uses for a tab load — keeps the shared, sessionId-keyed
 *  ETag cache consistent between a preview fetch and a real tab load. */
const PREVIEW_MESSAGE_LIMIT = 200;

export interface HistoryOverlayProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onDeleteSession: (session: ChatSession) => void;
  /**
   * Resume the session in a chat tab. Returns `true` if it landed (focused /
   * opened / reused a tab) → overlay closes; `false` if it could not (all tabs
   * busy — the executor shows a toast) → overlay stays open.
   */
  onResume: (session: ChatSession) => boolean;
}

export function HistoryOverlay({
  groupedSessions,
  agents,
  onDeleteSession,
  onResume,
}: HistoryOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-history');

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

  // Reset transient state each time the overlay closes.
  useEffect(() => {
    if (!open) {
      setSearchText('');
      setSearchResults(null);
      setIsSearching(false);
      requestSeq.current++;
      setPreviewSession(null);
      setPreviewMessages([]);
      previewSeq.current++;
    }
  }, [open]);

  const handleResume = () => {
    if (!previewSession) return;
    const landed = onResume(previewSession);
    if (landed) close(); // all-busy → executor toasts, stay open
  };

  return (
    <Modal isOpen={open} onClose={close} title="History" size="fullscreen" mode="HISTORY" fullscreenWidth="l">
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

        {/* RIGHT — read-only preview */}
        <div className="flex-1 min-w-0 flex flex-col min-h-0" data-testid="history-preview">
          {!previewSession ? (
            <div className="flex-1 flex items-center justify-center text-sm text-[var(--color-text-muted)]">
              Select a conversation to preview
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 px-4 py-2.5 border-b border-[var(--color-border)]">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate text-[var(--color-text)]">{previewSession.title}</p>
                  <p className="text-[11px] text-[var(--color-text-muted)]">Read-only preview</p>
                </div>
                <button
                  onClick={handleResume}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md
                    bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20 transition-colors shrink-0"
                >
                  <span className="material-symbols-outlined text-sm">open_in_new</span>
                  Resume in tab
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3">
                {previewLoading ? (
                  <p className="text-xs text-[var(--color-text-muted)] text-center py-6">Loading…</p>
                ) : previewMessages.length === 0 ? (
                  <p className="text-xs text-[var(--color-text-muted)] text-center py-6">No messages</p>
                ) : (
                  previewMessages.map((m) => <MessageBubble key={m.id} message={m} readOnly />)
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
