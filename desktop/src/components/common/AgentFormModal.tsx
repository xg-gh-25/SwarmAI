import { useState, useEffect, useMemo } from 'react';
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

// Helper to convert model ID to a display option
const modelIdToOption = (id: string) => ({
  id,
  // Convert model ID to human-readable name (e.g., claude-sonnet-4-5 -> Claude Sonnet 4 5)
  name: id
    .split(/[-.]/)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' '),
  description: id,
});

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

  // Fetch API config to get model list and check if Bedrock is enabled
  const { data: apiConfig } = useQuery({
    queryKey: ['apiConfig'],
    queryFn: settingsService.getAPIConfiguration,
    enabled: isOpen,
  });
  const useBedrock = apiConfig?.use_bedrock ?? false;
  const availableModels = useMemo(() => apiConfig?.available_models ?? [], [apiConfig?.available_models]);
  const defaultModelFromSettings = apiConfig?.default_model ?? '';

  // Convert model IDs to dropdown options
  const modelOptions = useMemo(() => availableModels.map(modelIdToOption), [availableModels]);

  // Fetch skills (refetch every time modal opens to avoid stale cache)
  const { data: skills = [], isLoading: loadingSkills } = useQuery({
    queryKey: ['skills', isOpen],
    queryFn: skillsService.list,
    enabled: isOpen,
    staleTime: 0,
  });

  // Fetch MCP servers
  const { data: mcpServers = [], isLoading: loadingMCPs } = useQuery({
    queryKey: ['mcpServers', isOpen],
    queryFn: mcpService.list,
    enabled: isOpen,
    staleTime: 0,
  });

  // Fetch plugins
  const { data: plugins = [], isLoading: loadingPlugins } = useQuery({
    queryKey: ['plugins', isOpen],
    queryFn: pluginsService.listPlugins,
    enabled: isOpen,
    staleTime: 0,
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

  // Set default model for create mode (use default from settings)
  useEffect(() => {
    if (!isEditMode && !model && defaultModelFromSettings) {
      setModel(defaultModelFromSettings);
    } else if (!isEditMode && !model && availableModels.length > 0) {
      // Fallback to first available model if no default is set
      setModel(availableModels[0]);
    }
  }, [defaultModelFromSettings, availableModels, model, isEditMode]);

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
            options={modelOptions}
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
