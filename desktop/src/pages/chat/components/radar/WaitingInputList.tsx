/**
 * Waiting Input list and item components for the Needs Attention zone.
 *
 * Renders pending questions and permission requests as actionable items
 * with a visually prominent "Respond" button. Items are ephemeral —
 * derived from SSE props, not persisted in the database.
 *
 * - ``WaitingInputItem``  — Single waiting input item (li)
 * - ``WaitingInputList``  — List wrapper (ul) rendering WaitingInputItems
 *
 * Requirements: 1.1, 1.2, 1.3, 1.4, 1.7, 6.5, 7.1–7.7
 */

import type { RadarWaitingItem } from '../../../../types';

// ---------------------------------------------------------------------------
// WaitingInputItem
// ---------------------------------------------------------------------------

interface WaitingInputItemProps {
  item: RadarWaitingItem;
  onRespond: () => void;
}

function WaitingInputItem({ item, onRespond }: WaitingInputItemProps) {
  return (
    <li role="listitem" className="radar-waiting-item">
      <div className="radar-waiting-item-content">
        <span className="radar-waiting-item-title">{item.title}</span>
        <span className="radar-waiting-item-question">{item.question}</span>
      </div>
      <button
        className="radar-waiting-item-respond"
        onClick={onRespond}
        type="button"
      >
        Respond
      </button>
    </li>
  );
}


// ---------------------------------------------------------------------------
// WaitingInputList
// ---------------------------------------------------------------------------

interface WaitingInputListProps {
  waitingItems: RadarWaitingItem[];
  onRespond: (itemId: string) => void;
}

export function WaitingInputList({ waitingItems, onRespond }: WaitingInputListProps) {
  if (waitingItems.length === 0) return null;

  return (
    <ul role="list" className="radar-waiting-list">
      {waitingItems.map((item) => (
        <WaitingInputItem
          key={item.id}
          item={item}
          onRespond={() => onRespond(item.id)}
        />
      ))}
    </ul>
  );
}
