/**
 * Unified MCP settings panel for workspace settings.
 *
 * Replaces MCPPage, MCPCatalogModal, MCPServersModal, McpsTab.
 * Two sections: Catalog Integrations (toggle + env) and Dev/Personal (full CRUD).
 *
 * Key exports:
 * - MCPSettingsPanel — Main component
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import Button from '../common/Button';
import { mcpConfigService } from '../../services/mcpConfig';
import type { ConfigEntry, DevCreateRequest } from '../../services/mcpConfig';

export default function MCPSettingsPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showAddForm, setShowAddForm] = useState(false);

  const { data: catalogEntries = [], isLoading: catalogLoading } = useQuery({
    queryKey: ['mcpCatalog'],
    queryFn: () => mcpConfigService.listCatalog(),
  });

  const { data: devEntries = [], isLoading: devLoading } = useQuery({
    queryKey: ['mcpDev'],
    queryFn: () => mcpConfigService.listDev(),
  });

  const catalogMutation = useMutation({
    mutationFn: ({ id, update }: { id: string; update: { enabled?: boolean; env?: Record<string, string> } }) =>
      mcpConfigService.updateCatalogEntry(id, update),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mcpCatalog'] }),
  });

  const createMutation = useMutation({
    mutationFn: (entry: DevCreateRequest) => mcpConfigService.createDevEntry(entry),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcpDev'] });
      setShowAddForm(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => mcpConfigService.deleteDevEntry(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mcpDev'] }),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      mcpConfigService.updateDevEntry(id, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mcpDev'] }),
  });

  const isLoading = catalogLoading || devLoading;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-[var(--color-text-muted)]">
        {t('common.loading')}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-[var(--color-text)]">{t('mcp.title')}</h3>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            {t('mcp.subtitle')}
          </p>
        </div>
        <Button icon="add" onClick={() => setShowAddForm(true)}>{t('mcp.addMcp')}</Button>
      </div>

      {/* Catalog Section */}
      <section>
        <h4 className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wide">
          {t('mcp.catalog.title')} ({catalogEntries.length})
        </h4>
        <div className="space-y-2">
          {catalogEntries.map((entry) => (
            <CatalogRow
              key={entry.id}
              entry={entry}
              onToggle={(enabled) => catalogMutation.mutate({ id: entry.id, update: { enabled } })}
              onEnvUpdate={(env) => catalogMutation.mutate({ id: entry.id, update: { env } })}
            />
          ))}
          {catalogEntries.length === 0 && (
            <p className="text-sm text-[var(--color-text-muted)] py-4 text-center">{t('mcp.catalog.empty')}</p>
          )}
        </div>
      </section>

      {/* Dev Section */}
      <section>
        <h4 className="text-sm font-medium text-[var(--color-text-muted)] mb-3 uppercase tracking-wide">
          {t('mcp.dev.title', { defaultValue: 'Dev / Personal' })} ({devEntries.length})
        </h4>
        <div className="space-y-2">
          {devEntries.map((entry) => (
            <DevRow
              key={entry.id}
              entry={entry}
              onToggle={(enabled) => toggleMutation.mutate({ id: entry.id, enabled })}
              onDelete={() => deleteMutation.mutate(entry.id)}
            />
          ))}
          {devEntries.length === 0 && (
            <p className="text-sm text-[var(--color-text-muted)] py-4 text-center">{t('mcp.noMcps')}</p>
          )}
        </div>
      </section>

      {/* Add Form Modal */}
      {showAddForm && (
        <AddDevForm
          onSubmit={(entry) => createMutation.mutate(entry)}
          onCancel={() => setShowAddForm(false)}
          isLoading={createMutation.isPending}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Catalog Row
// ---------------------------------------------------------------------------

function CatalogRow({
  entry,
  onToggle,
  onEnvUpdate,
}: {
  entry: ConfigEntry;
  onToggle: (enabled: boolean) => void;
  onEnvUpdate: (env: Record<string, string>) => void;
}) {
  const [showEnv, setShowEnv] = useState(false);
  const currentEnv = (entry.config?.env as Record<string, string>) || {};

  return (
    <div className={clsx(
      'p-3 rounded-lg border',
      'border-[var(--color-border)] bg-[var(--color-card)]',
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <span className="material-symbols-outlined text-lg text-primary">hub</span>
          <div className="min-w-0">
            <span className="text-sm font-medium text-[var(--color-text)]">{entry.name}</span>
            {entry.description && (
              <p className="text-xs text-[var(--color-text-muted)] truncate">{entry.description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {entry.requiredEnv && entry.requiredEnv.length > 0 && (
            <button
              onClick={() => setShowEnv(!showEnv)}
              className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              <span className="material-symbols-outlined text-lg">settings</span>
            </button>
          )}
          <ToggleSwitch checked={entry.enabled} onChange={onToggle} />
        </div>
      </div>
      {showEnv && entry.requiredEnv && (
        <EnvFields
          requiredEnv={entry.requiredEnv}
          optionalEnv={entry.optionalEnv || []}
          currentEnv={currentEnv}
          onSave={onEnvUpdate}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dev Row
// ---------------------------------------------------------------------------

function DevRow({
  entry,
  onToggle,
  onDelete,
}: {
  entry: ConfigEntry;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  const isPlugin = entry.source === 'plugin';

  return (
    <div className={clsx(
      'flex items-center justify-between p-3 rounded-lg border',
      'border-[var(--color-border)] bg-[var(--color-card)]',
    )}>
      <div className="flex items-center gap-3 min-w-0">
        <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
          {isPlugin ? 'extension' : 'terminal'}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--color-text)]">{entry.name}</span>
            {isPlugin && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400">Plugin</span>
            )}
          </div>
          {entry.description && (
            <p className="text-xs text-[var(--color-text-muted)] truncate">{entry.description}</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {!isPlugin && (
          <button
            onClick={onDelete}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-status-error"
          >
            <span className="material-symbols-outlined text-lg">delete</span>
          </button>
        )}
        <ToggleSwitch checked={entry.enabled} onChange={onToggle} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toggle Switch
// ---------------------------------------------------------------------------

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="relative inline-flex items-center cursor-pointer shrink-0">
      <input
        type="checkbox"
        checked={checked}
        onChange={() => onChange(!checked)}
        className="sr-only peer"
      />
      <div className={clsx(
        'w-9 h-5 rounded-full transition-colors',
        checked ? 'bg-primary' : 'bg-[var(--color-border)]',
      )}>
        <div className={clsx(
          'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform',
          checked && 'translate-x-4',
        )} />
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Env Fields (for catalog entries)
// ---------------------------------------------------------------------------

function EnvFields({
  requiredEnv,
  optionalEnv,
  currentEnv,
  onSave,
}: {
  requiredEnv: Array<{ key: string; label: string; placeholder?: string; secret?: boolean }>;
  optionalEnv: Array<{ key: string; label: string; default?: string }>;
  currentEnv: Record<string, string>;
  onSave: (env: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({ ...currentEnv });

  const update = (key: string, val: string) => setValues((prev) => ({ ...prev, [key]: val }));

  return (
    <div className="mt-3 pt-3 border-t border-[var(--color-border)] space-y-2">
      {requiredEnv.map((field) => (
        <div key={field.key}>
          <label className="block text-xs text-[var(--color-text-muted)] mb-1">{field.label}</label>
          <input
            type={field.secret ? 'password' : 'text'}
            value={values[field.key] || ''}
            onChange={(e) => update(field.key, e.target.value)}
            placeholder={field.placeholder}
            className="w-full px-3 py-1.5 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]"
          />
        </div>
      ))}
      {optionalEnv.map((field) => (
        <div key={field.key}>
          <label className="block text-xs text-[var(--color-text-muted)] mb-1">
            {field.label} {field.default && <span className="opacity-50">(default: {field.default})</span>}
          </label>
          <input
            type="text"
            value={values[field.key] || ''}
            onChange={(e) => update(field.key, e.target.value)}
            placeholder={field.default}
            className="w-full px-3 py-1.5 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]"
          />
        </div>
      ))}
      <Button variant="secondary" onClick={() => onSave(values)} className="mt-2">
        Save Env
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add Dev Form
// ---------------------------------------------------------------------------

function AddDevForm({
  onSubmit,
  onCancel,
  isLoading,
}: {
  onSubmit: (entry: DevCreateRequest) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [connectionType, setConnectionType] = useState<'stdio' | 'sse' | 'http'>('stdio');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const config: Record<string, unknown> = connectionType === 'stdio'
      ? { command, args: args.split(' ').filter(Boolean) }
      : { url };
    onSubmit({
      id: name.toLowerCase().replace(/\s+/g, '-'),
      name,
      connectionType,
      config,
      description: description || undefined,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <form
        onSubmit={handleSubmit}
        className="bg-[var(--color-card)] rounded-xl p-6 w-full max-w-md space-y-4 border border-[var(--color-border)]"
      >
        <h3 className="text-lg font-semibold text-[var(--color-text)]">{t('mcp.addMcp')}</h3>
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">{t('mcp.form.name')}</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} required
            className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
        </div>
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">{t('mcp.form.connectionType')}</label>
          <div className="flex gap-4">
            {(['stdio', 'sse', 'http'] as const).map((type) => (
              <label key={type} className="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="connType" value={type} checked={connectionType === type}
                  onChange={() => setConnectionType(type)} className="w-4 h-4" />
                <span className="text-sm text-[var(--color-text)] uppercase">{type}</span>
              </label>
            ))}
          </div>
        </div>
        {connectionType === 'stdio' ? (
          <>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">{t('mcp.form.command')}</label>
              <input type="text" value={command} onChange={(e) => setCommand(e.target.value)} required
                className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
            </div>
            <div>
              <label className="block text-sm text-[var(--color-text-muted)] mb-1">{t('mcp.form.args')}</label>
              <input type="text" value={args} onChange={(e) => setArgs(e.target.value)}
                className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
            </div>
          </>
        ) : (
          <div>
            <label className="block text-sm text-[var(--color-text-muted)] mb-1">{t('mcp.form.url')}</label>
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} required
              className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
          </div>
        )}
        <div>
          <label className="block text-sm text-[var(--color-text-muted)] mb-1">Description</label>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full px-3 py-2 rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
        </div>
        <div className="flex gap-3 pt-2">
          <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
          <Button type="submit" className="flex-1" isLoading={isLoading}>Add MCP</Button>
        </div>
      </form>
    </div>
  );
}
