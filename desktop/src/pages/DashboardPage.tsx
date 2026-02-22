import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';
import { pluginsService } from '../services/plugins';
import type { Agent, Skill } from '../types';
import { Skeleton } from '../components/common';

const quickActions = [
  {
    title: 'Start a Chat',
    description: 'Begin a conversation with an AI agent',
    icon: 'chat',
    path: '/chat',
    color: 'bg-blue-500/20 text-blue-400',
  },
  {
    title: 'Manage Agents',
    description: 'Create and configure your AI agents',
    icon: 'smart_toy',
    path: '/agents',
    color: 'bg-purple-500/20 text-purple-400',
  },
  {
    title: 'View Skills',
    description: 'Browse and manage available skills',
    icon: 'construction',
    path: '/skills',
    color: 'bg-green-500/20 text-green-400',
  },
  {
    title: 'MCP Servers',
    description: 'Monitor and configure MCP connections',
    icon: 'dns',
    path: '/mcp',
    color: 'bg-orange-500/20 text-orange-400',
  },
  {
    title: 'Plugins',
    description: 'Install and manage plugins from Git',
    icon: 'extension',
    path: '/plugins',
    color: 'bg-teal-500/20 text-teal-400',
  },
];

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
  recentAgents: Agent[];
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const [agents, skills, mcpServers, plugins] = await Promise.all([
          agentsService.list(),
          skillsService.list(),
          mcpService.list(),
          pluginsService.listPlugins(),
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
        <h1 className="text-3xl font-bold text-white mb-2">Welcome to Owork</h1>
        <p className="text-muted">
          Manage your AI agents, skills, and MCP server connections
        </p>
      </div>

      {/* Quick Actions */}
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-white mb-4">Quick Actions</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {quickActions.map((action) => (
            <Link
              key={action.path}
              to={action.path}
              className="p-6 bg-dark-card border border-dark-border rounded-xl hover:bg-dark-hover transition-colors group"
            >
              <div
                className={`w-12 h-12 rounded-lg flex items-center justify-center mb-4 ${action.color}`}
              >
                <span className="material-symbols-outlined text-2xl">{action.icon}</span>
              </div>
              <h3 className="text-lg font-semibold text-white mb-1 group-hover:text-primary transition-colors">
                {action.title}
              </h3>
              <p className="text-sm text-muted">{action.description}</p>
            </Link>
          ))}
        </div>
      </div>

      {/* Stats Overview */}
      <div className="mb-8">
        <h2 className="text-xl font-semibold text-white mb-4">Overview</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Total Agents */}
          <div className="p-6 bg-dark-card border border-dark-border rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-muted">Total Agents</span>
              <span className="material-symbols-outlined text-primary">smart_toy</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-white">{stats?.agents.total || 0}</p>
                <p className="text-sm text-status-online mt-1">
                  {stats?.agents.active || 0} active
                </p>
              </>
            )}
          </div>

          {/* Available Skills */}
          <div className="p-6 bg-dark-card border border-dark-border rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-muted">Available Skills</span>
              <span className="material-symbols-outlined text-green-400">construction</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-32" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-white">{stats?.skills.total || 0}</p>
                <p className="text-sm text-muted mt-1">
                  {stats?.skills.system || 0} system, {stats?.skills.custom || 0} custom
                </p>
              </>
            )}
          </div>

          {/* MCP Servers */}
          <div className="p-6 bg-dark-card border border-dark-border rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-muted">MCP Servers</span>
              <span className="material-symbols-outlined text-orange-400">dns</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-white">{stats?.mcpServers.total || 0}</p>
                <p className="text-sm text-muted mt-1">configured</p>
              </>
            )}
          </div>

          {/* Plugins */}
          <div className="p-6 bg-dark-card border border-dark-border rounded-xl">
            <div className="flex items-center justify-between mb-4">
              <span className="text-muted">Plugins</span>
              <span className="material-symbols-outlined text-teal-400">extension</span>
            </div>
            {isLoading ? (
              <>
                <Skeleton className="h-9 w-16 mb-1" />
                <Skeleton className="h-5 w-24" />
              </>
            ) : (
              <>
                <p className="text-3xl font-bold text-white">{stats?.plugins.total || 0}</p>
                <p className="text-sm text-status-online mt-1">
                  {stats?.plugins.installed || 0} installed
                </p>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Recent Agents */}
      <div>
        <h2 className="text-xl font-semibold text-white mb-4">Recent Agents</h2>
        <div className="bg-dark-card border border-dark-border rounded-xl">
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
            <div className="divide-y divide-dark-border">
              {stats.recentAgents.map((agent) => (
                <Link
                  key={agent.id}
                  to={`/chat?agentId=${agent.id}`}
                  className="flex items-center gap-4 p-4 hover:bg-dark-hover transition-colors"
                >
                  <div className="w-10 h-10 rounded-lg bg-primary/20 flex items-center justify-center">
                    <span className="material-symbols-outlined text-primary">smart_toy</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-white font-medium truncate">{agent.name}</p>
                    <p className="text-sm text-muted truncate">{agent.model}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        agent.status === 'active' ? 'bg-status-online' : 'bg-status-offline'
                      }`}
                    />
                    <span className="text-sm text-muted capitalize">{agent.status}</span>
                  </div>
                  <span className="material-symbols-outlined text-muted">chevron_right</span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="text-center py-8">
              <span className="material-symbols-outlined text-4xl text-muted mb-2">smart_toy</span>
              <p className="text-muted">No agents yet</p>
              <Link
                to="/agents"
                className="inline-flex items-center gap-2 mt-4 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
              >
                <span className="material-symbols-outlined text-xl">add</span>
                Create Agent
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
