/**
 * AI & Models settings tab.
 *
 * Auth method selection + region + verify + model list + default model.
 * Uses shared AuthConfigPanel for the auth section.
 */
import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { settingsService } from '../../services/settings';
import type { BedrockModel } from '../../services/settings';
import { Dropdown } from '../common';
import AuthConfigPanel from './AuthConfigPanel';

export default function AIModelsTab() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  // Auto-clear success messages after 3 seconds
  useEffect(() => {
    if (message?.type === 'success') {
      const timer = setTimeout(() => setMessage(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [message]);

  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>('');
  const [newModelId, setNewModelId] = useState('');
  const [thinkingMode, setThinkingMode] = useState<string>('adaptive');
  const [thinkingEffort, setThinkingEffort] = useState<string>('high');
  // Full current short→bedrock-id map — kept so a Bedrock-discovered model is
  // MERGED into it (never a partial PUT that would clobber existing entries).
  const [bedrockModelMap, setBedrockModelMap] = useState<Record<string, string>>({});
  const [discovered, setDiscovered] = useState<BedrockModel[] | null>(null);
  const [discovering, setDiscovering] = useState(false);

  const modelOptions = useMemo(() => availableModels.map(id => ({
    id,
    name: id.split(/[-.]/).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '),
    description: id,
  })), [availableModels]);

  useEffect(() => {
    settingsService.getAPIConfiguration()
      .then((config) => {
        setAvailableModels(config.availableModels || []);
        setDefaultModel(config.defaultModel || '');
        setThinkingMode(config.thinkingMode || 'adaptive');
        setThinkingEffort(config.thinkingEffort || 'high');
        setBedrockModelMap(config.bedrockModelMap || {});
      })
      .catch(() => {});
  }, []);

  const handleRefreshFromBedrock = async () => {
    setDiscovering(true);
    try {
      const result = await settingsService.getBedrockModels();
      if (!result.available) {
        // Fail-open: keep the current list, surface why. Never blank the picker.
        setDiscovered(null);
        setMessage({ type: 'error', text: `Bedrock unavailable: ${result.error ?? 'unknown error'}` });
        return;
      }
      setDiscovered(result.models);
      const newCount = result.models.filter((m) => m.isNew).length;
      setMessage({
        type: 'success',
        text: newCount > 0 ? `Found ${newCount} new model(s) on Bedrock` : 'No new models — all up to date',
      });
    } catch (error) {
      setMessage({ type: 'error', text: `Bedrock discovery failed: ${error}` });
    } finally {
      setDiscovering(false);
    }
  };

  // Add a discovered model: append short_name to available_models AND MERGE its
  // bedrock_id into the full map (A1 — a partial map PUT would clobber the other
  // entries because the backend does a top-level dict replace). default_model
  // is NOT changed — the user must switch it explicitly.
  const handleAddDiscovered = async (model: BedrockModel) => {
    if (availableModels.includes(model.shortName)) return;
    // Re-fetch the CURRENT config before merging — never merge onto possibly-stale
    // component state. A partial bedrock_model_map PUT does a top-level replace on
    // the backend, so merging onto a not-yet-loaded ({}) map would clobber the
    // existing entries. Re-reading guarantees the merge base is the live full map.
    let baseMap = bedrockModelMap;
    let baseAvailable = availableModels;
    let baseDefault = defaultModel;
    try {
      const fresh = await settingsService.getAPIConfiguration();
      baseMap = fresh.bedrockModelMap || {};
      baseAvailable = fresh.availableModels || [];
      baseDefault = fresh.defaultModel || '';
    } catch {
      // Fall back to component state if the re-fetch fails.
    }
    if (baseAvailable.includes(model.shortName)) return;
    const mergedMap = { ...baseMap, [model.shortName]: model.bedrockId };
    try {
      const config = await settingsService.updateAPIConfiguration({
        available_models: [...baseAvailable, model.shortName],
        bedrock_model_map: mergedMap,
        // Omit default_model when unknown — the backend preserves the current
        // default on an unchanged list; sending '' would 400 (not in list).
        ...(baseDefault ? { default_model: baseDefault } : {}),
      });
      setAvailableModels(config.availableModels || []);
      setDefaultModel(config.defaultModel || '');
      setBedrockModelMap(config.bedrockModelMap || mergedMap);
      setDiscovered((prev) =>
        prev ? prev.map((m) => (m.shortName === model.shortName ? { ...m, isNew: false } : m)) : prev,
      );
      queryClient.invalidateQueries({ queryKey: ['apiConfig'] });
      setMessage({ type: 'success', text: `Added ${model.shortName}` });
    } catch (error) {
      setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
    }
  };

  const saveModelConfig = async (models: string[], defaultMdl: string) => {
    try {
      const config = await settingsService.updateAPIConfiguration({
        available_models: models,
        default_model: defaultMdl,
      });
      setAvailableModels(config.availableModels || []);
      setDefaultModel(config.defaultModel || '');
      queryClient.invalidateQueries({ queryKey: ['apiConfig'] });
      setMessage({ type: 'success', text: t('common.message.saveSuccess') });
    } catch (error) {
      setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
    }
  };

  const handleAddModel = async () => {
    const trimmed = newModelId.trim();
    if (!trimmed || availableModels.includes(trimmed)) return;
    setNewModelId('');
    await saveModelConfig([...availableModels, trimmed], defaultModel);
  };

  const handleDeleteModel = async (modelId: string) => {
    if (availableModels.length <= 1) {
      setMessage({ type: 'error', text: 'Cannot remove the last model.' });
      return;
    }
    if (modelId === defaultModel) {
      setMessage({ type: 'error', text: 'Cannot remove the default model. Change the default first.' });
      return;
    }
    await saveModelConfig(availableModels.filter(m => m !== modelId), defaultModel);
  };

  return (
    <div className="space-y-6">
      {message && (
        <div className={`p-3 rounded-lg text-sm ${
          message.type === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
        }`}>{message.text}</div>
      )}

      {/* Authentication */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">AWS Account</h2>
        <AuthConfigPanel mode="settings" />
      </section>

      {/* Thinking */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Thinking</h2>
        <div className="space-y-4">
          <Dropdown
            label="Thinking Mode"
            options={[
              { id: 'adaptive', name: 'Adaptive', description: 'Model decides when to think deeply' },
              { id: 'enabled', name: 'Enabled', description: 'Always use extended thinking' },
              { id: 'disabled', name: 'Disabled', description: 'Never use extended thinking' },
            ]}
            selectedId={thinkingMode}
            onChange={async (id) => {
              try {
                const config = await settingsService.updateAPIConfiguration({ thinking_mode: id });
                setThinkingMode(config.thinkingMode || 'adaptive');
                setMessage({ type: 'success', text: t('common.message.saveSuccess') });
              } catch (error) {
                setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
              }
            }}
          />
          <Dropdown
            label="Thinking Effort"
            options={[
              { id: 'low', name: 'Low', description: 'Minimal thinking — fastest responses' },
              { id: 'medium', name: 'Medium', description: 'Balanced speed and depth' },
              { id: 'high', name: 'High', description: 'Deep thinking (default)' },
              { id: 'xhigh', name: 'Extra High', description: 'Very thorough analysis' },
              { id: 'max', name: 'Maximum', description: 'Deepest reasoning — slowest' },
            ]}
            selectedId={thinkingEffort}
            onChange={async (id) => {
              try {
                const config = await settingsService.updateAPIConfiguration({ thinking_effort: id });
                setThinkingEffort(config.thinkingEffort || 'high');
                setMessage({ type: 'success', text: t('common.message.saveSuccess') });
              } catch (error) {
                setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
              }
            }}
          />
        </div>
      </section>

      {/* Models */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.modelConfig.title')}</h2>
        <div className="space-y-4">
          <Dropdown
            label={t('settings.modelConfig.defaultModel')}
            options={modelOptions}
            selectedId={defaultModel}
            onChange={(id) => saveModelConfig(availableModels, id)}
            placeholder={t('common.placeholder.select')}
          />

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm text-[var(--color-text-muted)]">
                {t('settings.modelConfig.availableModels')}
              </label>
              <button
                onClick={handleRefreshFromBedrock}
                disabled={discovering}
                className="flex items-center gap-1 px-3 py-1.5 text-sm bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] hover:border-[var(--color-primary)] disabled:opacity-50"
                title="List Claude models available on your Bedrock account"
              >
                <span className={`material-symbols-outlined text-sm ${discovering ? 'animate-spin' : ''}`}>
                  {discovering ? 'progress_activity' : 'cloud_sync'}
                </span>
                {discovering ? 'Refreshing…' : 'Refresh from Bedrock'}
              </button>
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={newModelId}
                onChange={(e) => setNewModelId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddModel()}
                placeholder={t('settings.modelConfig.addModelPlaceholder')}
                className="flex-1 px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
              />
              <button
                onClick={handleAddModel}
                className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80"
              >
                {t('settings.modelConfig.addModel')}
              </button>
            </div>
          </div>

          {/* Discovered-from-Bedrock list — appears after a refresh */}
          {discovered && discovered.length > 0 && (
            <div className="border border-[var(--color-border)] rounded-lg p-3 space-y-2">
              <div className="text-xs text-[var(--color-text-muted)] mb-1">
                Available on your Bedrock account:
              </div>
              {discovered.map((m) => {
                const already = availableModels.includes(m.shortName);
                return (
                  <div key={m.bedrockId} className="flex items-center justify-between p-2 bg-[var(--color-bg)] rounded-lg">
                    <span className="flex items-center gap-2 min-w-0">
                      <span className="text-[var(--color-text)] font-mono text-sm truncate">{m.shortName}</span>
                      {m.isNew && !already && (
                        <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-[var(--color-primary)]/20 text-[var(--color-primary)] rounded">NEW</span>
                      )}
                    </span>
                    {already ? (
                      <span className="text-[var(--color-text-muted)] text-xs flex items-center gap-1">
                        <span className="material-symbols-outlined text-sm">check</span>added
                      </span>
                    ) : (
                      <button
                        onClick={() => handleAddDiscovered(m)}
                        className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80"
                      >
                        {t('settings.modelConfig.addModel')}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="space-y-2">
            {availableModels.map((model) => (
              <div key={model} className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
                <span className="text-[var(--color-text)] font-mono text-sm">{model}</span>
                {model === defaultModel ? (
                  <span className="flex items-center gap-1 text-amber-400 text-sm">
                    <span className="material-symbols-outlined text-sm">star</span>
                    {t('settings.modelConfig.defaultLabel')}
                  </span>
                ) : (
                  <button
                    onClick={() => handleDeleteModel(model)}
                    className="text-[var(--color-text-muted)] hover:text-red-400 transition-colors"
                  >
                    <span className="material-symbols-outlined text-sm">delete</span>
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
