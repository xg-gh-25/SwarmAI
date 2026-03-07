import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useTheme } from '../contexts/ThemeContext';
import { getVersion } from '@tauri-apps/api/app';
import { tauriService, BackendStatus, getBackendPort, setBackendPort } from '../services/tauri';
import { settingsService, APIConfigurationResponse } from '../services/settings';
import {
  checkForUpdates,
  downloadAndInstallUpdate,
  restartApp,
  formatBytes,
  UpdateProgress,
} from '../services/updater';
import { Update } from '@tauri-apps/plugin-updater';
import { Dropdown } from '../components/common';
import { evolutionService, type EvolutionConfig } from '../services/evolution';

// Check if running in development mode
const isDev = import.meta.env.DEV;

// Detect platform
function getPlatformInfo(): { platform: string; dataDir: string; skillsDir: string; logsDir: string } {
  const userAgent = navigator.userAgent.toLowerCase();
  
  // All platforms use the same path now
  const platform = userAgent.includes('win') ? 'Windows' : 
                   userAgent.includes('mac') ? 'macOS' : 'Linux';
  
  return {
    platform,
    dataDir: '~/.swarm-ai/',
    skillsDir: '~/.swarm-ai/skills/',
    logsDir: '~/.swarm-ai/logs/'
  };
}

const platformInfo = getPlatformInfo();

// AWS Region options for Bedrock
const AWS_REGION_OPTIONS = [
  { id: 'us-east-1', name: 'US East (N. Virginia)', description: 'us-east-1' },
  { id: 'us-west-2', name: 'US West (Oregon)', description: 'us-west-2' },
  { id: 'eu-west-1', name: 'EU (Ireland)', description: 'eu-west-1' },
  { id: 'eu-central-1', name: 'EU (Frankfurt)', description: 'eu-central-1' },
  { id: 'ap-northeast-1', name: 'Asia Pacific (Tokyo)', description: 'ap-northeast-1' },
  { id: 'ap-southeast-1', name: 'Asia Pacific (Singapore)', description: 'ap-southeast-1' },
  { id: 'ap-southeast-2', name: 'Asia Pacific (Sydney)', description: 'ap-southeast-2' },
];

export default function SettingsPage() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();
  const queryClient = useQueryClient();

  const handleLanguageChange = (lang: 'zh' | 'en') => {
    i18n.changeLanguage(lang);
    localStorage.setItem('language', lang);
  };

  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [apiConfig, setApiConfig] = useState<APIConfigurationResponse | null>(null);

  // Form fields
  const [baseUrl, setBaseUrl] = useState('');
  const [useBedrock, setUseBedrock] = useState(false);
  const [awsRegion, setAwsRegion] = useState('us-east-1');

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Model configuration
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>('');
  const [newModelId, setNewModelId] = useState('');

  // Convert model IDs to dropdown options
  const modelOptions = useMemo(() => availableModels.map(id => ({
    id,
    name: id.split(/[-.]/).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '),
    description: id,
  })), [availableModels]);

  // System dependencies
  const [nodejsVersion, setNodejsVersion] = useState<string | null>(null);
  const [pythonVersion, setPythonVersion] = useState<string | null>(null);
  const [gitBashPath, setGitBashPath] = useState<string | null>(null);
  const [checkingDependencies, setCheckingDependencies] = useState(false);

  // App version
  const [appVersion, setAppVersion] = useState<string>('');

  // Update state
  const [updateState, setUpdateState] = useState<'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error'>('idle');
  const [availableUpdate, setAvailableUpdate] = useState<Update | null>(null);
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  // Evolution config state
  const [evolutionConfig, setEvolutionConfig] = useState<EvolutionConfig | null>(null);
  const [savingEvolution, setSavingEvolution] = useState(false);

  useEffect(() => {
    // Load status first (which syncs the port), then load API config
    const init = async () => {
      await loadStatus();
      await loadAPIConfig();
      await checkSystemDependencies();
      await loadEvolutionConfig();

      // Get app version from Tauri (only in production)
      if (!isDev) {
        try {
          const version = await getVersion();
          setAppVersion(version);
        } catch (error) {
          console.error('Failed to get app version:', error);
          setAppVersion('unknown');
        }
      } else {
        setAppVersion('dev');
      }
    };
    init();
  }, []);

  const loadStatus = async (retryCount = 0) => {
    const MAX_RETRIES = 8;
    const RETRY_DELAY = 1500; // 1.5 seconds

    try {
      if (isDev) {
        // In dev mode, check if manual backend is running by pinging health endpoint
        const port = getBackendPort();
        try {
          const response = await fetch(`http://localhost:${port}/health`, {
            method: 'GET',
            signal: AbortSignal.timeout(2000)
          });
          setBackendStatus({ running: response.ok, port });
        } catch {
          setBackendStatus({ running: false, port });
        }
      } else {
        // In production, get port from Tauri and verify backend is actually responding
        const backend = await tauriService.getBackendStatus();
        const port = backend.port;
        let running = false;

        // Actually ping the backend to verify it's running
        try {
          const response = await fetch(`http://localhost:${port}/health`, {
            method: 'GET',
            signal: AbortSignal.timeout(2000)
          });
          running = response.ok;
        } catch {
          running = false;
        }

        // If not running, retry after a delay (backend might still be starting)
        if (!running && retryCount < MAX_RETRIES) {
          console.log(`Backend not ready, retrying... (${retryCount + 1}/${MAX_RETRIES})`);
          setBackendStatus({ running: false, port });
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
          return loadStatus(retryCount + 1);
        }

        // Sync the port to the global variable
        if (running) {
          setBackendPort(port);
        }
        setBackendStatus({ running, port });
      }
    } catch (error) {
      console.error('Failed to load status:', error);
    }
  };

  const loadAPIConfig = async () => {
    try {
      const config = await settingsService.getAPIConfiguration();
      setApiConfig(config);
      setBaseUrl(config.anthropicBaseUrl || '');
      setUseBedrock(config.useBedrock);
      setAwsRegion(config.awsRegion);
      // Model configuration
      setAvailableModels(config.availableModels || []);
      setDefaultModel(config.defaultModel || '');
    } catch (error) {
      console.error('Failed to load API config:', error);
    }
  };

  const handleSaveAPIConfig = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const updateData: Record<string, unknown> = {};

      updateData.anthropic_base_url = baseUrl || '';
      updateData.use_bedrock = useBedrock;

      if (useBedrock) {
        updateData.aws_region = awsRegion;
      }

      const config = await settingsService.updateAPIConfiguration(updateData);
      setApiConfig(config);

      setMessage({ type: 'success', text: 'API configuration saved!' });
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to save: ${error}` });
    } finally {
      setSaving(false);
    }
  };

  const handleCheckForUpdates = async () => {
    console.log('[Settings] Starting update check...');
    setUpdateState('checking');
    setUpdateError(null);
    try {
      const update = await checkForUpdates();
      console.log('[Settings] Update check result:', update);
      if (update) {
        setAvailableUpdate(update);
        setUpdateState('available');
      } else {
        setUpdateState('idle');
        setMessage({ type: 'success', text: 'You are using the latest version!' });
      }
    } catch (error) {
      console.error('[Settings] Update check failed:', error);
      setUpdateError(error instanceof Error ? error.message : 'Failed to check for updates');
      setUpdateState('error');
    }
  };

  const handleDownloadUpdate = async () => {
    if (!availableUpdate) return;
    setUpdateState('downloading');
    setUpdateError(null);
    try {
      await downloadAndInstallUpdate(availableUpdate, (progress) => {
        setUpdateProgress(progress);
      });
      setUpdateState('ready');
    } catch (error) {
      console.error('Download failed:', error);
      setUpdateError(error instanceof Error ? error.message : 'Download failed');
      setUpdateState('error');
    }
  };

  const handleRestartApp = async () => {
    try {
      await restartApp();
    } catch (error) {
      console.error('Restart failed:', error);
      setUpdateError(error instanceof Error ? error.message : 'Restart failed');
    }
  };

  // Model configuration helpers
  const saveModelConfig = async (models: string[], defaultMdl: string) => {
    try {
      const config = await settingsService.updateAPIConfiguration({
        available_models: models,
        default_model: defaultMdl,
      });
      setAvailableModels(config.availableModels || []);
      setDefaultModel(config.defaultModel || '');
      // Invalidate cache so AgentFormModal gets updated models
      queryClient.invalidateQueries({ queryKey: ['apiConfig'] });
      setMessage({ type: 'success', text: t('common.message.saveSuccess') });
    } catch (error) {
      setMessage({ type: 'error', text: `${t('common.message.saveFailed')}: ${error}` });
      await loadAPIConfig(); // Reload on failure
    }
  };

  const handleAddModel = async () => {
    const trimmed = newModelId.trim();
    if (!trimmed) return;
    if (availableModels.includes(trimmed)) {
      setMessage({ type: 'error', text: t('settings.modelConfig.duplicateModel') });
      return;
    }
    const newModels = [...availableModels, trimmed];
    setNewModelId('');
    await saveModelConfig(newModels, defaultModel);
  };

  const handleDeleteModel = async (modelId: string) => {
    if (modelId === defaultModel) {
      setMessage({ type: 'error', text: t('settings.modelConfig.cannotDeleteDefault') });
      return;
    }
    if (availableModels.length <= 1) {
      setMessage({ type: 'error', text: t('settings.modelConfig.cannotDeleteLast') });
      return;
    }
    const newModels = availableModels.filter(m => m !== modelId);
    await saveModelConfig(newModels, defaultModel);
  };

  const handleSetDefaultModel = async (modelId: string) => {
    await saveModelConfig(availableModels, modelId);
  };

  const loadEvolutionConfig = async () => {
    try {
      const config = await evolutionService.getConfig();
      setEvolutionConfig(config);
    } catch (error) {
      console.error('Failed to load evolution config:', error);
    }
  };

  const handleEvolutionToggle = async (field: keyof EvolutionConfig, value: boolean) => {
    if (!evolutionConfig) return;
    setSavingEvolution(true);
    try {
      const updated = await evolutionService.updateConfig({ [field]: value });
      setEvolutionConfig(updated);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to save evolution config: ${error}` });
    } finally {
      setSavingEvolution(false);
    }
  };

  const handleEvolutionNumber = async (field: keyof EvolutionConfig, value: number) => {
    if (!evolutionConfig || isNaN(value) || value < 0) return;
    setSavingEvolution(true);
    try {
      const updated = await evolutionService.updateConfig({ [field]: value });
      setEvolutionConfig(updated);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to save evolution config: ${error}` });
    } finally {
      setSavingEvolution(false);
    }
  };

  const checkSystemDependencies = async () => {
    if (isDev) {
      // Skip in dev mode (manual backend doesn't have Tauri commands)
      return;
    }

    setCheckingDependencies(true);
    try {
      // Check Node.js version
      try {
        const nodeVersion = await tauriService.checkNodejsVersion();
        setNodejsVersion(nodeVersion);
      } catch (error) {
        setNodejsVersion('Not installed');
        console.error('Node.js check failed:', error);
      }

      // Check Python version
      try {
        const pyVersion = await tauriService.checkPythonVersion();
        setPythonVersion(pyVersion);
      } catch (error) {
        setPythonVersion('Not installed');
        console.error('Python check failed:', error);
      }

      // Check Git Bash path (Windows only)
      if (platformInfo.platform === 'Windows') {
        try {
          const bashPath = await tauriService.checkGitBashPath();
          setGitBashPath(bashPath);
        } catch (error) {
          setGitBashPath('Not found');
          console.error('Git Bash check failed:', error);
        }
      }
    } finally {
      setCheckingDependencies(false);
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-[var(--color-text)] mb-6">{t('settings.title')}</h1>

      {/* Language Settings */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('settings.language.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">{t('settings.language.description')}</p>
        <div className="flex gap-3">
          <button
            onClick={() => handleLanguageChange('zh')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              i18n.language === 'zh'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {i18n.language === 'zh' && <span className="material-symbols-outlined text-sm">check</span>}
            {t('settings.language.zh')}
          </button>
          <button
            onClick={() => handleLanguageChange('en')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              i18n.language === 'en'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {i18n.language === 'en' && <span className="material-symbols-outlined text-sm">check</span>}
            {t('settings.language.en')}
          </button>
        </div>
      </section>

      {/* Theme Settings */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('settings.theme.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">{t('settings.theme.description')}</p>
        <div className="flex gap-3">
          <button
            onClick={() => setTheme('light')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'light'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'light' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">light_mode</span>
            {t('settings.theme.light')}
          </button>
          <button
            onClick={() => setTheme('dark')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'dark'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'dark' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">dark_mode</span>
            {t('settings.theme.dark')}
          </button>
          <button
            onClick={() => setTheme('system')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              theme === 'system'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
            }`}
          >
            {theme === 'system' && <span className="material-symbols-outlined text-sm">check</span>}
            <span className="material-symbols-outlined text-sm">contrast</span>
            {t('settings.theme.system')}
          </button>
        </div>
      </section>

      {message && (
        <div
          className={`mb-4 p-4 rounded-lg ${
            message.type === 'success' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
          }`}
        >
          {message.text}
        </div>
      )}

      {/* API Configuration */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">API Configuration</h2>
        <div className="space-y-4">
          {/* Use Bedrock Toggle */}
          <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
            <div>
              <label className="text-sm font-medium text-[var(--color-text)]">Use AWS Bedrock</label>
              <p className="text-xs text-[var(--color-text-muted)]">Use AWS Bedrock instead of Anthropic API</p>
            </div>
            <button
              onClick={() => setUseBedrock(!useBedrock)}
              className={`relative w-12 h-6 rounded-full transition-colors ${
                useBedrock ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
              }`}
            >
              <span
                className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  useBedrock ? 'left-7' : 'left-1'
                }`}
              />
            </button>
          </div>

          {!useBedrock && (
            <>
              {/* Custom Base URL */}
              <div>
                <label className="block text-sm text-[var(--color-text-muted)] mb-2">
                  Custom Base URL (Optional)
                </label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.anthropic.com (default)"
                  className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
                />
                <p className="text-xs text-[var(--color-text-muted)] mt-1">
                  For proxies or custom endpoints. Leave empty for default.
                </p>
              </div>

              {/* Anthropic API Key Status */}
              <div className="p-3 bg-[var(--color-bg)] rounded-lg">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--color-text-muted)]">Anthropic API Key</span>
                  {apiConfig?.anthropicApiKeyConfigured ? (
                    <span className="text-green-400 text-sm flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">check_circle</span>
                      Configured (env var)
                    </span>
                  ) : (
                    <span className="text-amber-400 text-sm flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">warning</span>
                      Not configured
                    </span>
                  )}
                </div>
                {!apiConfig?.anthropicApiKeyConfigured && (
                  <div className="mt-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg">
                    <p className="text-xs text-[var(--color-text)] mb-2">
                      Set the <code className="px-1 py-0.5 bg-[var(--color-bg)] rounded text-xs">ANTHROPIC_API_KEY</code> environment variable before launching SwarmAI:
                    </p>
                    <code className="block text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg)] p-2 rounded">
                      export ANTHROPIC_API_KEY=sk-ant-...
                    </code>
                  </div>
                )}
              </div>
            </>
          )}

          {useBedrock && (
            <>
              {/* AWS Credentials Status */}
              <div className="p-3 bg-[var(--color-bg)] rounded-lg">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--color-text-muted)]">AWS Credentials</span>
                  {apiConfig?.awsCredentialsConfigured ? (
                    <span className="text-green-400 text-sm flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">check_circle</span>
                      Configured
                    </span>
                  ) : (
                    <span className="text-amber-400 text-sm flex items-center gap-1">
                      <span className="material-symbols-outlined text-sm">warning</span>
                      Not configured
                    </span>
                  )}
                </div>
                {!apiConfig?.awsCredentialsConfigured && (
                  <div className="mt-2 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg">
                    <p className="text-xs text-[var(--color-text)] mb-2">
                      AWS credentials are resolved from the standard credential chain. Refresh with ADA CLI:
                    </p>
                    <code className="block text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg)] p-2 rounded">
                      ada credentials update --account=ACCOUNT_ID --role=ROLE_NAME --provider=isengard
                    </code>
                    <p className="text-xs text-[var(--color-text-muted)] mt-2">
                      Credentials are read from <code className="px-1 py-0.5 bg-[var(--color-bg)] rounded">~/.aws/credentials</code>, <code className="px-1 py-0.5 bg-[var(--color-bg)] rounded">~/.ada/credentials</code>, or environment variables.
                    </p>
                  </div>
                )}
              </div>

              {/* AWS Region */}
              <Dropdown
                label="AWS Region"
                options={AWS_REGION_OPTIONS}
                selectedId={awsRegion}
                onChange={setAwsRegion}
                placeholder="Select AWS Region..."
              />
            </>
          )}

          {/* Save Button */}
          <button
            onClick={handleSaveAPIConfig}
            disabled={saving}
            className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save API Configuration'}
          </button>
        </div>
      </section>

      {/* Model Configuration */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.modelConfig.title')}</h2>
        <div className="space-y-4">
          {/* Default Model Dropdown */}
          <div>
            <Dropdown
              label={t('settings.modelConfig.defaultModel')}
              options={modelOptions}
              selectedId={defaultModel}
              onChange={handleSetDefaultModel}
              placeholder={t('common.placeholder.select')}
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              {t('settings.modelConfig.defaultModelDesc')}
            </p>
          </div>

          {/* Add Model Input */}
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

          {/* Model List */}
          <div className="space-y-2">
            {availableModels.map((model) => (
              <div
                key={model}
                className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg"
              >
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
                    title={t('common.button.delete')}
                  >
                    <span className="material-symbols-outlined text-sm">delete</span>
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Claude Agent SDK */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Claude Agent SDK</h2>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Status</span>
            <span className="text-green-400">✓ Bundled</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Version</span>
            <span className="text-[var(--color-text)]">0.1.20</span>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-2">
            The Claude Agent SDK includes a bundled Claude Code CLI. No external installation required.
          </p>
        </div>
      </section>

      {/* Self-Evolution */}
      {evolutionConfig && (
        <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
          <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">Self-Evolution</h2>
          <p className="text-sm text-[var(--color-text-muted)] mb-4">
            Controls how the agent autonomously builds new capabilities when it encounters gaps or optimization opportunities.
          </p>
          <div className="space-y-4">
            {/* Master toggle */}
            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Enable Self-Evolution</label>
                <p className="text-xs text-[var(--color-text-muted)]">Master switch for all evolution triggers</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('enabled', !evolutionConfig.enabled)}
                disabled={savingEvolution}
                role="switch"
                aria-checked={evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            {/* Per-trigger toggles */}
            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Proactive Optimization</label>
                <p className="text-xs text-[var(--color-text-muted)]">Detect and act on optimization opportunities</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('proactiveEnabled', !evolutionConfig.proactiveEnabled)}
                disabled={savingEvolution || !evolutionConfig.enabled}
                role="switch"
                aria-checked={evolutionConfig.proactiveEnabled && evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.proactiveEnabled && evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.proactiveEnabled && evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Stuck Detection</label>
                <p className="text-xs text-[var(--color-text-muted)]">Detect loops and switch strategies automatically</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('stuckDetectionEnabled', !evolutionConfig.stuckDetectionEnabled)}
                disabled={savingEvolution || !evolutionConfig.enabled}
                role="switch"
                aria-checked={evolutionConfig.stuckDetectionEnabled && evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.stuckDetectionEnabled && evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.stuckDetectionEnabled && evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            {/* Auto-approve toggles */}
            <div className="pt-2 border-t border-[var(--color-border)]">
              <p className="text-xs text-[var(--color-text-muted)] mb-3">Auto-approve (skip confirmation prompts)</p>
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Auto-approve Skills</label>
                <p className="text-xs text-[var(--color-text-muted)]">Create new skills without asking</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('autoApproveSkills', !evolutionConfig.autoApproveSkills)}
                disabled={savingEvolution || !evolutionConfig.enabled}
                role="switch"
                aria-checked={evolutionConfig.autoApproveSkills && evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.autoApproveSkills && evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.autoApproveSkills && evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Auto-approve Scripts</label>
                <p className="text-xs text-[var(--color-text-muted)]">Create new scripts without asking</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('autoApproveScripts', !evolutionConfig.autoApproveScripts)}
                disabled={savingEvolution || !evolutionConfig.enabled}
                role="switch"
                aria-checked={evolutionConfig.autoApproveScripts && evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.autoApproveScripts && evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.autoApproveScripts && evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Auto-approve Installs</label>
                <p className="text-xs text-[var(--color-text-muted)]">Install packages (pip/npm/brew) without asking</p>
              </div>
              <button
                onClick={() => handleEvolutionToggle('autoApproveInstalls', !evolutionConfig.autoApproveInstalls)}
                disabled={savingEvolution || !evolutionConfig.enabled}
                role="switch"
                aria-checked={evolutionConfig.autoApproveInstalls && evolutionConfig.enabled}
                className={`relative w-12 h-6 rounded-full transition-colors ${
                  evolutionConfig.autoApproveInstalls && evolutionConfig.enabled ? 'bg-[var(--color-primary)]' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  evolutionConfig.autoApproveInstalls && evolutionConfig.enabled ? 'left-7' : 'left-1'
                }`} />
              </button>
            </div>

            {/* Numeric inputs */}
            <div className="pt-2 border-t border-[var(--color-border)]">
              <p className="text-xs text-[var(--color-text-muted)] mb-3">Limits</p>
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Max Retries</label>
                <p className="text-xs text-[var(--color-text-muted)]">Attempts per evolution trigger (1–5)</p>
              </div>
              <input
                type="number"
                min={1}
                max={5}
                value={evolutionConfig.maxRetries}
                onChange={(e) => handleEvolutionNumber('maxRetries', parseInt(e.target.value, 10))}
                disabled={savingEvolution || !evolutionConfig.enabled}
                className="w-16 px-2 py-1 bg-[var(--color-bg)] border border-[var(--color-border)] rounded text-[var(--color-text)] text-sm text-center focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>

            <div className="flex items-center justify-between p-3 bg-[var(--color-bg)] rounded-lg">
              <div>
                <label className="text-sm font-medium text-[var(--color-text)]">Verification Timeout</label>
                <p className="text-xs text-[var(--color-text-muted)]">Seconds to wait for capability verification</p>
              </div>
              <input
                type="number"
                min={30}
                max={600}
                step={30}
                value={evolutionConfig.verificationTimeoutSeconds}
                onChange={(e) => handleEvolutionNumber('verificationTimeoutSeconds', parseInt(e.target.value, 10))}
                disabled={savingEvolution || !evolutionConfig.enabled}
                className="w-20 px-2 py-1 bg-[var(--color-bg)] border border-[var(--color-border)] rounded text-[var(--color-text)] text-sm text-center focus:outline-none focus:border-[var(--color-primary)]"
              />
            </div>
          </div>
        </section>
      )}

      {/* System Dependencies */}
      {!isDev && (
        <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-[var(--color-text)]">System Dependencies</h2>
            <button
              onClick={checkSystemDependencies}
              disabled={checkingDependencies}
              className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:bg-[var(--color-primary)] hover:text-white transition-colors disabled:opacity-50"
            >
              {checkingDependencies ? 'Checking...' : 'Refresh'}
            </button>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Node.js</span>
              {nodejsVersion === null ? (
                <span className="text-[var(--color-text-muted)]">Checking...</span>
              ) : nodejsVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{nodejsVersion}</span>
              )}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Python</span>
              {pythonVersion === null ? (
                <span className="text-[var(--color-text-muted)]">Checking...</span>
              ) : pythonVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{pythonVersion}</span>
              )}
            </div>
            {platformInfo.platform === 'Windows' && (
              <div className="flex items-center justify-between">
                <span className="text-[var(--color-text-muted)]">Git Bash</span>
                {gitBashPath === null ? (
                  <span className="text-[var(--color-text-muted)]">Checking...</span>
                ) : gitBashPath === 'Not found' ? (
                  <span className="text-red-400">✗ Not found</span>
                ) : (
                  <span className="text-green-400 text-xs font-mono truncate max-w-[300px]" title={gitBashPath}>
                    {gitBashPath}
                  </span>
                )}
              </div>
            )}
            <p className="text-xs text-[var(--color-text-muted)] mt-2">
              System-level dependencies detected in PATH. These are not required for the app to run.
            </p>
          </div>
        </section>
      )}

      {/* Git Bash Warning (Windows only) */}
      {!isDev && platformInfo.platform === 'Windows' && gitBashPath === 'Not found' && (
        <section className="mb-8 bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-6">
          <div className="flex items-start gap-3">
            <span className="text-yellow-500 text-xl">⚠</span>
            <div className="flex-1">
              <h3 className="text-yellow-500 font-semibold mb-2">Git Bash Required</h3>
              <p className="text-[var(--color-text)] text-sm mb-3">
                Git Bash is required for Claude Agent SDK to execute shell commands on Windows.
                Please install Git for Windows and configure the environment variable.
              </p>
              <div className="space-y-2 text-sm">
                <div>
                  <span className="text-[var(--color-text-muted)]">1. Download and install Git for Windows:</span>
                  <a
                    href="https://git-scm.com/downloads/win"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 text-[var(--color-primary)] hover:underline"
                  >
                    https://git-scm.com/downloads/win
                  </a>
                </div>
                <div>
                  <span className="text-[var(--color-text-muted)]">2. Set the environment variable:</span>
                  <code className="ml-2 px-2 py-1 bg-[var(--color-bg)] rounded text-xs text-[var(--color-text)]">
                    CLAUDE_CODE_GIT_BASH_PATH
                  </code>
                </div>
                <div className="mt-2 p-3 bg-[var(--color-bg)] rounded-lg">
                  <p className="text-[var(--color-text-muted)] text-xs mb-1">Example (default installation path):</p>
                  <code className="text-[var(--color-text)] text-xs font-mono">
                    CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe
                  </code>
                </div>
                <p className="text-[var(--color-text-muted)] text-xs mt-2">
                  After setting the environment variable, restart the application and click "Refresh" above to verify.
                </p>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Backend Status */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Backend Service</h2>
        {backendStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Status</span>
              <span className={backendStatus.running ? 'text-green-400' : 'text-red-400'}>
                {backendStatus.running ? '● Running' : '○ Stopped'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Port</span>
              <span className="text-[var(--color-text)]">{backendStatus.port}</span>
            </div>
          </div>
        ) : (
          <p className="text-[var(--color-text-muted)]">Loading...</p>
        )}
      </section>

      {/* Storage Info */}
      <section className="mb-8 bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Storage</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Data Directory</span>
            <span className="text-[var(--color-text)] font-mono text-xs">
              {platformInfo.dataDir}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Skills Directory</span>
            <span className="text-[var(--color-text)] font-mono text-xs">
              {platformInfo.skillsDir}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Database</span>
            <span className="text-[var(--color-text)] font-mono text-xs">data.db (SQLite)</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Logs Directory</span>
            <span className="text-[var(--color-text)] font-mono text-xs">
              {platformInfo.logsDir}
            </span>
          </div>
        </div>
      </section>

      {/* About */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">About</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Version</span>
            <span className="text-[var(--color-text)]">{appVersion || 'Loading...'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Platform</span>
            <span className="text-[var(--color-text)]">{platformInfo.platform}</span>
          </div>

          {/* Update Section - only in production */}
          {!isDev && (
            <div className="pt-3 border-t border-[var(--color-border)]">
              {/* Idle state - show check button */}
              {updateState === 'idle' && (
                <button
                  onClick={handleCheckForUpdates}
                  className="w-full px-4 py-2 bg-[var(--color-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors flex items-center justify-center gap-2"
                >
                  <span className="material-symbols-outlined text-lg">update</span>
                  Check for Updates
                </button>
              )}

              {/* Checking state */}
              {updateState === 'checking' && (
                <div className="flex items-center justify-center gap-2 py-2 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined animate-spin text-lg">progress_activity</span>
                  Checking for updates...
                </div>
              )}

              {/* Update available */}
              {updateState === 'available' && availableUpdate && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-400">
                    <span className="material-symbols-outlined">new_releases</span>
                    <span>Version {availableUpdate.version} available!</span>
                  </div>
                  {availableUpdate.body && (
                    <div className="bg-[var(--color-bg)] rounded p-3 text-xs text-[var(--color-text-muted)] max-h-24 overflow-y-auto">
                      {availableUpdate.body}
                    </div>
                  )}
                  <button
                    onClick={handleDownloadUpdate}
                    className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-lg">download</span>
                    Download & Install
                  </button>
                </div>
              )}

              {/* Downloading state */}
              {updateState === 'downloading' && updateProgress && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-[var(--color-text-muted)]">
                    <span>Downloading...</span>
                    <span>
                      {updateProgress.total
                        ? `${formatBytes(updateProgress.downloaded)} / ${formatBytes(updateProgress.total)}`
                        : formatBytes(updateProgress.downloaded)}
                    </span>
                  </div>
                  <div className="h-2 bg-[var(--color-bg)] rounded-full overflow-hidden">
                    <div
                      className="h-full bg-[var(--color-primary)] transition-all duration-300"
                      style={{ width: `${updateProgress.percentage}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Ready to restart */}
              {updateState === 'ready' && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-green-400">
                    <span className="material-symbols-outlined">check_circle</span>
                    <span>Update downloaded! Restart to apply.</span>
                  </div>
                  <button
                    onClick={handleRestartApp}
                    className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-lg">restart_alt</span>
                    Restart Now
                  </button>
                </div>
              )}

              {/* Error state */}
              {updateState === 'error' && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-red-400">
                    <span className="material-symbols-outlined">error</span>
                    <span className="text-xs">{updateError || 'Update failed'}</span>
                  </div>
                  <button
                    onClick={handleCheckForUpdates}
                    className="w-full px-4 py-2 bg-[var(--color-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-lg">refresh</span>
                    Try Again
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
