import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import type { Agent, AgentCreateRequest } from '../../types';
import { agentsService } from '../../services/agents';
import { skillsService } from '../../services/skills';
import { mcpService } from '../../services/mcp';
import { pluginsService } from '../../services/plugins';
import Modal from './Modal';
import Dropdown from './Dropdown';
import MultiSelect from './MultiSelect';
import ToolSelector, { getDefaultEnabledTools } from './ToolSelector';
import Button from './Button';
import { Spinner } from './SkeletonLoader';

// Model options with descriptions for the Dropdown component
const MODEL_OPTIONS = [
  { id: 'claude-sonnet-4-5-20250929', name: 'Claude Sonnet 4.5', description: 'Best balance of speed and intelligence' },
  { id: 'claude-haiku-4-5-20251001', name: 'Claude Haiku 4.5', description: 'Fastest and most cost-effective' },
  { id: 'claude-opus-4-5-20251101', name: 'Claude Opus 4.5', description: 'Most intelligent, best for complex tasks' },
];

// Helper to convert models array to dropdown options
const getModelOptions = (models: string[]) => {
  return models.map((model) => {
    const predefined = MODEL_OPTIONS.find((opt) => opt.id === model);
    if (predefined) return predefined;
    return { id: model, name: model };
  });
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
  const isEditMode = !!agent;
  const modalTitle = title || (isEditMode ? 'Edit Agent' : 'Create New Agent');

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

  // Fetch models
  const { data: models = [] } = useQuery({
    queryKey: ['models'],
    queryFn: agentsService.listModels,
    enabled: isOpen,
  });

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
    // Note: models is intentionally excluded from dependencies to prevent
    // form reset on query refetch. The second useEffect handles default model.
  }, [isOpen, agent]);

  // Update default model when models list loads (for create mode)
  useEffect(() => {
    if (!isEditMode && models.length > 0 && !model) {
      setModel(models[0]);
    }
  }, [models, model, isEditMode]);

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
          <label className="block text-sm font-medium text-muted mb-2">Agent Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Enter agent name"
            required
            className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder:text-muted focus:outline-none focus:border-primary"
          />
        </div>

        {/* Description */}
        <div>
          <label className="block text-sm font-medium text-muted mb-2">Description</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Describe what this agent does"
            className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder:text-muted focus:outline-none focus:border-primary"
          />
        </div>

        {/* System Prompt */}
        <div>
          <label className="block text-sm font-medium text-muted mb-2">System Prompt</label>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Enter system prompt instructions (optional)"
            rows={4}
            className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder:text-muted focus:outline-none focus:border-primary resize-none"
          />
        </div>

        {/* Global User Mode Toggle */}
        <div className="flex items-center justify-between">
          <div>
            <label className="block text-sm font-medium text-muted">Global User Mode</label>
            <p className="text-xs text-muted mt-0.5">Use home directory with full file access</p>
          </div>
          <button
            type="button"
            onClick={() => setGlobalUserMode(!globalUserMode)}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              globalUserMode ? 'bg-primary' : 'bg-dark-border'
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
            <label className="block text-sm font-medium text-muted">Enable Human Approval</label>
            <p className="text-xs text-muted mt-0.5">Dangerous commands require user confirmation</p>
          </div>
          <button
            type="button"
            onClick={() => setEnableHumanApproval(!enableHumanApproval)}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              enableHumanApproval ? 'bg-primary' : 'bg-dark-border'
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
        <Dropdown
          label="Base Model"
          options={getModelOptions(models)}
          selectedId={model || null}
          onChange={setModel}
          placeholder="Select a model..."
        />

        {/* Built-in Tools */}
        <ToolSelector selectedTools={allowedTools} onChange={setAllowedTools} />

        {/* Plugins Selection */}
        <MultiSelect
          label={isEditMode ? 'Enabled Plugins' : 'Plugins (Optional)'}
          placeholder="Select plugins..."
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
            <label className="block text-sm font-medium text-muted">Allow All Skills</label>
            <p className="text-xs text-muted mt-0.5">
              {globalUserMode
                ? 'Required in Global User Mode (skill restrictions not supported)'
                : 'Grant access to all available skills'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => !globalUserMode && setAllowAllSkills(!allowAllSkills)}
            disabled={globalUserMode}
            className={clsx(
              'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none',
              globalUserMode ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
              allowAllSkills ? 'bg-primary' : 'bg-dark-border'
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
          label={isEditMode ? 'Enabled Skills' : 'Skills (Optional)'}
          placeholder={
            globalUserMode
              ? 'All skills enabled (Global User Mode)'
              : allowAllSkills
                ? 'All skills enabled'
                : 'Select skills...'
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
          label={isEditMode ? 'Enabled MCPs' : 'MCP Servers (Optional)'}
          placeholder="Select MCP servers..."
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
            Cancel
          </Button>
          <Button type="submit" className="flex-1" disabled={isSaving || !name.trim()}>
            {isSaving ? (
              <span className="flex items-center gap-2">
                <Spinner size="sm" color="#ffffff" />
                {isEditMode ? 'Saving...' : 'Creating...'}
              </span>
            ) : isEditMode ? (
              'Save Changes'
            ) : (
              'Create Agent'
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
