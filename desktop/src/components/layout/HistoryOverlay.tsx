/**
 * HistoryOverlay — the left-nav "History" surface.
 *
 * Replaces the empty `history` placeholder in DomainStubOverlays. Opens on the
 * `swarm:show-history` window event (via useExclusiveOverlay, so it participates
 * in the single-overlay mux + back-to-chat contract like every other A10 domain
 * overlay) and hosts the (previously dead) HistoryView inside the shared
 * fullscreen Modal.
 *
 * "Clean, useful History" = real message-CONTENT search: the search box is
 * debounced and drives `searchService.searchSessions` (backend FTS over message
 * bodies), so a conversation is findable by what was SAID in it, not just its
 * title. An empty query falls back to the time-grouped full session list
 * (`groupedSessions`) — the same list the app shows everywhere else.
 *
 * Wiring B (no new event-data channel): rendered inside ChatPage, it receives
 * `groupedSessions` / `agents` / `onSelectSession` / `onDeleteSession` as direct
 * props (all already in ChatPage scope). The window-event only toggles open/close.
 *
 * @exports HistoryOverlay
 */
import { useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { HistoryView } from '../../pages/chat/components/RightSidebar/HistoryView';
import type { Agent, ChatSession } from '../../types';
import type { GroupedSessions } from '../../pages/chat/utils';
import { searchService } from '../../services/search';

const DEBOUNCE_MS = 250;

export interface HistoryOverlayProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
}

export function HistoryOverlay({
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
}: HistoryOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-history');

  const [searchText, setSearchText] = useState('');
  const [searchResults, setSearchResults] = useState<ChatSession[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  // Guards against out-of-order responses clobbering a newer query's results.
  const requestSeq = useRef(0);

  // Debounced backend FTS. Empty query → clear results → grouped fallback.
  useEffect(() => {
    const q = searchText.trim();
    if (!q) {
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
          setSearchResults([]); // surface as "no matches" rather than a crash
          setIsSearching(false);
        }
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchText]);

  // Reset transient search state each time the overlay closes, so reopening
  // starts clean on the grouped fallback.
  useEffect(() => {
    if (!open) {
      setSearchText('');
      setSearchResults(null);
      setIsSearching(false);
      requestSeq.current++;
    }
  }, [open]);

  const handleSelect = (session: ChatSession) => {
    onSelectSession(session);
    close();
  };

  return (
    <Modal isOpen={open} onClose={close} title="History" size="fullscreen" mode="HISTORY" fullscreenWidth="m">
      <div className="flex-1 min-h-0 flex flex-col" data-testid="history-overlay">
        <HistoryView
          groupedSessions={groupedSessions}
          agents={agents}
          onSelectSession={handleSelect}
          onDeleteSession={onDeleteSession}
          onBack={close}
          hideHeader
          searchText={searchText}
          onSearchTextChange={setSearchText}
          searchResults={searchResults}
          isSearching={isSearching}
        />
      </div>
    </Modal>
  );
}
