import { useState } from 'react';
import clsx from 'clsx';

// Tool category definitions
export interface ToolCategory {
  id: string;
  name: string;
  description: string;
  icon: string;
  tools: Tool[];
  enabledByDefault: boolean;
}

export interface Tool {
  id: string;
  name: string;
  description: string;
  enabledByDefault?: boolean; // Override category default for specific tools
}

// Built-in tool categories based on agent_manager.py
export const TOOL_CATEGORIES: ToolCategory[] = [
  {
    id: 'bash',
    name: 'Bash Tools',
    description: 'Execute terminal commands',
    icon: 'terminal',
    enabledByDefault: true,
    tools: [
      { id: 'Bash', name: 'Bash', description: 'Execute shell commands in terminal' },
      { id: 'KillShell', name: 'KillShell', description: 'Kill running background shell processes' },
    ],
  },
  {
    id: 'file',
    name: 'File Tools',
    description: 'Read, write, and search files',
    icon: 'folder',
    enabledByDefault: true,
    tools: [
      { id: 'Read', name: 'Read', description: 'Read file contents' },
      { id: 'Write', name: 'Write', description: 'Write content to files' },
      { id: 'Edit', name: 'Edit', description: 'Edit existing files' },
      { id: 'Glob', name: 'Glob', description: 'Find files by pattern' },
      { id: 'Grep', name: 'Grep', description: 'Search file contents' },
    ],
  },
  {
    id: 'web',
    name: 'Web Tools',
    description: 'Fetch web content and search',
    icon: 'language',
    enabledByDefault: true,
    tools: [
      { id: 'WebFetch', name: 'WebFetch', description: 'Fetch content from URLs' },
      { id: 'WebSearch', name: 'WebSearch', description: 'Search the web' },
    ],
  },
  {
    id: 'advanced',
    name: 'Advanced Tools',
    description: 'Task management and agent control',
    icon: 'settings_suggest',
    enabledByDefault: false,
    tools: [
      { id: 'TodoWrite', name: 'TodoWrite', description: 'Manage task lists and track progress', enabledByDefault: true },
      { id: 'Task', name: 'Task', description: 'Launch sub-agents for complex tasks' },
      { id: 'TaskOutput', name: 'TaskOutput', description: 'Retrieve output from background tasks' },
      { id: 'EnterPlanMode', name: 'EnterPlanMode', description: 'Enter planning mode for implementation' },
      { id: 'Skill', name: 'Skill', description: 'Execute skills and slash commands', enabledByDefault: true },
    ],
  },
];

// Get all tool IDs from a category
export const getCategoryToolIds = (categoryId: string): string[] => {
  const category = TOOL_CATEGORIES.find((c) => c.id === categoryId);
  return category ? category.tools.map((t) => t.id) : [];
};

// Get all default enabled tool IDs
// Considers both category-level and tool-level enabledByDefault flags
export const getDefaultEnabledTools = (): string[] => {
  const enabledTools: string[] = [];

  for (const category of TOOL_CATEGORIES) {
    for (const tool of category.tools) {
      // Tool is enabled if:
      // 1. Category is enabled by default AND tool doesn't explicitly disable itself, OR
      // 2. Tool explicitly enables itself (overrides category default)
      const toolDefault = tool.enabledByDefault;
      const shouldEnable = toolDefault !== undefined
        ? toolDefault  // Tool has explicit setting
        : category.enabledByDefault;  // Use category default

      if (shouldEnable) {
        enabledTools.push(tool.id);
      }
    }
  }

  return enabledTools;
};

interface ToolSelectorProps {
  selectedTools: string[];
  onChange: (tools: string[]) => void;
  className?: string;
}

export function ToolSelector({ selectedTools, onChange, className }: ToolSelectorProps) {
  const [expandedCategories, setExpandedCategories] = useState<string[]>([]);

  const toggleCategory = (categoryId: string) => {
    setExpandedCategories((prev) =>
      prev.includes(categoryId)
        ? prev.filter((id) => id !== categoryId)
        : [...prev, categoryId]
    );
  };

  const isCategoryFullyEnabled = (category: ToolCategory) => {
    return category.tools.every((tool) => selectedTools.includes(tool.id));
  };

  const isCategoryPartiallyEnabled = (category: ToolCategory) => {
    const enabledCount = category.tools.filter((tool) => selectedTools.includes(tool.id)).length;
    return enabledCount > 0 && enabledCount < category.tools.length;
  };

  const toggleCategoryAll = (category: ToolCategory) => {
    const categoryToolIds = category.tools.map((t) => t.id);
    const isFullyEnabled = isCategoryFullyEnabled(category);

    if (isFullyEnabled) {
      // Disable all tools in this category
      onChange(selectedTools.filter((id) => !categoryToolIds.includes(id)));
    } else {
      // Enable all tools in this category
      const newTools = [...selectedTools];
      categoryToolIds.forEach((toolId) => {
        if (!newTools.includes(toolId)) {
          newTools.push(toolId);
        }
      });
      onChange(newTools);
    }
  };

  const toggleTool = (toolId: string) => {
    if (selectedTools.includes(toolId)) {
      onChange(selectedTools.filter((id) => id !== toolId));
    } else {
      onChange([...selectedTools, toolId]);
    }
  };

  return (
    <div className={clsx('space-y-2', className)}>
      <label className="block text-sm font-medium text-muted mb-2">Built-in Tools</label>
      <div className="space-y-2">
        {TOOL_CATEGORIES.map((category) => {
          const isExpanded = expandedCategories.includes(category.id);
          const isFullyEnabled = isCategoryFullyEnabled(category);
          const isPartiallyEnabled = isCategoryPartiallyEnabled(category);
          const enabledCount = category.tools.filter((t) => selectedTools.includes(t.id)).length;

          return (
            <div
              key={category.id}
              className="bg-dark-bg border border-dark-border rounded-lg overflow-hidden"
            >
              {/* Category Header */}
              <div
                className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-dark-hover transition-colors"
                onClick={() => toggleCategory(category.id)}
              >
                <div className="flex items-center gap-3">
                  <span className="material-symbols-outlined text-primary">{category.icon}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-white font-medium">{category.name}</span>
                      <span className="text-xs text-muted">
                        ({enabledCount}/{category.tools.length})
                      </span>
                    </div>
                    <p className="text-xs text-muted">{category.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {/* Category Toggle */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCategoryAll(category);
                    }}
                    className={clsx(
                      'w-10 h-5 rounded-full transition-colors relative',
                      isFullyEnabled
                        ? 'bg-primary'
                        : isPartiallyEnabled
                        ? 'bg-primary/50'
                        : 'bg-dark-border'
                    )}
                  >
                    <span
                      className={clsx(
                        'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                        isFullyEnabled || isPartiallyEnabled ? 'left-5' : 'left-0.5'
                      )}
                    />
                  </button>
                  {/* Expand/Collapse Icon */}
                  <span
                    className={clsx(
                      'material-symbols-outlined text-muted transition-transform',
                      isExpanded && 'rotate-180'
                    )}
                  >
                    expand_more
                  </span>
                </div>
              </div>

              {/* Expanded Tools List */}
              {isExpanded && (
                <div className="border-t border-dark-border bg-dark-card/50">
                  {category.tools.map((tool) => {
                    const isEnabled = selectedTools.includes(tool.id);
                    return (
                      <div
                        key={tool.id}
                        className="flex items-center justify-between px-4 py-2 pl-12 hover:bg-dark-hover/50 transition-colors"
                      >
                        <div>
                          <span className="text-sm text-white">{tool.name}</span>
                          <p className="text-xs text-muted">{tool.description}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => toggleTool(tool.id)}
                          className={clsx(
                            'w-8 h-4 rounded-full transition-colors relative',
                            isEnabled ? 'bg-primary' : 'bg-dark-border'
                          )}
                        >
                          <span
                            className={clsx(
                              'absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform',
                              isEnabled ? 'left-4' : 'left-0.5'
                            )}
                          />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ToolSelector;
