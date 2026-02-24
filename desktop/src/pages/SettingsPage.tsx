import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { getVersion } from '@tauri-apps/api/app';
import { tauriService, BackendStatus, getBackendPort, setBackendPort } from '../services/tauri';
import { settingsService, APIConfigurationResponse, BedrockAuthType } from '../services/settings';
import { useTheme, ThemeMode } from '../contexts';
import {
  checkForUpdates,
  downloadAndInstallUpdate,
  restartApp,
  formatBytes,
  UpdateProgress,
} from '../services/updater';
import { Update } from '@tauri-apps/plugin-updater';
import { Dropdown } from '../components/common';

// Check if running in development mode
const isDev = import.meta.env.DEV;

// Detect platform
function getPlatformInfo(): { platform: string; dataDir: string; skillsDir: string; logsDir: string } {
  const userAgent = navigator.userAgent.toLowerCase();

  if (userAgent.includes('win')) {
    return {
      platform: 'Windows',
      dataDir: '%LOCALAPPDATA%\\Owork\\',
      skillsDir: '%LOCALAPPDATA%\\Owork\\skills\\',
      logsDir: '%LOCALAPPDATA%\\Owork\\logs\\'
    };
  } else if (userAgent.includes('mac')) {
    return {
      platform: 'macOS',
      dataDir: '~/Library/Application Support/Owork/',
      skillsDir: '~/Library/Application Support/Owork/skills/',
      logsDir: '~/Library/Application Support/Owork/logs/'
    };
  } else {
    // Linux and other Unix-like systems
    return {
      platform: 'Linux',
      dataDir: '~/.local/share/owork/',
      skillsDir: '~/.local/share/owork/skills/',
      logsDir: '~/.local/share/owork/logs/'
    };
  }
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

  const handleLanguageChange = (lang: 'zh' | 'en') => {
    i18n.changeLanguage(lang);
    localStorage.setItem('language', lang);
  };

  const { mode: themeMode, setMode: setThemeMode } = useTheme();

  const handleThemeChange = (theme: ThemeMode) => {
    setThemeMode(theme);
  };

  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [apiConfig, setApiConfig] = useState<APIConfigurationResponse | null>(null);

  // Form fields
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [useBedrock, setUseBedrock] = useState(false);
  const [bedrockAuthType, setBedrockAuthType] = useState<BedrockAuthType>('credentials');
  const [awsAccessKey, setAwsAccessKey] = useState('');
  const [awsSecretKey, setAwsSecretKey] = useState('');
  const [awsSessionToken, setAwsSessionToken] = useState('');
  const [awsBearerToken, setAwsBearerToken] = useState('');
  const [awsRegion, setAwsRegion] = useState('us-east-1');

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

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

  useEffect(() => {
    // Load status first (which syncs the port), then load API config
    const init = async () => {
      await loadStatus();
      await loadAPIConfig();
      await checkSystemDependencies();

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
      setBaseUrl(config.anthropic_base_url || '');
      setUseBedrock(config.use_bedrock);
      setBedrockAuthType(config.bedrock_auth_type || 'credentials');
      setAwsRegion(config.aws_region);
    } catch (error) {
      console.error('Failed to load API config:', error);
    }
  };

  const handleSaveAPIConfig = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const updateData: Record<string, unknown> = {};

      // Only include fields that have values
      if (apiKey) updateData.anthropic_api_key = apiKey;
      updateData.anthropic_base_url = baseUrl || '';
      updateData.use_bedrock = useBedrock;

      if (useBedrock) {
        updateData.bedrock_auth_type = bedrockAuthType;
        updateData.aws_region = awsRegion;

        if (bedrockAuthType === 'credentials') {
          // AK/SK authentication
          if (awsAccessKey) updateData.aws_access_key_id = awsAccessKey;
          if (awsSecretKey) updateData.aws_secret_access_key = awsSecretKey;
          updateData.aws_session_token = awsSessionToken || '';
        } else {
          // Bearer token authentication
          if (awsBearerToken) updateData.aws_bearer_token = awsBearerToken;
        }
      }

      const config = await settingsService.updateAPIConfiguration(updateData);
      setApiConfig(config);

      // Clear sensitive fields after save
      setApiKey('');
      setAwsAccessKey('');
      setAwsSecretKey('');
      setAwsSessionToken('');
      setAwsBearerToken('');

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
      <h1 className="text-2xl font-bold text-white mb-6">{t('settings.title')}</h1>

      {/* Language Settings */}
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-2">{t('settings.language.title')}</h2>
        <p className="text-sm text-muted mb-4">{t('settings.language.description')}</p>
        <div className="flex gap-3">
          <button
            onClick={() => handleLanguageChange('zh')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              i18n.language === 'zh'
                ? 'bg-primary text-white'
                : 'bg-dark-bg text-muted border border-dark-border hover:border-muted'
            }`}
          >
            {i18n.language === 'zh' && <span className="material-symbols-outlined text-sm">check</span>}
            {t('settings.language.zh')}
          </button>
          <button
            onClick={() => handleLanguageChange('en')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              i18n.language === 'en'
                ? 'bg-primary text-white'
                : 'bg-dark-bg text-muted border border-dark-border hover:border-muted'
            }`}
          >
            {i18n.language === 'en' && <span className="material-symbols-outlined text-sm">check</span>}
            {t('settings.language.en')}
          </button>
        </div>
      </section>

      {/* Theme Settings */}
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold mb-2">{t('settings.theme.title')}</h2>
        <p className="text-sm text-muted mb-4">{t('settings.language.description')}</p>
        <div className="flex gap-3">
          <button
            onClick={() => handleThemeChange('light')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              themeMode === 'light'
                ? 'bg-primary text-white'
                : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
            }`}
          >
            <span className="material-symbols-outlined text-lg">light_mode</span>
            {t('settings.theme.light')}
          </button>
          <button
            onClick={() => handleThemeChange('dark')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              themeMode === 'dark'
                ? 'bg-primary text-white'
                : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
            }`}
          >
            <span className="material-symbols-outlined text-lg">dark_mode</span>
            {t('settings.theme.dark')}
          </button>
          <button
            onClick={() => handleThemeChange('system')}
            className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
              themeMode === 'system'
                ? 'bg-primary text-white'
                : 'bg-dark-bg border border-dark-border text-muted hover:border-muted'
            }`}
          >
            <span className="material-symbols-outlined text-lg">contrast</span>
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
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">API Configuration</h2>
        <div className="space-y-4">
          {/* Use Bedrock Toggle */}
          <div className="flex items-center justify-between p-3 bg-dark-bg rounded-lg">
            <div>
              <label className="text-sm font-medium text-white">Use AWS Bedrock</label>
              <p className="text-xs text-muted">Use AWS Bedrock instead of Anthropic API</p>
            </div>
            <button
              onClick={() => setUseBedrock(!useBedrock)}
              className={`relative w-12 h-6 rounded-full transition-colors ${
                useBedrock ? 'bg-primary' : 'bg-gray-600'
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
                <label className="block text-sm text-muted mb-2">
                  Custom Base URL (Optional)
                </label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.anthropic.com (default)"
                  className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                />
                <p className="text-xs text-muted mt-1">
                  For proxies or custom endpoints. Leave empty for default.
                </p>
              </div>

              {/* API Key */}
              <div>
                <label className="block text-sm text-muted mb-2">
                  API Key
                  {apiConfig?.anthropic_api_key_set && (
                    <span className="ml-2 text-green-400 text-xs">✓ Configured</span>
                  )}
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={apiConfig?.anthropic_api_key_set ? '••••••••••••••••' : 'sk-ant-...'}
                  className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                />
                <p className="text-xs text-muted mt-1">
                  Leave blank to keep existing key. Your API key is stored securely.
                </p>
              </div>
            </>
          )}

          {useBedrock && (
            <>
              {/* Authentication Type Selector */}
              <div>
                <label className="block text-sm text-muted mb-2">Authentication Method</label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setBedrockAuthType('credentials')}
                    className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      bedrockAuthType === 'credentials'
                        ? 'bg-primary text-white'
                        : 'bg-dark-bg text-muted border border-dark-border hover:border-muted'
                    }`}
                  >
                    AK/SK Credentials
                  </button>
                  <button
                    type="button"
                    onClick={() => setBedrockAuthType('bearer_token')}
                    className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      bedrockAuthType === 'bearer_token'
                        ? 'bg-primary text-white'
                        : 'bg-dark-bg text-muted border border-dark-border hover:border-muted'
                    }`}
                  >
                    Bearer Token
                  </button>
                </div>
              </div>

              {bedrockAuthType === 'credentials' && (
                <>
                  {/* AWS Access Key ID */}
                  <div>
                    <label className="block text-sm text-muted mb-2">
                      AWS Access Key ID
                      {apiConfig?.aws_access_key_id_set && (
                        <span className="ml-2 text-green-400 text-xs">✓ Configured</span>
                      )}
                    </label>
                    <input
                      type="text"
                      value={awsAccessKey}
                      onChange={(e) => setAwsAccessKey(e.target.value)}
                      placeholder={apiConfig?.aws_access_key_id_set ? '••••••••••••' : 'AKIA...'}
                      className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                    />
                  </div>

                  {/* AWS Secret Access Key */}
                  <div>
                    <label className="block text-sm text-muted mb-2">AWS Secret Access Key</label>
                    <input
                      type="password"
                      value={awsSecretKey}
                      onChange={(e) => setAwsSecretKey(e.target.value)}
                      placeholder="••••••••••••••••"
                      className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                    />
                  </div>

                  {/* AWS Session Token */}
                  <div>
                    <label className="block text-sm text-muted mb-2">
                      AWS Session Token (Optional)
                    </label>
                    <input
                      type="password"
                      value={awsSessionToken}
                      onChange={(e) => setAwsSessionToken(e.target.value)}
                      placeholder="For temporary credentials"
                      className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                    />
                    <p className="text-xs text-muted mt-1">
                      Only needed for temporary security credentials (STS).
                    </p>
                  </div>
                </>
              )}

              {bedrockAuthType === 'bearer_token' && (
                <>
                  {/* AWS Bearer Token */}
                  <div>
                    <label className="block text-sm text-muted mb-2">
                      AWS Bearer Token
                      {apiConfig?.aws_bearer_token_set && (
                        <span className="ml-2 text-green-400 text-xs">✓ Configured</span>
                      )}
                    </label>
                    <input
                      type="password"
                      value={awsBearerToken}
                      onChange={(e) => setAwsBearerToken(e.target.value)}
                      placeholder={apiConfig?.aws_bearer_token_set ? '••••••••••••••••' : 'Enter bearer token...'}
                      className="w-full px-4 py-2 bg-dark-bg border border-dark-border rounded-lg text-white placeholder-muted focus:outline-none focus:border-primary"
                    />
                    <p className="text-xs text-muted mt-1">
                      Bearer token for AWS Bedrock authentication.
                    </p>
                  </div>
                </>
              )}

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
            className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save API Configuration'}
          </button>
        </div>
      </section>

      {/* Claude Agent SDK */}
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Claude Agent SDK</h2>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-muted">Status</span>
            <span className="text-green-400">✓ Bundled</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Version</span>
            <span className="text-white">0.1.20</span>
          </div>
          <p className="text-xs text-muted mt-2">
            The Claude Agent SDK includes a bundled Claude Code CLI. No external installation required.
          </p>
        </div>
      </section>

      {/* System Dependencies */}
      {!isDev && (
        <section className="mb-8 bg-dark-card rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">System Dependencies</h2>
            <button
              onClick={checkSystemDependencies}
              disabled={checkingDependencies}
              className="px-3 py-1 text-xs bg-dark-bg text-muted rounded hover:bg-primary hover:text-white transition-colors disabled:opacity-50"
            >
              {checkingDependencies ? 'Checking...' : 'Refresh'}
            </button>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-muted">Node.js</span>
              {nodejsVersion === null ? (
                <span className="text-muted">Checking...</span>
              ) : nodejsVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{nodejsVersion}</span>
              )}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted">Python</span>
              {pythonVersion === null ? (
                <span className="text-muted">Checking...</span>
              ) : pythonVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{pythonVersion}</span>
              )}
            </div>
            {platformInfo.platform === 'Windows' && (
              <div className="flex items-center justify-between">
                <span className="text-muted">Git Bash</span>
                {gitBashPath === null ? (
                  <span className="text-muted">Checking...</span>
                ) : gitBashPath === 'Not found' ? (
                  <span className="text-red-400">✗ Not found</span>
                ) : (
                  <span className="text-green-400 text-xs font-mono truncate max-w-[300px]" title={gitBashPath}>
                    {gitBashPath}
                  </span>
                )}
              </div>
            )}
            <p className="text-xs text-muted mt-2">
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
              <p className="text-gray-300 text-sm mb-3">
                Git Bash is required for Claude Agent SDK to execute shell commands on Windows.
                Please install Git for Windows and configure the environment variable.
              </p>
              <div className="space-y-2 text-sm">
                <div>
                  <span className="text-muted">1. Download and install Git for Windows:</span>
                  <a
                    href="https://git-scm.com/downloads/win"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 text-primary hover:underline"
                  >
                    https://git-scm.com/downloads/win
                  </a>
                </div>
                <div>
                  <span className="text-muted">2. Set the environment variable:</span>
                  <code className="ml-2 px-2 py-1 bg-dark-bg rounded text-xs text-white">
                    CLAUDE_CODE_GIT_BASH_PATH
                  </code>
                </div>
                <div className="mt-2 p-3 bg-dark-bg rounded-lg">
                  <p className="text-muted text-xs mb-1">Example (default installation path):</p>
                  <code className="text-white text-xs font-mono">
                    CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe
                  </code>
                </div>
                <p className="text-muted text-xs mt-2">
                  After setting the environment variable, restart the application and click "Refresh" above to verify.
                </p>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Backend Status */}
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Backend Service</h2>
        {backendStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-muted">Status</span>
              <span className={backendStatus.running ? 'text-green-400' : 'text-red-400'}>
                {backendStatus.running ? '● Running' : '○ Stopped'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted">Port</span>
              <span className="text-white">{backendStatus.port}</span>
            </div>
          </div>
        ) : (
          <p className="text-muted">Loading...</p>
        )}
      </section>

      {/* Storage Info */}
      <section className="mb-8 bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Storage</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">Data Directory</span>
            <span className="text-white font-mono text-xs">
              {platformInfo.dataDir}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Skills Directory</span>
            <span className="text-white font-mono text-xs">
              {platformInfo.skillsDir}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Database</span>
            <span className="text-white font-mono text-xs">data.db (SQLite)</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Logs Directory</span>
            <span className="text-white font-mono text-xs">
              {platformInfo.logsDir}
            </span>
          </div>
        </div>
      </section>

      {/* About */}
      <section className="bg-dark-card rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">About</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">Version</span>
            <span className="text-white">{appVersion || 'Loading...'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Platform</span>
            <span className="text-white">{platformInfo.platform}</span>
          </div>

          {/* Update Section - only in production */}
          {!isDev && (
            <div className="pt-3 border-t border-dark-border">
              {/* Idle state - show check button */}
              {updateState === 'idle' && (
                <button
                  onClick={handleCheckForUpdates}
                  className="w-full px-4 py-2 bg-dark-bg text-white rounded-lg hover:bg-primary transition-colors flex items-center justify-center gap-2"
                >
                  <span className="material-symbols-outlined text-lg">update</span>
                  Check for Updates
                </button>
              )}

              {/* Checking state */}
              {updateState === 'checking' && (
                <div className="flex items-center justify-center gap-2 py-2 text-muted">
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
                    <div className="bg-dark-bg rounded p-3 text-xs text-muted max-h-24 overflow-y-auto">
                      {availableUpdate.body}
                    </div>
                  )}
                  <button
                    onClick={handleDownloadUpdate}
                    className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 transition-colors flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-lg">download</span>
                    Download & Install
                  </button>
                </div>
              )}

              {/* Downloading state */}
              {updateState === 'downloading' && updateProgress && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-muted">
                    <span>Downloading...</span>
                    <span>
                      {updateProgress.total
                        ? `${formatBytes(updateProgress.downloaded)} / ${formatBytes(updateProgress.total)}`
                        : formatBytes(updateProgress.downloaded)}
                    </span>
                  </div>
                  <div className="h-2 bg-dark-bg rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all duration-300"
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
                    className="w-full px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 transition-colors flex items-center justify-center gap-2"
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
                    className="w-full px-4 py-2 bg-dark-bg text-white rounded-lg hover:bg-primary transition-colors flex items-center justify-center gap-2"
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
