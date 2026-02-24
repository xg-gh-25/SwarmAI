# Chat History Time-Based Grouping Design

## Overview

Optimize chat history sidebar by grouping sessions into time-based categories (Today, Yesterday, This Week, This Month, Older) for better navigation.

## Requirements

- Group by `lastAccessedAt` timestamp
- Dynamic time periods: Today, Yesterday, This Week, This Month, Older
- Non-collapsible groups (visual separation only)
- Empty groups are hidden
- Week starts on Monday (Chinese convention)

## Implementation

### 1. Time Grouping Logic

Add a utility function in `ChatPage.tsx`:

```typescript
type TimeGroup = 'today' | 'yesterday' | 'thisWeek' | 'thisMonth' | 'older';

interface GroupedSessions {
  group: TimeGroup;
  sessions: ChatSession[];
}

const groupSessionsByTime = (sessions: ChatSession[]): GroupedSessions[] => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);

  // Week starts on Monday
  const dayOfWeek = now.getDay();
  const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const weekStart = new Date(today.getTime() - mondayOffset * 86400000);

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);

  const groups: Record<TimeGroup, ChatSession[]> = {
    today: [],
    yesterday: [],
    thisWeek: [],
    thisMonth: [],
    older: [],
  };

  for (const session of sessions) {
    const date = new Date(session.lastAccessedAt);
    const sessionDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    if (sessionDay.getTime() === today.getTime()) {
      groups.today.push(session);
    } else if (sessionDay.getTime() === yesterday.getTime()) {
      groups.yesterday.push(session);
    } else if (sessionDay >= weekStart) {
      groups.thisWeek.push(session);
    } else if (sessionDay >= monthStart) {
      groups.thisMonth.push(session);
    } else {
      groups.older.push(session);
    }
  }

  // Return only non-empty groups in order
  const order: TimeGroup[] = ['today', 'yesterday', 'thisWeek', 'thisMonth', 'older'];
  return order
    .filter(group => groups[group].length > 0)
    .map(group => ({ group, sessions: groups[group] }));
};
```

### 2. i18n Keys

Add to `desktop/src/i18n/locales/en.json`:
```json
{
  "chat": {
    "today": "Today",
    "yesterday": "Yesterday",
    "thisWeek": "This Week",
    "thisMonth": "This Month",
    "older": "Older"
  }
}
```

Add to `desktop/src/i18n/locales/zh.json`:
```json
{
  "chat": {
    "today": "今天",
    "yesterday": "昨天",
    "thisWeek": "本周",
    "thisMonth": "本月",
    "older": "更早"
  }
}
```

### 3. UI Rendering

Replace the flat session list with grouped rendering:

```tsx
const groupLabelKey: Record<TimeGroup, string> = {
  today: 'chat.today',
  yesterday: 'chat.yesterday',
  thisWeek: 'chat.thisWeek',
  thisMonth: 'chat.thisMonth',
  older: 'chat.older',
};

// In render:
const groupedSessions = groupSessionsByTime(sessions);

{groupedSessions.map((group, groupIndex) => (
  <div key={group.group}>
    <p className={clsx(
      'px-3 py-2 text-xs font-medium text-muted uppercase tracking-wider',
      groupIndex > 0 && 'mt-3'
    )}>
      {t(groupLabelKey[group.group])}
    </p>
    {group.sessions.map((session) => (
      // ... existing session item rendering
    ))}
  </div>
))}
```

## Files to Modify

| File | Changes |
|------|---------|
| `desktop/src/pages/ChatPage.tsx` | Add `groupSessionsByTime()`, update rendering logic |
| `desktop/src/i18n/locales/en.json` | Add 5 new keys under `chat` |
| `desktop/src/i18n/locales/zh.json` | Add 5 new keys under `chat` |

## Visual Design

```
CHAT HISTORY
─────────────────────────
TODAY
  [icon] Session title
         Agent • 5m ago
  [icon] Another session
         Agent • 2h ago

YESTERDAY
  [icon] Session title
         Agent • 1d ago

THIS WEEK
  [icon] Session title
         Agent • 3d ago

OLDER
  [icon] Session title
         Agent • Jan 15
```

- Group labels use existing muted text style
- First group has no top margin, subsequent groups have `mt-3`
- Session items unchanged from current design
