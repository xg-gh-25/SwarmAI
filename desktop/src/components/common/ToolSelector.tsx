/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

// Tool category definitions
export interface ToolCategory {
  id: string;
  name: string;
  descriptionKey: string;
  icon: string;
  tools: Tool[];
  enabledByDefault: boolean;
}

export interface Tool {
  id: string;
  name: string;
  descriptionKey: string;
  enabledByDefault?: boolean; // Override category default for specific tools
}

// Built-in tool categories based on security_hooks.py
export const TOOL_CATEGORIES: ToolCategory[] = [
  {
    id: 'bash',
    name: 'Bash Tools',
    descriptionKey: 'ui.ToolSelector.bashCategoryDesc',
    icon: 'terminal',
    enabledByDefault: true,
    tools: [
      { id: 'Bash', name: 'Bash', descriptionKey: 'ui.ToolSelector.bashToolDesc' },
      { id: 'KillShell', name: 'KillShell', descriptionKey: 'ui.ToolSelector.killShellToolDesc' },
    ],
  },
  {
    id: 'file',
    name: 'File Tools',
    descriptionKey: 'ui.ToolSelector.fileCategoryDesc',
    icon: 'folder',
    enabledByDefault: true,
    tools: [
      { id: 'Read', name: 'Read', descriptionKey: 'ui.ToolSelector.readToolDesc' },
      { id: 'Write', name: 'Write', descriptionKey: 'ui.ToolSelector.writeToolDesc' },
      { id: 'Edit', name: 'Edit', descriptionKey: 'ui.ToolSelector.editToolDesc' },
      { id: 'Glob', name: 'Glob', descriptionKey: 'ui.ToolSelector.globToolDesc' },
      { id: 'Grep', name: 'Grep', descriptionKey: 'ui.ToolSelector.grepToolDesc' },
    ],
  },
  {
    id: 'web',
    name: 'Web Tools',
    descriptionKey: 'ui.ToolSelector.webCategoryDesc',
    icon: 'language',
    enabledByDefault: true,
    tools: [
      { id: 'WebFetch', name: 'WebFetch', descriptionKey: 'ui.ToolSelector.webFetchToolDesc' },
      { id: 'WebSearch', name: 'WebSearch', descriptionKey: 'ui.ToolSelector.webSearchToolDesc' },
    ],
  },
  {
    id: 'advanced',
    name: 'Advanced Tools',
    descriptionKey: 'ui.ToolSelector.advancedCategoryDesc',
    icon: 'settings_suggest',
    enabledByDefault: false,
    tools: [
      { id: 'TodoWrite', name: 'TodoWrite', descriptionKey: 'ui.ToolSelector.todoWriteToolDesc', enabledByDefault: true },
      { id: 'Task', name: 'Task', descriptionKey: 'ui.ToolSelector.taskToolDesc' },
      { id: 'TaskOutput', name: 'TaskOutput', descriptionKey: 'ui.ToolSelector.taskOutputToolDesc' },
      { id: 'EnterPlanMode', name: 'EnterPlanMode', descriptionKey: 'ui.ToolSelector.enterPlanModeToolDesc' },
      { id: 'Skill', name: 'Skill', descriptionKey: 'ui.ToolSelector.skillToolDesc', enabledByDefault: true },
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
  const { t } = useTranslation();
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
      <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('ui.ToolSelector.builtInTools')}</label>
      <div className="space-y-2">
        {TOOL_CATEGORIES.map((category) => {
          const isExpanded = expandedCategories.includes(category.id);
          const isFullyEnabled = isCategoryFullyEnabled(category);
          const isPartiallyEnabled = isCategoryPartiallyEnabled(category);
          const enabledCount = category.tools.filter((t) => selectedTools.includes(t.id)).length;

          return (
            <div
              key={category.id}
              className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg overflow-hidden"
            >
              {/* Category Header */}
              <div
                className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-[var(--color-hover)] transition-colors"
                onClick={() => toggleCategory(category.id)}
              >
                <div className="flex items-center gap-3">
                  <span className="material-symbols-outlined icon-md text-primary">{category.icon}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-[var(--color-text)] font-medium">{category.name}</span>
                      <span className="text-xs text-[var(--color-text-muted)]">
                        ({enabledCount}/{category.tools.length})
                      </span>
                    </div>
                    <p className="text-xs text-[var(--color-text-muted)]">{t(category.descriptionKey)}</p>
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
                        : 'bg-[var(--color-border)]'
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
                      'material-symbols-outlined icon-sm text-[var(--color-text-muted)] transition-transform',
                      isExpanded && 'rotate-180'
                    )}
                  >
                    expand_more
                  </span>
                </div>
              </div>

              {/* Expanded Tools List */}
              {isExpanded && (
                <div className="border-t border-[var(--color-border)] bg-[var(--color-card)]/50">
                  {category.tools.map((tool) => {
                    const isEnabled = selectedTools.includes(tool.id);
                    return (
                      <div
                        key={tool.id}
                        className="flex items-center justify-between px-4 py-2 pl-12 hover:bg-[var(--color-hover)]/50 transition-colors"
                      >
                        <div>
                          <span className="text-sm text-[var(--color-text)]">{tool.name}</span>
                          <p className="text-xs text-[var(--color-text-muted)]">{t(tool.descriptionKey)}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => toggleTool(tool.id)}
                          className={clsx(
                            'w-8 h-4 rounded-full transition-colors relative',
                            isEnabled ? 'bg-primary' : 'bg-[var(--color-border)]'
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
