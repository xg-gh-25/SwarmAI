/**
 * Chat page constants and message factory utilities.
 *
 * - ``USER_MESSAGE_MAX_LINES``        — Line clamp threshold for user message truncation
 * - ``DEFAULT_SIDEBAR_WIDTH`` etc.    — Sidebar dimension constraints
 * - ``SLASH_COMMANDS``                — Available slash command definitions
 * - ``createWelcomeMessage``          — Factory for the default welcome assistant message
 * - ``createWorkspaceChangeMessage``  — Factory for workspace-change notification messages
 */
import type { Message } from '../../types';

// Time constants
export const MS_PER_DAY = 86400000;

// UI constants
export const USER_MESSAGE_MAX_LINES = 5;
export const DEFAULT_SIDEBAR_WIDTH = 256;
export const DEFAULT_RIGHT_SIDEBAR_WIDTH = 320;
export const MIN_SIDEBAR_WIDTH = 200;
export const MAX_SIDEBAR_WIDTH = 1000;
export const MIN_RIGHT_SIDEBAR_WIDTH = 240;
export const MAX_RIGHT_SIDEBAR_WIDTH = 600;

// ToDo Radar sidebar constants
export const DEFAULT_TODO_RADAR_WIDTH = 300;
export const MIN_TODO_RADAR_WIDTH = 200;
export const MAX_TODO_RADAR_WIDTH = 1000;



// Slash commands configuration
export const SLASH_COMMANDS = [
  { name: '/clear', description: 'Clear conversation context' },
  { name: '/compact', description: 'Compact conversation history' },
  { name: '/plugin list', description: 'List installed plugins' },
  { name: '/plugin install', description: 'Install a plugin: /plugin install {name}@{marketplace}' },
  { name: '/plugin uninstall', description: 'Uninstall a plugin: /plugin uninstall {id}' },
  { name: '/plugin marketplace list', description: 'List available marketplaces' },
] as const;

// Time group types for session grouping
export type TimeGroup = 'today' | 'yesterday' | 'thisWeek' | 'thisMonth' | 'older';

// i18n keys for time group labels
export const TIME_GROUP_LABEL_KEYS: Record<TimeGroup, string> = {
  today: 'chat.today',
  yesterday: 'chat.yesterday',
  thisWeek: 'chat.thisWeek',
  thisMonth: 'chat.thisMonth',
  older: 'chat.older',
};

// Welcome message generator - single source of truth
export const createWelcomeMessage = (customText?: string): Message => ({
  id: Date.now().toString(),
  role: 'assistant',
  content: [
    {
      type: 'text',
      text: customText ?? `# Welcome to SwarmAI! 🐝

**Your AI Team, 24/7 — Work smarter, move faster, and enjoy the journey.**
`,
    },
  ],
  timestamp: new Date().toISOString(),
});

// Workspace change message generator
export const createWorkspaceChangeMessage = (workspaceName?: string, workspacePath?: string): Message => {
  const contextMessage = workspaceName
    ? `📁 Workspace changed to: **${workspaceName}** (${workspacePath})`
    : '📁 Workspace cleared';

  return createWelcomeMessage(`${contextMessage}

---

# Welcome to SwarmAI! 🐝

**Your AI Team, 24/7 — Work smarter, move faster, and enjoy the journey.**`);
};
