import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SearchBar, StatusBadge, Button, SkeletonTable, ResizableTable, ResizableTableCell, ConfirmDialog, AgentFormModal } from '../components/common';
import type { Agent, AgentCreateRequest, Skill, MCPServer } from '../types';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';

export default function AgentsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  // Agent table column configuration
  const AGENT_COLUMNS = [
    { key: 'name', header: t('agents.table.name'), initialWidth: 180, minWidth: 120 },
    { key: 'status', header: t('agents.table.status'), initialWidth: 100, minWidth: 80 },
    { key: 'model', header: t('agents.table.model'), initialWidth: 200, minWidth: 150 },
    { key: 'skills', header: t('agents.table.skills'), initialWidth: 200, minWidth: 120 },
    { key: 'mcps', header: t('agents.table.mcps'), initialWidth: 200, minWidth: 120 },
    { key: 'actions', header: t('agents.table.actions'), initialWidth: 140, minWidth: 100, align: 'right' as const },
  ];
  const [agents, setAgents] = useState<Agent[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);

  // Skills, MCPs for table display
  const [skills, setSkills] = useState<Skill[]>([]);
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);

  // Fetch agents on mount
  useEffect(() => {
    const fetchData = async () => {
      try {
        const agentsData = await agentsService.list();
        setAgents(agentsData);
      } catch (error) {
        console.error('Failed to fetch agents:', error);
      } finally {
        setIsInitialLoading(false);
      }
    };
    fetchData();
  }, []);

  // Fetch skills and MCPs on mount for table display
  useEffect(() => {
    const fetchSkillsAndMCPs = async () => {
      try {
        const [skillsData, mcpsData] = await Promise.all([
          skillsService.list(),
          mcpService.list(),
        ]);
        setSkills(skillsData);
        setMcpServers(mcpsData);
      } catch (error) {
        console.error('Failed to fetch skills/MCPs:', error);
      }
    };
    fetchSkillsAndMCPs();
  }, []);

  // Helper functions to get names from IDs
  const getSkillNames = (agent: Agent) => {
    if (agent.allowAllSkills) return t('agents.allSkills');
    if (!agent.skillIds || agent.skillIds.length === 0) return '-';
    const names = agent.skillIds
      .map((id) => skills.find((s) => s.id === id)?.name)
      .filter(Boolean);
    return names.length > 0 ? names.join(', ') : '-';
  };

  const getMcpNames = (mcpIds: string[]) => {
    if (!mcpIds || mcpIds.length === 0) return '-';
    const names = mcpIds
      .map((id) => mcpServers.find((m) => m.id === id)?.name)
      .filter(Boolean);
    return names.length > 0 ? names.join(', ') : '-';
  };

  const filteredAgents = agents.filter(
    (agent) =>
      agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.model?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDeleteClick = (agent: Agent) => {
    setDeleteTarget(agent);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await agentsService.delete(deleteTarget.id);
      setAgents((prev) => prev.filter((a) => a.id !== deleteTarget.id));
      setDeleteTarget(null);
      // Invalidate React Query cache so other pages (like ChatPage) get updated data
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    } catch (error) {
      console.error('Failed to delete agent:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleStartChat = (agentId: string) => {
    navigate(`/chat?agentId=${agentId}`);
  };

  const handleCreateAgent = async (data: Agent | AgentCreateRequest) => {
    // For create mode, data will always be AgentCreateRequest (no id property)
    if (!('id' in data)) {
      const created = await agentsService.create(data);
      setAgents((prev) => [...prev, created]);
      // Invalidate React Query cache so other pages get updated data
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    }
  };

  const handleEditAgent = async (data: Agent | AgentCreateRequest) => {
    // For edit mode, data will always be Agent (has id property)
    if ('id' in data) {
      const updated = await agentsService.update(data.id, data);
      setAgents((prev) =>
        prev.map((agent) => (agent.id === updated.id ? updated : agent))
      );
      // Invalidate React Query cache so other pages get updated data
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    }
  };

  const handleOpenEditModal = (agent: Agent) => {
    setEditingAgent(agent);
    setIsEditModalOpen(true);
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('agents.title')}</h1>
          <p className="text-muted mt-1">{t('agents.subtitle')}</p>
        </div>
        <Button icon="add" onClick={() => setIsCreateModalOpen(true)}>
          {t('agents.addAgent')}
        </Button>
      </div>

      <div className="flex gap-6">
        {/* Agent List */}
        <div className="flex-1">
          <SearchBar
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder={t('agents.searchPlaceholder')}
            className="mb-4"
          />

          <div className="bg-dark-card border border-dark-border rounded-xl overflow-hidden">
            {isInitialLoading ? (
              <SkeletonTable rows={5} columns={6} />
            ) : (
              <ResizableTable columns={AGENT_COLUMNS}>
                {filteredAgents.map((agent) => (
                  <tr
                    key={agent.id}
                    className="border-b border-dark-border hover:bg-dark-hover transition-colors"
                  >
                    <ResizableTableCell>
                      <span className="text-white font-medium">{agent.name}</span>
                    </ResizableTableCell>
                    <ResizableTableCell>
                      <StatusBadge status={agent.status} />
                    </ResizableTableCell>
                    <ResizableTableCell>
                      <span className="text-muted">{agent.model}</span>
                    </ResizableTableCell>
                    <ResizableTableCell>
                      <span className="text-muted" title={getSkillNames(agent)}>
                        {getSkillNames(agent)}
                      </span>
                    </ResizableTableCell>
                    <ResizableTableCell>
                      <span className="text-muted" title={getMcpNames(agent.mcpIds)}>
                        {getMcpNames(agent.mcpIds)}
                      </span>
                    </ResizableTableCell>
                    <ResizableTableCell align="right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleStartChat(agent.id);
                          }}
                          className="p-2 rounded-lg text-muted hover:text-primary hover:bg-primary/10 transition-colors"
                          title={t('agents.startChat')}
                        >
                          <span className="material-symbols-outlined text-xl">chat</span>
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleOpenEditModal(agent);
                          }}
                          className="p-2 rounded-lg text-muted hover:text-white hover:bg-dark-hover transition-colors"
                          title={t('chat.editAgent')}
                        >
                          <span className="material-symbols-outlined text-xl">edit</span>
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteClick(agent);
                          }}
                          className="p-2 rounded-lg text-muted hover:text-status-error hover:bg-status-error/10 transition-colors"
                          title={t('agents.deleteAgent')}
                        >
                          <span className="material-symbols-outlined text-xl">delete</span>
                        </button>
                      </div>
                    </ResizableTableCell>
                  </tr>
                ))}
                {filteredAgents.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-6 py-12 text-center">
                      <span className="material-symbols-outlined text-4xl text-muted mb-2">smart_toy</span>
                      <p className="text-muted">{t('agents.noAgents')}</p>
                    </td>
                  </tr>
                )}
              </ResizableTable>
            )}
          </div>
        </div>
      </div>

      {/* Create Modal */}
      <AgentFormModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onSave={handleCreateAgent}
        agent={null}
      />

      {/* Edit Modal */}
      <AgentFormModal
        isOpen={isEditModalOpen}
        onClose={() => {
          setIsEditModalOpen(false);
          setEditingAgent(null);
        }}
        onSave={handleEditAgent}
        agent={editingAgent}
      />

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title={t('agents.deleteAgent')}
        message={
          <>
            Are you sure you want to delete <strong>{deleteTarget?.name}</strong>?
            <br />
            <span className="text-sm">This action cannot be undone.</span>
          </>
        }
        confirmText={t('common.button.delete')}
        cancelText={t('common.button.cancel')}
        variant="danger"
        isLoading={isDeleting}
      />
    </div>
  );
}
