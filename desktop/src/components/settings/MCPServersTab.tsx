/**
 * MCP Servers settings tab.
 *
 * Catalog integrations (toggle + env) and Dev/Personal (full CRUD).
 * Replaces the standalone MCPSettingsModal — now lives in Settings.
 * Reuses MCPSettingsPanel directly (it's already well-structured with React Query).
 */
import MCPSettingsPanel from '../workspace-settings/MCPSettingsPanel';

export default function MCPServersTab() {
  return <MCPSettingsPanel />;
}
