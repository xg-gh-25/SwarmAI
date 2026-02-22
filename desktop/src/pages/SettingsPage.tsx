import { useState, useEffect } from 'react';
import { tauriService, BackendStatus, getBackendPort, setBackendPort } from '../services/tauri';
import { settingsService, APIConfigurationResponse, BedrockAuthType } from '../services/settings';
import { Dropdown } from '../components/common';

// Check if running in development mode
const isDev = import.meta.env.DEV;

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
  const [checkingDependencies, setCheckingDependencies] = useState(false);

  useEffect(() => {
    // Load status first (which syncs the port), then load API config
    const init = async () => {
      await loadStatus();
      await loadAPIConfig();
      await checkSystemDependencies();
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
        let port = backend.port;
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
    } finally {
      setCheckingDependencies(false);
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-white mb-6">Settings</h1>

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
      <section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">API Configuration</h2>
        <div className="space-y-4">
          {/* Use Bedrock Toggle */}
          <div className="flex items-center justify-between p-3 bg-[#101622] rounded-lg">
            <div>
              <label className="text-sm font-medium text-white">Use AWS Bedrock</label>
              <p className="text-xs text-gray-500">Use AWS Bedrock instead of Anthropic API</p>
            </div>
            <button
              onClick={() => setUseBedrock(!useBedrock)}
              className={`relative w-12 h-6 rounded-full transition-colors ${
                useBedrock ? 'bg-[#2b6cee]' : 'bg-gray-600'
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
                <label className="block text-sm text-gray-400 mb-2">
                  Custom Base URL (Optional)
                </label>
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.anthropic.com (default)"
                  className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                />
                <p className="text-xs text-gray-500 mt-1">
                  For proxies or custom endpoints. Leave empty for default.
                </p>
              </div>

              {/* API Key */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">
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
                  className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Leave blank to keep existing key. Your API key is stored securely.
                </p>
              </div>
            </>
          )}

          {useBedrock && (
            <>
              {/* Authentication Type Selector */}
              <div>
                <label className="block text-sm text-gray-400 mb-2">Authentication Method</label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setBedrockAuthType('credentials')}
                    className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      bedrockAuthType === 'credentials'
                        ? 'bg-[#2b6cee] text-white'
                        : 'bg-[#101622] text-gray-400 border border-gray-700 hover:border-gray-500'
                    }`}
                  >
                    AK/SK Credentials
                  </button>
                  <button
                    type="button"
                    onClick={() => setBedrockAuthType('bearer_token')}
                    className={`flex-1 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                      bedrockAuthType === 'bearer_token'
                        ? 'bg-[#2b6cee] text-white'
                        : 'bg-[#101622] text-gray-400 border border-gray-700 hover:border-gray-500'
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
                    <label className="block text-sm text-gray-400 mb-2">
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
                      className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                    />
                  </div>

                  {/* AWS Secret Access Key */}
                  <div>
                    <label className="block text-sm text-gray-400 mb-2">AWS Secret Access Key</label>
                    <input
                      type="password"
                      value={awsSecretKey}
                      onChange={(e) => setAwsSecretKey(e.target.value)}
                      placeholder="••••••••••••••••"
                      className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                    />
                  </div>

                  {/* AWS Session Token */}
                  <div>
                    <label className="block text-sm text-gray-400 mb-2">
                      AWS Session Token (Optional)
                    </label>
                    <input
                      type="password"
                      value={awsSessionToken}
                      onChange={(e) => setAwsSessionToken(e.target.value)}
                      placeholder="For temporary credentials"
                      className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Only needed for temporary security credentials (STS).
                    </p>
                  </div>
                </>
              )}

              {bedrockAuthType === 'bearer_token' && (
                <>
                  {/* AWS Bearer Token */}
                  <div>
                    <label className="block text-sm text-gray-400 mb-2">
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
                      className="w-full px-4 py-2 bg-[#101622] border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-[#2b6cee]"
                    />
                    <p className="text-xs text-gray-500 mt-1">
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
            className="w-full px-4 py-2 bg-[#2b6cee] text-white rounded-lg hover:bg-[#2b6cee]/80 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save API Configuration'}
          </button>
        </div>
      </section>

      {/* Claude Agent SDK */}
      <section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Claude Agent SDK</h2>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Status</span>
            <span className="text-green-400">✓ Bundled</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Version</span>
            <span className="text-white">0.1.20</span>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            The Claude Agent SDK includes a bundled Claude Code CLI. No external installation required.
          </p>
        </div>
      </section>

      {/* System Dependencies */}
      {!isDev && (
        <section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">System Dependencies</h2>
            <button
              onClick={checkSystemDependencies}
              disabled={checkingDependencies}
              className="px-3 py-1 text-xs bg-[#101622] text-gray-400 rounded hover:bg-[#2b6cee] hover:text-white transition-colors disabled:opacity-50"
            >
              {checkingDependencies ? 'Checking...' : 'Refresh'}
            </button>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Node.js</span>
              {nodejsVersion === null ? (
                <span className="text-gray-500">Checking...</span>
              ) : nodejsVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{nodejsVersion}</span>
              )}
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Python</span>
              {pythonVersion === null ? (
                <span className="text-gray-500">Checking...</span>
              ) : pythonVersion === 'Not installed' ? (
                <span className="text-red-400">✗ Not found</span>
              ) : (
                <span className="text-green-400">{pythonVersion}</span>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              System-level dependencies detected in PATH. These are not required for the app to run.
            </p>
          </div>
        </section>
      )}

      {/* Backend Status */}
      <section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Backend Service</h2>
        {backendStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Status</span>
              <span className={backendStatus.running ? 'text-green-400' : 'text-red-400'}>
                {backendStatus.running ? '● Running' : '○ Stopped'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-400">Port</span>
              <span className="text-white">{backendStatus.port}</span>
            </div>
          </div>
        ) : (
          <p className="text-gray-500">Loading...</p>
        )}
      </section>

      {/* Storage Info */}
      <section className="mb-8 bg-[#1a1f2e] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">Storage</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Data Directory</span>
            <span className="text-white font-mono text-xs">
              ~/Library/Application Support/Owork/
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Skills Directory</span>
            <span className="text-white font-mono text-xs">
              ~/Library/Application Support/Owork/skills/
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Database</span>
            <span className="text-white font-mono text-xs">data.db (SQLite)</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Logs Directory</span>
            <span className="text-white font-mono text-xs">
              ~/Library/Application Support/Owork/logs/
            </span>
          </div>
        </div>
      </section>

      {/* About */}
      <section className="bg-[#1a1f2e] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-white mb-4">About</h2>
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Version</span>
            <span className="text-white">0.0.1-beta</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Platform</span>
            <span className="text-white">macOS</span>
          </div>
        </div>
      </section>
    </div>
  );
}
