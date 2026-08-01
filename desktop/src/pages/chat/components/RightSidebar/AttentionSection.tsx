/**
 * AttentionSection — the 🔔 "Needs You" section in the Radar sidebar.
 *
 * Thin wrapper: renders a CollapsibleSection header (with the total count) around
 * the shared <AttentionList> card list. All card rendering + the "acting" state
 * machine now live in AttentionList (extracted run_843962a5, Gate-1 fix B) so the
 * ChatHeader Alerts popover can render the IDENTICAL cards from one source.
 *
 * Empty queue → renders null (section disappears). Running pipelines live in the
 * bottom PipelinesBar, not here.
 */
import { CollapsibleSection } from './shared/CollapsibleSection';
import { AttentionList } from './AttentionList';
import type { AttentionItem, ItemClickHandler } from './types';

interface AttentionSectionProps {
  items: AttentionItem[];
  /** Inject a message into the current chat input (paused / job items). */
  onItemClick?: ItemClickHandler;
  /** Switch to another tab (waiting items). */
  onSelectTab?: (tabId: string) => void;
}

export function AttentionSection({ items, onItemClick, onSelectTab }: AttentionSectionProps) {
  if (items.length === 0) return null;

  return (
    <CollapsibleSection
      name="attention"
      icon="notifications"
      label="Needs You"
      count={items.length}
      defaultExpanded={true}
      accent="rgba(245,166,35,0.5)"
    >
      <AttentionList items={items} onItemClick={onItemClick} onSelectTab={onSelectTab} />
    </CollapsibleSection>
  );
}
