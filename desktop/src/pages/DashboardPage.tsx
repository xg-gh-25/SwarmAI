import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';
import { pluginsService } from '../services/plugins';
import { tasksService } from '../services/tasks';
import type { Agent, Skill } from '../types';
import { Skeleton } from '../components/common';

interface DashboardStats {
  agents: {
    total: number;
    active: number;
  };
  skills: {
    total: number;
    system: number;
    custom: number;
  };
  mcpServers: {
    total: number;
  };
  plugins: {
    total: number;
    installed: number;
  };
  tasks: {
    total: number;
    running: number;
  };
  recentAgents: Agent[];
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const quickActions = [
    {
      titleKey: 'dashboard.action.startChat',
      descriptionKey: 'dashboard.action.startChatDesc',
      icon: 'chat',
      path: '/chat',
      color: 'bg-blue-500/20 text-blue-400',
    },
    {
      titleKey: 'dashboard.action.manageAgents',
      descriptionKey: 'dashboard.action.manageAgentsDesc',
      icon: 'smart_toy',
      path: '/agents',
      color: 'bg-purple-500/20 text-purple-400',
    },
    {
      titleKey: 'dashboard.action.viewSkills',
      descriptionKey: 'dashboard.action.viewSkillsDesc',
      icon: 'construction',
      path: '/skills',
      color: 'bg-green-500/20 text-green-400',
    },
    {
      titleKey: 'dashboard.action.mcpServers',
      descriptionKey: 'dashboard.action.mcpServersDesc',
      icon: 'dns',
      path: '/mcp',
      color: 'bg-orange-500/20 text-orange-400',
    },
    {
      titleKey: 'dashboard.action.plugins',
      descriptionKey: 'dashboard.action.pluginsDesc',
      icon: 'extension',
      path: '/plugins',
      color: 'bg-teal-500/20 text-teal-400',
    },
    {
      titleKey: 'dashboard.action.tasks',
      descriptionKey: 'dashboard.action.tasksDesc',
      icon: 'task_alt',
      path: '/tasks',
      color: 'bg-amber-500/20 text-amber-400',
    },
  ];

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const [agents, skills, mcpServers, plugins, tasks, runningTaskCount] = await Promise.all([
          agentsService.list(),
          skillsService.list(),
          mcpService.list(),
          pluginsService.listPlugins(),
          tasksService.list(),
          tasksService.getRunningCount(),
        ]);

        const activeAgents = agents.filter((a: Agent) => a.status === 'active').length;
        const systemSkills = skills.filter((s: Skill) => s.isSystem).length;
        const customSkills = skills.filter((s: Skill) => !s.isSystem).length;
        const installedPlugins = plugins.filter((p) => p.status === 'installed').length;

        // Get 3 most recent agents
        const recentAgents = [...agents]
          .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
          .slice(0, 3);

        setStats({
          agents: {
            total: agents.length,
            active: activeAgents,
          },
          skills: {
            total: skills.length,
            system: systemSkills,
            custom: customSkills,
          },
          mcpServers: {
            total: mcpServers.length,
          },
          plugins: {
            total: plugins.length,
            installed: installedPlugins,
          },
          tasks: {
            total: tasks.length,
            running: runningTaskCount,
          },
          recentAgents,
        });
      } catch (error) {
        console.error('Failed to fetch dashboard stats:', error);
      } finally {
        setIsLoading(false);
      }
    };

    fetchStats();
  }, []);

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-[var(--color-text)] mb-2">{t('dashboard.title')}</h1>
        <p className="text-[var(--color-text-muted)]">
          {t('dashboard.subtitle')}
        </p>
      </div>

      {/* Quick Actions */}
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-[var(--color-text)] mb-4">{t('dashboard.quickActions')}</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {quickActions.map((action) => (
            <Link
              key={action.path}
              to={action.path}
              className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl hover:bg-[var(--color-hover)] transition-colors group"
            >
              <div
                className={`w-12 h-12 rounded-lg flex items-center justify-center mb-4 ${action.color}`}
              >
                <span className="material-symbols-outlined text-2xl">{action.icon}</span>
              </div>
              <h3 className="text-lg font-semibold text-[var(--color-text)] mb-1 group-hover:text-primary transition-colors">
                {t(action.titleKey)}
              </h3>
              <p className="text-sm text-[var(--color-text-muted)]">{t(action.descriptionKey)}</p>
            </Link>
          ))}
        </div>
      </div>

      {/* Stats Overview */}
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-[var(--color-text)] mb-4">{t('dashboard.overview')}</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Total Agents */}
          <div className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[var(--color-text-muted)]">{t('dashboard.stats.totalAgents')}</span>
              <span className="material-symbols-outlined text-primary">smart_toy</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-[var(--color-text)]">{stats?.agents.total || 0}</p>
                <p className="text-sm text-status-online mt-1">
                  {t('dashboard.stats.active', { count: stats?.agents.active || 0 })}
                </p>
              </>
            )}
          </div>

          {/* Available Skills */}
          <div className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[var(--color-text-muted)]">{t('dashboard.stats.availableSkills')}</span>
              <span className="material-symbols-outlined text-green-400">construction</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-32" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-[var(--color-text)]">{stats?.skills.total || 0}</p>
                <p className="text-sm text-[var(--color-text-muted)] mt-1">
                  {t('dashboard.stats.skillsBreakdown', { system: stats?.skills.system || 0, custom: stats?.skills.custom || 0 })}
                </p>
              </>
            )}
          </div>

          {/* MCP Servers */}
          <div className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[var(--color-text-muted)]">{t('dashboard.stats.mcpServers')}</span>
              <span className="material-symbols-outlined text-orange-400">dns</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-[var(--color-text)]">{stats?.mcpServers.total || 0}</p>
                <p className="text-sm text-[var(--color-text-muted)] mt-1">{t('dashboard.stats.configured')}</p>
              </>
            )}
          </div>

          {/* Plugins */}
          <div className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[var(--color-text-muted)]">{t('dashboard.stats.plugins')}</span>
              <span className="material-symbols-outlined text-teal-400">extension</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-[var(--color-text)]">{stats?.plugins.total || 0}</p>
                <p className="text-sm text-status-online mt-1">
                  {t('dashboard.stats.installed', { count: stats?.plugins.installed || 0 })}
                </p>
              </>
            )}
          </div>

          {/* Tasks */}
          <div className="p-6 bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[var(--color-text-muted)]">{t('dashboard.stats.tasks')}</span>
              <span className="material-symbols-outlined text-amber-400">task_alt</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-[var(--color-text)]">{stats?.tasks.total || 0}</p>
                <p className="text-sm text-[var(--color-text-muted)] mt-1">
                  {(stats?.tasks.running || 0) > 0 ? (
                    <span className="text-status-online">{t('dashboard.stats.running', { running: stats?.tasks.running || 0 })}</span>
                  ) : (
                    t('dashboard.stats.totalTasks', { total: stats?.tasks.total || 0 })
                  )}
                </p>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Recent Agents */}
      <div>
        <h2 className="text-xl font-semibold text-[var(--color-text)] mb-4">{t('dashboard.recentAgents')}</h2>
        <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl">
          {isLoading ? (
            <div className="p-6 space-y-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-4">
                  <Skeleton className="w-10 h-10 rounded-lg" />
                  <div className="flex-1">
                    <Skeleton className="h-5 w-32 mb-1" />
                    <Skeleton className="h-4 w-48" />
                  </div>
                </div>
              ))}
            </div>
          ) : stats?.recentAgents && stats.recentAgents.length > 0 ? (
            <div className="divide-y divide-[var(--color-border)]">
              {stats.recentAgents.map((agent) => (
                <Link
                  key={agent.id}
                  to={`/chat?agentId=${agent.id}`}
                  className="flex items-center gap-4 p-4 hover:bg-[var(--color-hover)] transition-colors"
                >
                  <div className="w-10 h-10 rounded-lg bg-primary/20 flex items-center justify-center">
                    <span className="material-symbols-outlined text-primary">smart_toy</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[var(--color-text)] font-medium truncate">{agent.name}</p>
                    <p className="text-sm text-[var(--color-text-muted)] truncate">{agent.model}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        agent.status === 'active' ? 'bg-status-online' : 'bg-status-offline'
                      }`}
                    />
                    <span className="text-sm text-[var(--color-text-muted)] capitalize">{agent.status}</span>
                  </div>
                  <span className="material-symbols-outlined text-[var(--color-text-muted)]">chevron_right</span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="text-center py-8">
              <span className="material-symbols-outlined text-4xl text-[var(--color-text-muted)] mb-2">smart_toy</span>
              <p className="text-[var(--color-text-muted)]">{t('dashboard.noAgents')}</p>
              <Link
                to="/agents"
                className="inline-flex items-center gap-2 mt-4 px-4 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors"
              >
                <span className="material-symbols-outlined text-xl">add</span>
                {t('dashboard.createAgent')}
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
