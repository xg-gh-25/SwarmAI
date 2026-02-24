import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { Agent, AgentCreateRequest } from '../../types';
import { skillsService } from '../../services/skills';
import { mcpService } from '../../services/mcp';
import { pluginsService } from '../../services/plugins';
import { settingsService } from '../../services/settings';
import Modal from './Modal';
import Dropdown from './Dropdown';
import MultiSelect from './MultiSelect';
import ToolSelector, { getDefaultEnabledTools } from './ToolSelector';
import Button from './Button';
import { Spinner } from './SkeletonLoader';

// Claude models (available in both Bedrock and API Proxy modes)
const CLAUDE_MODELS = [
  { id: 'claude-sonnet-4-5-20250929', name: 'Claude Sonnet 4.5', description: 'Best balance of speed and intelligence' },
  { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5', description: 'Fastest and most cost-effective' },
  { id: 'claude-opus-4-5-20251101', name: 'Claude Opus 4.5', description: 'Most intelligent, best for complex tasks' },
];

// Third-party models (only available via API Proxy, not Bedrock mode)
const THIRD_PARTY_MODELS = [
  { id: 'minimax.minimax-m2', name: 'MiniMax-m2', description: 'MiniMax M2 model (API Proxy only)' },
  { id: 'qwen.qwen3-next-80b-a3b', name: 'Qwen3-Next-80B-A3B', description: 'Qwen3 Next 80B (API Proxy only)' },
  { id: 'qwen.qwen3-coder-480b-a35b-v1:0', name: 'Qwen3-Coder-480B-A35B', description: 'Qwen3 Coder 480B (API Proxy only)' },
  { id: 'qwen.qwen3-235b-a22b-2507-v1:0', name: 'Qwen3-235B-A22B-2507', description: 'Qwen3 235B (API Proxy only)' },
];

// Helper to get model options based on API mode
// When useBedrock is true: only show Claude models
// When useBedrock is false (API Proxy): show Claude + third-party models
const getModelOptions = (useBedrock: boolean) => {
  if (useBedrock) {
    return CLAUDE_MODELS;
  }
  return [...CLAUDE_MODELS, ...THIRD_PARTY_MODELS];
};

export interface AgentFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (agent: Agent | AgentCreateRequest) => Promise<void>;
  agent?: Agent | null; // null or undefined for create mode, Agent object for edit mode
  title?: string;
}

export default function AgentFormModal({
  isOpen,
  onClose,
  onSave,
  agent,
  title,
}: AgentFormModalProps) {
  const { t } = useTranslation();
  const isEditMode = !!agent;
  const modalTitle = title || (isEditMode ? t('agents.editAgent') : t('agents.createAgent'));

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [model, setModel] = useState<string>('');
  const [pluginIds, setPluginIds] = useState<string[]>([]);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [allowAllSkills, setAllowAllSkills] = useState(false);
  const [mcpIds, setMcpIds] = useState<string[]>([]);
  const [allowedTools, setAllowedTools] = useState<string[]>(getDefaultEnabledTools());
  const [globalUserMode, setGlobalUserMode] = useState(true); // Default to global mode
  const [enableHumanApproval, setEnableHumanApproval] = useState(true);

  const [isSaving, setIsSaving] = useState(false);

  // Fetch API config to check if Bedrock is enabled
  const { data: apiConfig } = useQuery({
    queryKey: ['apiConfig'],
    queryFn: settingsService.getAPIConfiguration,
    enabled: isOpen,
  });
  const useBedrock = apiConfig?.use_bedrock ?? false;

  // Fetch skills
  const { data: skills = [], isLoading: loadingSkills } = useQuery({
    queryKey: ['skills'],
    queryFn: skillsService.list,
    enabled: isOpen,
  });

  // Fetch MCP servers
  const { data: mcpServers = [], isLoading: loadingMCPs } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: mcpService.list,
    enabled: isOpen,
  });

  // Fetch plugins
  const { data: plugins = [], isLoading: loadingPlugins } = useQuery({
    queryKey: ['plugins'],
    queryFn: pluginsService.listPlugins,
    enabled: isOpen,
  });

  // Filter to only show installed plugins
  const installedPlugins = plugins.filter((p) => p.status === 'installed');

  // Initialize form when modal opens or agent changes
  useEffect(() => {
    if (isOpen) {
      if (agent) {
        // Edit mode - populate from agent
        setName(agent.name);
        setDescription(agent.description || '');
        setSystemPrompt(agent.systemPrompt || '');
        setModel(agent.model || '');
        setPluginIds(agent.pluginIds || []);
        setSkillIds(agent.skillIds || []);
        setAllowAllSkills(agent.allowAllSkills || false);
        setMcpIds(agent.mcpIds || []);
        setAllowedTools(agent.allowedTools || getDefaultEnabledTools());
        setGlobalUserMode(agent.globalUserMode ?? true); // Default to global mode
        setEnableHumanApproval(agent.enableHumanApproval ?? true);
      } else {
        // Create mode - reset to defaults
        setName('');
        setDescription('');
        setSystemPrompt('');
        setModel(''); // Will be set by the second useEffect when models load
        setPluginIds([]);
        setSkillIds([]);
        setAllowAllSkills(false);
        setMcpIds([]);
        setAllowedTools(getDefaultEnabledTools());
        setGlobalUserMode(true); // Default to global mode
        setEnableHumanApproval(true);
      }
    }
  }, [isOpen, agent]);

  // Set default model for create mode (use first available model)
  useEffect(() => {
    if (!isEditMode && !model) {
      const availableModels = getModelOptions(useBedrock);
      if (availableModels.length > 0) {
        setModel(availableModels[0].id);
      }
    }
  }, [useBedrock, model, isEditMode]);

  // Global User Mode requires Allow All Skills - skill restrictions not supported
  useEffect(() => {
    if (globalUserMode) {
      setAllowAllSkills(true);
      setSkillIds([]); // Clear selected skills since all are allowed
    }
  }, [globalUserMode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    setIsSaving(true);
    try {
      if (isEditMode && agent) {
        // Edit mode - update existing agent
        const updatedAgent: Agent = {
          ...agent,
          name,
          description: description || undefined,
          systemPrompt: systemPrompt || undefined,
          model,
          pluginIds,
          skillIds: allowAllSkills ? [] : skillIds,
          allowAllSkills,
          mcpIds,
          allowedTools,
          globalUserMode,
          enableHumanApproval,
        };
        await onSave(updatedAgent);
      } else {
        // Create mode - create new agent
        const newAgent: AgentCreateRequest = {
          name,
          description: description || undefined,
          model,
          permissionMode: 'bypassPermissions',
          systemPrompt: systemPrompt || undefined,
          pluginIds,
          skillIds: allowAllSkills ? [] : skillIds,
          allowAllSkills,
          mcpIds,
          allowedTools,
          globalUserMode,
          enableHumanApproval,
        };
        await onSave(newAgent);
      }
      onClose();
    } catch (error) {
      console.error('Failed to save agent:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleClose = () => {
    if (!isSaving) {
      onClose();
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title={modalTitle} size="lg">
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Agent Name */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('agents.form.name')}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('agents.form.namePlaceholder')}
            required
            className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
          />
        </div>

        {/* Description */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('agents.form.description')}</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('agents.form.descriptionPlaceholder')}
            className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
          />
        </div>

        {/* System Prompt */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('agents.form.systemPrompt')}</label>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder={t('agents.form.systemPromptPlaceholder')}
            rows={4}
            className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary resize-none"
          />
        </div>

        {/* Global User Mode Toggle */}
        <div className="flex items-center justify-between">
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)]">{t('agents.form.globalUserMode')}</label>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{t('agents.form.globalUserModeDescription')}</p>
          </div>
          <button
            type="button"
            onClick={() => setGlobalUserMode(!globalUserMode)}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              globalUserMode ? 'bg-primary' : 'bg-[var(--color-border)]'
            )}
          >
            <span
              className={clsx(
                'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
                globalUserMode ? 'translate-x-5' : 'translate-x-0'
              )}
            />
          </button>
        </div>

        {/* Enable Human Approval Toggle */}
        <div className="flex items-center justify-between">
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)]">{t('agents.form.enableHumanApproval')}</label>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{t('agents.form.enableHumanApprovalDescription')}</p>
          </div>
          <button
            type="button"
            onClick={() => setEnableHumanApproval(!enableHumanApproval)}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              enableHumanApproval ? 'bg-primary' : 'bg-[var(--color-border)]'
            )}
          >
            <span
              className={clsx(
                'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
                enableHumanApproval ? 'translate-x-5' : 'translate-x-0'
              )}
            />
          </button>
        </div>

        {/* Base Model */}
        <div>
          <Dropdown
            label={t('agents.form.model')}
            options={getModelOptions(useBedrock)}
            selectedId={model || null}
            onChange={setModel}
            placeholder={t('agents.form.modelPlaceholder')}
          />
          {useBedrock && (
            <p className="mt-1 text-xs text-amber-400">
              <span className="material-symbols-outlined text-xs align-middle mr-1">info</span>
              {t('agents.form.thirdPartyModelsNote')}
            </p>
          )}
        </div>

        {/* Built-in Tools */}
        <ToolSelector selectedTools={allowedTools} onChange={setAllowedTools} />

        {/* Plugins Selection */}
        <MultiSelect
          label={isEditMode ? t('agents.form.enabledPlugins') : t('agents.form.pluginsOptional')}
          placeholder={t('agents.form.selectPlugins')}
          options={installedPlugins.map((plugin) => ({
            id: plugin.id,
            name: plugin.name,
            description: plugin.description,
          }))}
          selectedIds={pluginIds}
          onChange={setPluginIds}
          loading={loadingPlugins}
        />

        {/* Allow All Skills Toggle */}
        <div className="flex items-center justify-between">
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)]">{t('agents.form.allowAllSkills')}</label>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {globalUserMode
                ? t('agents.form.allowAllSkillsRequiredDescription')
                : t('agents.form.allowAllSkillsDescription')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => !globalUserMode && setAllowAllSkills(!allowAllSkills)}
            disabled={globalUserMode}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              globalUserMode ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
              allowAllSkills ? 'bg-primary' : 'bg-[var(--color-border)]'
            )}
          >
            <span
              className={clsx(
                'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out',
                allowAllSkills ? 'translate-x-5' : 'translate-x-0'
              )}
            />
          </button>
        </div>

        {/* Skills Selection */}
        <MultiSelect
          label={isEditMode ? t('agents.form.enabledSkills') : t('agents.form.skillsOptional')}
          placeholder={
            globalUserMode
              ? t('agents.form.allSkillsEnabledGlobal')
              : allowAllSkills
                ? t('agents.form.allSkillsEnabled')
                : t('agents.form.selectSkills')
          }
          options={skills.map((skill) => ({
            id: skill.id,
            name: skill.name,
            description: skill.description,
          }))}
          selectedIds={allowAllSkills ? [] : skillIds}
          onChange={setSkillIds}
          loading={loadingSkills}
          disabled={allowAllSkills || globalUserMode}
        />

        {/* MCP Servers Selection */}
        <MultiSelect
          label={isEditMode ? t('agents.form.enabledMCPs') : t('agents.form.mcpServersOptional')}
          placeholder={t('agents.form.selectMCPServers')}
          options={mcpServers.map((mcp) => ({
            id: mcp.id,
            name: mcp.name,
            description: mcp.description,
          }))}
          selectedIds={mcpIds}
          onChange={setMcpIds}
          loading={loadingMCPs}
        />

        {/* Action Buttons */}
        <div className="flex gap-3 pt-4">
          <Button
            type="button"
            variant="secondary"
            className="flex-1"
            onClick={handleClose}
            disabled={isSaving}
          >
            {t('common.button.cancel')}
          </Button>
          <Button type="submit" className="flex-1" disabled={isSaving || !name.trim()}>
            {isSaving ? (
              <span className="flex items-center gap-2">
                <Spinner size="sm" color="#ffffff" />
                {isEditMode ? t('common.button.saving') : t('common.button.creating')}
              </span>
            ) : isEditMode ? (
              t('common.button.saveChanges')
            ) : (
              t('agents.createAgent')
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
