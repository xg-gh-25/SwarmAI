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



// Slash command types
export type SlashCommandCategory = 'system' | 'skill';

export interface SlashCommand {
  name: string;
  /** i18n key of the description (built-in commands); the renderer calls t() on it */
  descriptionKey?: string;
  /** literal description (skill commands: the skill's own text) */
  description?: string;
  category: SlashCommandCategory;
}

// Built-in system commands (hardcoded)
export const SYSTEM_COMMANDS: SlashCommand[] = [
  { name: '/clear', descriptionKey: 'ui.constants.cmdClear', category: 'system' },
  { name: '/compact', descriptionKey: 'ui.constants.cmdCompact', category: 'system' },
  { name: '/plugin list', descriptionKey: 'ui.constants.cmdPluginList', category: 'system' },
  { name: '/plugin install', descriptionKey: 'ui.constants.cmdPluginInstall', category: 'system' },
  { name: '/plugin uninstall', descriptionKey: 'ui.constants.cmdPluginUninstall', category: 'system' },
  { name: '/plugin marketplace list', descriptionKey: 'ui.constants.cmdMarketplaceList', category: 'system' },
];

/** @deprecated Use SYSTEM_COMMANDS + skill-derived commands instead */
export const SLASH_COMMANDS = SYSTEM_COMMANDS;

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

**Human directs. AI delivers.**
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

**Human directs. AI delivers.**`);
};
