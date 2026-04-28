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
import { Dropdown } from '../common';
import AuthConfigPanel from './AuthConfigPanel';

export default function AIModelsTab() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>('');
  const [newModelId, setNewModelId] = useState('');

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
      })
      .catch(() => {});
  }, []);

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
    if (modelId === defaultModel || availableModels.length <= 1) return;
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
            <label className="block text-sm text-[var(--color-text-muted)] mb-2">
              {t('settings.modelConfig.availableModels')}
            </label>
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
