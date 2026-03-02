import type { Message } from '../../types';

// Time constants
export const MS_PER_DAY = 86400000;

// UI constants
export const TOOL_INPUT_COLLAPSE_LENGTH = 200;
export const DEFAULT_SIDEBAR_WIDTH = 256;
export const DEFAULT_RIGHT_SIDEBAR_WIDTH = 320;
export const MIN_SIDEBAR_WIDTH = 200;
export const MAX_SIDEBAR_WIDTH = 600;
export const MIN_RIGHT_SIDEBAR_WIDTH = 240;
export const MAX_RIGHT_SIDEBAR_WIDTH = 600;

// ToDo Radar sidebar constants
export const DEFAULT_TODO_RADAR_WIDTH = 300;
export const MIN_TODO_RADAR_WIDTH = 200;
export const MAX_TODO_RADAR_WIDTH = 500;

// Right sidebar group types and constants
export const RIGHT_SIDEBAR_IDS = ['todoRadar', 'chatHistory', 'fileBrowser'] as const;
export type RightSidebarId = typeof RIGHT_SIDEBAR_IDS[number];

export const DEFAULT_ACTIVE_SIDEBAR: RightSidebarId = 'todoRadar';

export interface SidebarWidthConfig {
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  storageKey: string;
}

export const RIGHT_SIDEBAR_WIDTH_CONFIGS: Record<RightSidebarId, SidebarWidthConfig> = {
  todoRadar: {
    defaultWidth: DEFAULT_TODO_RADAR_WIDTH,
    minWidth: MIN_TODO_RADAR_WIDTH,
    maxWidth: MAX_TODO_RADAR_WIDTH,
    storageKey: 'todoRadarSidebarWidth',
  },
  chatHistory: {
    defaultWidth: DEFAULT_SIDEBAR_WIDTH,
    minWidth: MIN_SIDEBAR_WIDTH,
    maxWidth: MAX_SIDEBAR_WIDTH,
    storageKey: 'chatSidebarWidth',
  },
  fileBrowser: {
    defaultWidth: DEFAULT_RIGHT_SIDEBAR_WIDTH,
    minWidth: MIN_RIGHT_SIDEBAR_WIDTH,
    maxWidth: MAX_RIGHT_SIDEBAR_WIDTH,
    storageKey: 'rightSidebarWidth',
  },
};

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
      text: customText ?? `# Welcome to SwarmAI! 🤖

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

# Welcome to SwarmAI! 🤖

**Your AI Team, 24/7 — Work smarter, move faster, and enjoy the journey.**`);
};
