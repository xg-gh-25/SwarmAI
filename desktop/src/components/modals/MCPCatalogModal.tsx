/**
 * MCPCatalogModal — Browse and install optional MCP integrations.
 *
 * Two-screen flow:
 *   1. Catalog grid — shows all available integrations with install status
 *   2. Setup form  — preset selector + env var fields + install button
 *
 * Talks to:
 *   GET  /mcp/catalog         — list catalog entries (annotated with `installed`)
 *   POST /mcp/catalog/install — install with user-provided env vars
 */

import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import Modal from '../common/Modal';
import Button from '../common/Button';
import { mcpService } from '../../services/mcp';
import type { MCPCatalogEntry, MCPCatalogPreset, MCPServer } from '../../types';

// Category metadata for display
const CATEGORY_META: Record<string, { icon: string; label: string }> = {
  communication: { icon: 'chat', label: 'Communication' },
  development: { icon: 'code', label: 'Development' },
  productivity: { icon: 'task_alt', label: 'Productivity' },
  data: { icon: 'database', label: 'Data' },
};

interface MCPCatalogModalProps {
  isOpen: boolean;
  onClose: () => void;
  onInstalled: (server: MCPServer) => void;
}

export default function MCPCatalogModal({
  isOpen,
  onClose,
  onInstalled,
}: MCPCatalogModalProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<MCPCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<MCPCatalogEntry | null>(null);

  // Fetch catalog when modal opens
  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setSelected(null);
    mcpService
      .getCatalog()
      .then(setCatalog)
      .catch((err) => console.error('Failed to load MCP catalog:', err))
      .finally(() => setLoading(false));
  }, [isOpen]);

  const handleInstalled = (server: MCPServer, catalogId: string) => {
    // Mark as installed in local state
    setCatalog((prev) =>
      prev.map((e) => (e.id === catalogId ? { ...e, installed: true } : e))
    );
    onInstalled(server);
    setSelected(null);
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={selected ? selected.name : t('mcp.catalog.title')}
      size="xl"
    >
      {selected ? (
        <SetupForm
          entry={selected}
          onBack={() => setSelected(null)}
          onInstalled={handleInstalled}
        />
      ) : (
        <CatalogGrid
          catalog={catalog}
          loading={loading}
          onSelect={setSelected}
        />
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Catalog Grid (Screen 1)
// ---------------------------------------------------------------------------

function CatalogGrid({
  catalog,
  loading,
  onSelect,
}: {
  catalog: MCPCatalogEntry[];
  loading: boolean;
  onSelect: (entry: MCPCatalogEntry) => void;
}) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        {t('common.status.loading')}
      </div>
    );
  }

  if (catalog.length === 0) {
    return (
      <div className="text-center py-12 text-[var(--color-text-muted)]">
        <span className="material-symbols-outlined text-4xl block mb-2">
          extension_off
        </span>
        {t('mcp.catalog.empty')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        {t('mcp.catalog.subtitle')}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {catalog.map((entry) => {
          const catMeta = CATEGORY_META[entry.category] || {
            icon: 'extension',
            label: entry.category,
          };
          return (
            <button
              key={entry.id}
              onClick={() => !entry.installed && onSelect(entry)}
              disabled={entry.installed}
              className={clsx(
                'text-left p-4 rounded-xl border transition-all',
                entry.installed
                  ? 'border-[var(--color-border)] bg-[var(--color-card)] opacity-60 cursor-default'
                  : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-primary/50 hover:shadow-md cursor-pointer'
              )}
            >
              <div className="flex items-start gap-3">
                <span className="material-symbols-outlined text-2xl text-primary mt-0.5">
                  {catMeta.icon}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-[var(--color-text)] truncate">
                      {entry.name}
                    </span>
                    {entry.installed && (
                      <span className="shrink-0 flex items-center gap-1 text-xs text-green-500 font-medium">
                        <span className="material-symbols-outlined text-sm">
                          check_circle
                        </span>
                        {t('mcp.catalog.installed')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)] mt-1 line-clamp-2">
                    {entry.description}
                  </p>
                  <div className="flex items-center gap-2 mt-2">
                    <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary">
                      {catMeta.label}
                    </span>
                    {entry.runtime && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-hover)] text-[var(--color-text-muted)]">
                        {entry.runtime}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Setup Form (Screen 2)
// ---------------------------------------------------------------------------

function SetupForm({
  entry,
  onBack,
  onInstalled,
}: {
  entry: MCPCatalogEntry;
  onBack: () => void;
  onInstalled: (server: MCPServer, catalogId: string) => void;
}) {
  const { t } = useTranslation();
  const presetKeys = useMemo(() => Object.keys(entry.presets), [entry]);
  const hasPresets = presetKeys.length > 0;

  const [selectedPreset, setSelectedPreset] = useState<string>(
    hasPresets ? presetKeys[0] : ''
  );
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Initialize env values from defaults and preset
  useEffect(() => {
    const defaults: Record<string, string> = {};
    // Set defaults from optional_env
    for (const field of entry.optional_env) {
      if (field.default) defaults[field.key] = field.default;
    }
    // Apply preset env on top
    if (selectedPreset && entry.presets[selectedPreset]) {
      Object.assign(defaults, entry.presets[selectedPreset].env);
    }
    setEnvValues((prev) => ({ ...defaults, ...stripPresetKeys(prev) }));
  }, [selectedPreset, entry]);

  // Remove preset-owned keys from user values so preset switch is clean
  function stripPresetKeys(vals: Record<string, string>): Record<string, string> {
    const presetOwnedKeys = new Set<string>();
    for (const preset of Object.values(entry.presets)) {
      for (const k of Object.keys(preset.env)) presetOwnedKeys.add(k);
    }
    const result: Record<string, string> = {};
    for (const [k, v] of Object.entries(vals)) {
      if (!presetOwnedKeys.has(k)) result[k] = v;
    }
    return result;
  }

  const currentPreset: MCPCatalogPreset | null =
    selectedPreset ? entry.presets[selectedPreset] ?? null : null;

  const updateEnv = (key: string, value: string) => {
    setEnvValues((prev) => ({ ...prev, [key]: value }));
  };

  // Validation: all required fields must be filled (unless preset provides them)
  const missingRequired = entry.required_env.filter((field) => {
    const val = envValues[field.key]?.trim();
    return !val;
  });

  const handleInstall = async () => {
    setInstalling(true);
    setError(null);
    try {
      // Build final env: merge defaults, preset, and user input
      const finalEnv: Record<string, string> = {};
      for (const [k, v] of Object.entries(envValues)) {
        if (v.trim()) finalEnv[k] = v.trim();
      }
      const server = await mcpService.installFromCatalog({
        catalog_id: entry.id,
        env: finalEnv,
      });
      setSuccess(true);
      setTimeout(() => onInstalled(server, entry.id), 800);
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : t('mcp.catalog.installFailed');
      setError(msg);
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Back link */}
      <button
        onClick={onBack}
        className="flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        <span className="material-symbols-outlined text-lg">arrow_back</span>
        {t('mcp.catalog.back')}
      </button>

      {/* Description */}
      <p className="text-sm text-[var(--color-text-muted)]">
        {entry.description}
      </p>

      {/* Preset selector */}
      {hasPresets && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('mcp.catalog.selectPreset')}
          </label>
          <div className="flex flex-wrap gap-2">
            {presetKeys.map((key) => {
              const preset = entry.presets[key];
              return (
                <button
                  key={key}
                  onClick={() => setSelectedPreset(key)}
                  className={clsx(
                    'px-3 py-1.5 text-sm rounded-lg border transition-colors',
                    selectedPreset === key
                      ? 'border-primary bg-primary/10 text-primary font-medium'
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-primary/40'
                  )}
                >
                  {preset.label}
                </button>
              );
            })}
            <button
              onClick={() => setSelectedPreset('')}
              className={clsx(
                'px-3 py-1.5 text-sm rounded-lg border transition-colors',
                !selectedPreset
                  ? 'border-primary bg-primary/10 text-primary font-medium'
                  : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-primary/40'
              )}
            >
              {t('mcp.catalog.presetNone')}
            </button>
          </div>

          {/* Setup hint for selected preset */}
          {currentPreset?.setup_hint && (
            <div className="mt-3 p-3 rounded-lg bg-blue-500/10 border border-blue-500/20">
              <div className="flex items-start gap-2">
                <span className="material-symbols-outlined text-blue-400 text-lg mt-0.5">
                  info
                </span>
                <p className="text-xs text-blue-300 leading-relaxed">
                  {currentPreset.setup_hint}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Required env fields */}
      {entry.required_env.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('mcp.catalog.requiredFields')}
          </label>
          <div className="space-y-3">
            {entry.required_env.map((field) => {
              // If preset provides this field, show as pre-filled and disabled
              const presetProvides =
                currentPreset && field.key in currentPreset.env;
              return (
                <div key={field.key}>
                  <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                    {field.label}
                  </label>
                  <input
                    type={field.secret ? 'password' : 'text'}
                    value={envValues[field.key] || ''}
                    onChange={(e) => updateEnv(field.key, e.target.value)}
                    placeholder={field.placeholder || ''}
                    disabled={!!presetProvides}
                    className={clsx(
                      'w-full px-3 py-2 text-sm rounded-lg border bg-[var(--color-bg)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/50 focus:outline-none focus:border-primary',
                      presetProvides
                        ? 'border-green-500/30 opacity-70'
                        : 'border-[var(--color-border)]'
                    )}
                  />
                  {presetProvides && (
                    <span className="text-xs text-green-500 mt-0.5 block">
                      Set by preset
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Optional env fields */}
      {entry.optional_env.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('mcp.catalog.optionalFields')}
          </label>
          <div className="space-y-3">
            {entry.optional_env.map((field) => (
              <div key={field.key}>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                  {field.label}
                  {field.default && (
                    <span className="ml-1 text-[var(--color-text-muted)]/50">
                      (default: {field.default})
                    </span>
                  )}
                </label>
                <input
                  type={field.secret ? 'password' : 'text'}
                  value={envValues[field.key] || ''}
                  onChange={(e) => updateEnv(field.key, e.target.value)}
                  placeholder={field.placeholder || field.default || ''}
                  className="w-full px-3 py-2 text-sm rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/50 focus:outline-none focus:border-primary"
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Setup docs link */}
      {entry.setup_docs_url && (
        <a
          href={entry.setup_docs_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
        >
          <span className="material-symbols-outlined text-lg">open_in_new</span>
          {t('mcp.catalog.setupDocs')}
        </a>
      )}

      {/* Error message */}
      {error && (
        <div className="p-3 rounded-lg bg-status-error/10 border border-status-error/20">
          <p className="text-sm text-status-error">{error}</p>
        </div>
      )}

      {/* Success message */}
      {success && (
        <div className="p-3 rounded-lg bg-green-500/10 border border-green-500/20">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-green-500">
              check_circle
            </span>
            <p className="text-sm text-green-500">
              {t('mcp.catalog.installSuccess')}
            </p>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3 pt-2">
        <Button variant="secondary" onClick={onBack}>
          {t('common.button.cancel')}
        </Button>
        <Button
          className="flex-1"
          onClick={handleInstall}
          isLoading={installing}
          disabled={missingRequired.length > 0 || success}
        >
          {success
            ? t('mcp.catalog.installed')
            : t('mcp.catalog.configure')}
        </Button>
      </div>
    </div>
  );
}
