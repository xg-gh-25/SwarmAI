/**
 * Settings → LLM Models (design docs/design/2026-11-01-settings-account-merge.md §3):
 * where the model is called from (ModelProvider), thinking, the model list and the
 * default model. AWS sign-in is on the Swarm AWS Account page. Outside the desktop app
 * (a Hive's web UI) the old AuthConfigPanel stays here: on a Hive it is the only place
 * to see the instance role, change the region or switch to a Bedrock key.
 */
import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { settingsService } from '../../services/settings';
import type { BedrockModel, SettingsConfig } from '../../services/settings';
import { Dropdown } from '../common';
import AuthConfigPanel from './AuthConfigPanel';
import ModelProvider from './ModelProvider';
import { isDesktop } from '../../services/tauri';

const THINKING_MODE_OPTIONS = [
  { id: 'adaptive', name: 'Adaptive', description: 'Model decides when to think deeply' },
  { id: 'enabled', name: 'Enabled', description: 'Always use extended thinking' },
  { id: 'disabled', name: 'Disabled', description: 'Never use extended thinking' },
];

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
  // What the DEFAULT model accepts + the mode it actually runs with — computed by
  // the backend (model_registry, the one rule). Null when an older backend omits
  // them: then all three modes are offered and the stored mode is shown.
  const [supportedThinkingModes, setSupportedThinkingModes] = useState<string[] | null>(null);
  const [effectiveThinkingMode, setEffectiveThinkingMode] = useState<string | null>(null);
  const [thinkingEffort, setThinkingEffort] = useState<string>('high');
  // Full current short→bedrock-id map — kept so a Bedrock-discovered model is
  // MERGED into it (never a partial PUT that would clobber existing entries).
  const [bedrockModelMap, setBedrockModelMap] = useState<Record<string, string>>({});
  const [discovered, setDiscovered] = useState<BedrockModel[] | null>(null);
  const [discovering, setDiscovering] = useState(false);

  // Every settings response carries the thinking fields for the CURRENT default
  // model, so apply them after ANY load/PUT — a default-model change can change
  // which thinking modes are valid.
  const applyThinking = (config: SettingsConfig) => {
    setThinkingMode(config.thinkingMode || 'adaptive');
    setSupportedThinkingModes(config.supportedThinkingModes ?? null);
    setEffectiveThinkingMode(config.effectiveThinkingMode ?? null);
  };

  const supportedOptions = supportedThinkingModes
    ? THINKING_MODE_OPTIONS.filter((o) => supportedThinkingModes.includes(o.id))
    : [];
  // Never render an empty picker (an unexpected payload falls back to all modes).
  const thinkingModeOptions = supportedOptions.length ? supportedOptions : THINKING_MODE_OPTIONS;
  const shownThinkingMode = effectiveThinkingMode ?? thinkingMode;
  const storedModeIsKnown = THINKING_MODE_OPTIONS.some((o) => o.id === thinkingMode);
  const storedModeName = THINKING_MODE_OPTIONS.find((o) => o.id === thinkingMode)?.name ?? thinkingMode;
  const shownModeName = THINKING_MODE_OPTIONS.find((o) => o.id === shownThinkingMode)?.name ?? shownThinkingMode;

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
        applyThinking(config);
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
      applyThinking(config);
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
      applyThinking(config);
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

      {/* Where the model is called from (sign-in itself: Swarm AWS Account) */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.modelProvider.heading')}</h2>
        {isDesktop() ? <ModelProvider /> : <AuthConfigPanel mode="settings" />}
      </section>

      {/* Thinking */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('ui.AIModelsTab.thinking')}</h2>
        <div className="space-y-4">
          <Dropdown
            label={t('ui.AIModelsTab.thinkingMode')}
            options={thinkingModeOptions}
            selectedId={shownThinkingMode}
            onChange={async (id) => {
              try {
                const config = await settingsService.updateAPIConfiguration({ thinking_mode: id });
                applyThinking(config);
                setMessage({ type: 'success', text: t('common.message.saveSuccess') });
              } catch (error) {
                setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
              }
            }}
          />
          {storedModeIsKnown && effectiveThinkingMode && effectiveThinkingMode !== thinkingMode && (
            <p data-testid="thinking-mode-not-in-effect" className="text-xs text-[var(--color-text-muted)] -mt-2">
              {t('ui.AIModelsTab.modeNotInEffect', { stored: storedModeName, shown: shownModeName })}
            </p>
          )}
          <Dropdown
            label={t('ui.AIModelsTab.thinkingEffort')}
            disabled={shownThinkingMode === 'disabled'}
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
                applyThinking(config);
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
                className="btn-md btn-secondary disabled:opacity-50"
                title={t('ui.AIModelsTab.refreshTitle')}
              >
                <span className={`material-symbols-outlined icon-sm ${discovering ? 'animate-spin' : ''}`}>
                  {discovering ? 'progress_activity' : 'cloud_sync'}
                </span>
                {discovering ? t('ui.AIModelsTab.refreshing') : t('ui.AIModelsTab.refreshFromBedrock')}
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
                className="btn-lg btn-primary"
              >
                {t('settings.modelConfig.addModel')}
              </button>
            </div>
          </div>

          {/* Discovered-from-Bedrock list — appears after a refresh */}
          {discovered && discovered.length > 0 && (
            <div className="border border-[var(--color-border)] rounded-lg p-3 space-y-2">
              <div className="text-xs text-[var(--color-text-muted)] mb-1">
                {t('ui.AIModelsTab.availableOnBedrock')}
              </div>
              {discovered.map((m) => {
                const already = availableModels.includes(m.shortName);
                return (
                  <div key={m.bedrockId} className="flex items-center justify-between p-2 bg-[var(--color-bg)] rounded-lg">
                    <span className="flex items-center gap-2 min-w-0">
                      <span className="text-[var(--color-text)] font-mono text-sm truncate">{m.shortName}</span>
                      {m.isNew && !already && (
                        <span className="px-1.5 py-0.5 text-[11px] font-semibold bg-[var(--color-primary)]/20 text-[var(--color-primary)] rounded">{t('ui.AIModelsTab.newBadge')}</span>
                      )}
                    </span>
                    {already ? (
                      <span className="text-[var(--color-text-muted)] text-xs flex items-center gap-1">
                        <span className="material-symbols-outlined icon-xs">check</span>{t('ui.AIModelsTab.added')}
                      </span>
                    ) : (
                      <button
                        onClick={() => handleAddDiscovered(m)}
                        className="btn-md btn-primary"
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
                    <span className="material-symbols-outlined icon-xs">star</span>
                    {t('settings.modelConfig.defaultLabel')}
                  </span>
                ) : (
                  <button
                    onClick={() => handleDeleteModel(model)}
                    className="text-[var(--color-text-muted)] hover:text-red-400 transition-colors"
                  >
                    <span className="material-symbols-outlined icon-sm">delete</span>
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
