import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SearchBar, Button, Modal, SkeletonTable, ResizableTable, ResizableTableCell, ConfirmDialog } from '../components/common';
import type { MCPServer, MCPServerCreateRequest } from '../types';
import { mcpService } from '../services/mcp';

// MCP table column configuration - will be translated via hook
const getMcpColumns = (t: (key: string) => string) => [
  { key: 'name', header: t('mcp.table.name'), initialWidth: 180, minWidth: 120 },
  { key: 'connectionType', header: t('mcp.table.type'), initialWidth: 140, minWidth: 100 },
  { key: 'endpoint', header: t('mcp.form.endpoint') || 'Endpoint', initialWidth: 300, minWidth: 150 },
  { key: 'description', header: t('common.label.description'), initialWidth: 250, minWidth: 120 },
  { key: 'actions', header: t('mcp.table.actions'), initialWidth: 100, minWidth: 80, align: 'right' as const },
];

export default function MCPPage() {
  const { t } = useTranslation();
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [selectedServer, setSelectedServer] = useState<MCPServer | null>(null);
  const [isInitialLoading, setIsInitialLoading] = useState(true);

  // Delete confirmation state
  const [deleteTarget, setDeleteTarget] = useState<MCPServer | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Get translated columns
  const MCP_COLUMNS = getMcpColumns(t);

  // Fetch MCP servers on mount
  useEffect(() => {
    const fetchServers = async () => {
      try {
        const data = await mcpService.list();
        setServers(data);
      } catch (error) {
        console.error('Failed to fetch MCP servers:', error);
      } finally {
        setIsInitialLoading(false);
      }
    };
    fetchServers();
  }, []);

  const filteredServers = servers.filter((server) =>
    server.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleDeleteClick = (server: MCPServer) => {
    setDeleteTarget(server);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await mcpService.delete(deleteTarget.id);
      setServers((prev) => prev.filter((server) => server.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (error) {
      console.error('Failed to delete MCP server:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSave = async (data: MCPServerCreateRequest, serverId?: string) => {
    try {
      if (serverId) {
        const updated = await mcpService.update(serverId, data);
        setServers((prev) =>
          prev.map((s) => (s.id === updated.id ? updated : s))
        );
      } else {
        const created = await mcpService.create(data);
        setServers((prev) => [...prev, created]);
      }
      setIsAddModalOpen(false);
      setSelectedServer(null);
    } catch (error) {
      console.error('Failed to save MCP server:', error);
    }
  };

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('mcp.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('mcp.subtitle')}</p>
        </div>
        <Button icon="add" onClick={() => setIsAddModalOpen(true)}>
          {t('mcp.addMcp')}
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t('mcp.searchPlaceholder')}
          className="w-96"
        />
      </div>

      {/* MCP Servers Table */}
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {isInitialLoading ? (
          <SkeletonTable rows={5} columns={5} />
        ) : (
          <ResizableTable columns={MCP_COLUMNS}>
            {filteredServers.map((server) => (
              <tr
                key={server.id}
                className="border-b border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors"
              >
                <ResizableTableCell>
                  <span className="text-[var(--color-text)] font-medium">{server.name}</span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="px-2 py-1 text-xs font-medium bg-primary/10 text-primary rounded uppercase">
                    {server.connectionType}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)]" title={server.endpoint}>
                    {server.endpoint || '-'}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)]" title={server.description || ''}>
                    {server.description || '-'}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell align="right">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      onClick={() => setSelectedServer(server)}
                      className="p-2 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                      title={t('mcp.editMcp')}
                    >
                      <span className="material-symbols-outlined text-xl">edit</span>
                    </button>
                    <button
                      onClick={() => handleDeleteClick(server)}
                      className="p-2 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                      title={t('mcp.deleteMcp')}
                    >
                      <span className="material-symbols-outlined text-xl">delete</span>
                    </button>
                  </div>
                </ResizableTableCell>
              </tr>
            ))}

            {filteredServers.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center">
                  <span className="material-symbols-outlined text-4xl text-[var(--color-text-muted)] mb-2">dns</span>
                  <p className="text-[var(--color-text-muted)]">{t('mcp.noMcps')}</p>
                </td>
              </tr>
            )}
          </ResizableTable>
        )}
      </div>

      {/* Add/Edit Modal */}
      <Modal
        isOpen={isAddModalOpen || selectedServer !== null}
        onClose={() => {
          setIsAddModalOpen(false);
          setSelectedServer(null);
        }}
        title={selectedServer ? t('mcp.editMcp') : t('mcp.addMcp')}
        size="lg"
      >
        <MCPServerForm
          server={selectedServer}
          onClose={() => {
            setIsAddModalOpen(false);
            setSelectedServer(null);
          }}
          onSave={handleSave}
        />
      </Modal>

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title={t('mcp.deleteMcp')}
        message={
          <>
            {t('mcp.deleteConfirm', { name: '' })}<strong>{deleteTarget?.name}</strong>?
            <br />
            <span className="text-sm">{t('common.message.cannotUndo')}</span>
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

// MCP Server Form Component
function MCPServerForm({
  server,
  onClose,
  onSave,
}: {
  server: MCPServer | null;
  onClose: () => void;
  onSave: (data: MCPServerCreateRequest, serverId?: string) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(server?.name || '');
  const [description, setDescription] = useState(server?.description || '');
  const [connectionType, setConnectionType] = useState<'stdio' | 'sse' | 'http'>(
    server?.connectionType || 'stdio'
  );
  const [command, setCommand] = useState(
    server?.connectionType === 'stdio' ? (server.config.command as string) : ''
  );
  const [args, setArgs] = useState(
    server?.connectionType === 'stdio' ? (server.config.args as string[]).join(' ') : ''
  );
  const [envVars, setEnvVars] = useState<{ key: string; value: string }[]>(() => {
    if (server?.connectionType === 'stdio' && server.config.env) {
      const env = server.config.env as Record<string, string>;
      return Object.entries(env).map(([key, value]) => ({ key, value }));
    }
    return [];
  });
  const [url, setUrl] = useState(
    server?.connectionType !== 'stdio' ? (server?.config.url as string) || '' : ''
  );

  // Compute duplicate env var keys for validation
  const duplicateEnvKeys = new Set<string>();
  const seenEnvKeys = new Set<string>();
  envVars.forEach(({ key }) => {
    const trimmed = key.trim();
    if (trimmed) {
      if (seenEnvKeys.has(trimmed)) duplicateEnvKeys.add(trimmed);
      seenEnvKeys.add(trimmed);
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (connectionType === 'stdio' && duplicateEnvKeys.size > 0) return;

    let config: Record<string, unknown> = {};

    if (connectionType === 'stdio') {
      const env: Record<string, string> = {};
      envVars.forEach(({ key, value }) => {
        if (key.trim()) env[key.trim()] = value;
      });
      config = { command, args: args.split(' ').filter(Boolean) };
      if (Object.keys(env).length > 0) {
        config.env = env;
      }
    } else {
      config = { url };
    }

    const data: MCPServerCreateRequest = {
      name,
      description: description || undefined,
      connectionType,
      config,
    };

    onSave(data, server?.id);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.name')}</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('mcp.form.namePlaceholder')}
          required
          className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.description')}</label>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t('mcp.form.descriptionPlaceholder')}
          className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.connectionType')}</label>
        <div className="flex gap-4">
          {(['stdio', 'sse', 'http'] as const).map((type) => (
            <label key={type} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="connectionType"
                value={type}
                checked={connectionType === type}
                onChange={(e) => setConnectionType(e.target.value as typeof connectionType)}
                className="w-4 h-4 border-[var(--color-border)] bg-[var(--color-bg)] text-primary focus:ring-primary"
              />
              <span className="text-[var(--color-text)] uppercase">{type}</span>
            </label>
          ))}
        </div>
      </div>

      {connectionType === 'stdio' ? (
        <>
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.command')}</label>
            <input
              type="text"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder={t('mcp.form.commandPlaceholder')}
              required
              className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.args')}</label>
            <input
              type="text"
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder={t('mcp.form.argsPlaceholder')}
              className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.env')}</label>
            <p className="text-xs text-[var(--color-text-muted)] mb-3">{t('mcp.form.envHelp')}</p>
            <div className="space-y-2">
              {envVars.map((envVar, index) => {
                const isDuplicate = envVar.key.trim() && duplicateEnvKeys.has(envVar.key.trim());
                return (
                  <div key={index} className="flex gap-2 items-center">
                    <input
                      type="text"
                      value={envVar.key}
                      onChange={(e) => {
                        const updated = [...envVars];
                        updated[index] = { ...updated[index], key: e.target.value };
                        setEnvVars(updated);
                      }}
                      placeholder={t('mcp.form.envKeyPlaceholder')}
                      className={`flex-1 px-3 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none text-sm ${isDuplicate ? 'border-status-error focus:border-status-error' : 'border-[var(--color-border)] focus:border-primary'}`}
                    />
                    <input
                      type="password"
                      value={envVar.value}
                      onChange={(e) => {
                        const updated = [...envVars];
                        updated[index] = { ...updated[index], value: e.target.value };
                        setEnvVars(updated);
                      }}
                      placeholder={t('mcp.form.envValuePlaceholder')}
                      className="flex-1 px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => setEnvVars(envVars.filter((_, i) => i !== index))}
                      className="p-2 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                    >
                      <span className="material-symbols-outlined text-lg">close</span>
                    </button>
                  </div>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => setEnvVars([...envVars, { key: '', value: '' }])}
              className="mt-2 flex items-center gap-1 text-sm text-primary hover:text-primary/80 transition-colors"
            >
              <span className="material-symbols-outlined text-lg">add</span>
              {t('mcp.form.addEnvVar')}
            </button>
          </div>
        </>
      ) : (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('mcp.form.url')}</label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t('mcp.form.urlPlaceholder')}
            required
            className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary"
          />
        </div>
      )}

      <div className="flex gap-3 pt-4">
        <Button type="button" variant="secondary" onClick={onClose}>
          {t('common.button.cancel')}
        </Button>
        <Button type="submit" className="flex-1">
          {server ? t('common.button.saveChanges') : t('mcp.addMcp')}
        </Button>
      </div>
    </form>
  );
}
