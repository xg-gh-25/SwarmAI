// Sandbox Configuration Types (Built-in SDK bash sandboxing)
export interface SandboxNetworkConfig {
  allowLocalBinding: boolean;
  allowUnixSockets: string[];
  allowAllUnixSockets: boolean;
}

export interface SandboxConfig {
  enabled: boolean;
  autoAllowBashIfSandboxed: boolean;
  excludedCommands: string[];
  allowUnsandboxedCommands: boolean;
  network: SandboxNetworkConfig;
}

export interface SandboxNetworkConfigRequest {
  allowLocalBinding?: boolean;
  allowUnixSockets?: string[];
  allowAllUnixSockets?: boolean;
}

export interface SandboxConfigRequest {
  enabled?: boolean;
  autoAllowBashIfSandboxed?: boolean;
  excludedCommands?: string[];
  allowUnsandboxedCommands?: boolean;
  network?: SandboxNetworkConfigRequest;
}

// Agent Types
export interface Agent {
  id: string;
  name: string;
  description?: string;
  model?: string;
  permissionMode: 'default' | 'acceptEdits' | 'plan' | 'bypassPermissions';
  systemPrompt?: string;
  allowedTools: string[];
  pluginIds: string[];
  skillIds: string[];
  allowAllSkills: boolean;
  mcpIds: string[];
  workingDirectory?: string;
  enableBashTool: boolean;
  enableFileTools: boolean;
  enableWebTools: boolean;
  enableToolLogging: boolean;
  enableSafetyChecks: boolean;
  globalUserMode: boolean;
  enableHumanApproval: boolean;
  sandbox?: SandboxConfig;
  status: 'active' | 'inactive';
  createdAt: string;
  updatedAt: string;
}

export interface AgentCreateRequest {
  name: string;
  description?: string;
  model?: string;
  permissionMode?: 'default' | 'acceptEdits' | 'plan' | 'bypassPermissions';
  systemPrompt?: string;
  pluginIds?: string[];
  skillIds?: string[];
  allowAllSkills?: boolean;
  mcpIds?: string[];
  allowedTools?: string[];
  enableBashTool?: boolean;
  enableFileTools?: boolean;
  enableWebTools?: boolean;
  globalUserMode?: boolean;
  enableHumanApproval?: boolean;
  sandbox?: SandboxConfigRequest;
}

export interface AgentUpdateRequest extends Partial<AgentCreateRequest> {}

// Skill Types
export interface Skill {
  id: string;
  name: string;
  description: string;
  folderName?: string;
  localPath?: string;
  // Source tracking
  sourceType: 'user' | 'plugin' | 'marketplace' | 'local';
  sourcePluginId?: string;
  sourceMarketplaceId?: string;
  sourcePluginName?: string;
  sourceMarketplaceName?: string;
  // Git tracking
  gitUrl?: string;
  gitBranch?: string;
  gitCommit?: string;
  // Metadata
  createdBy?: string;
  createdAt: string;
  updatedAt: string;
  version: string;
  isSystem: boolean;
  // Version control fields
  currentVersion: number;
  hasDraft: boolean;
}

export interface SkillCreateRequest {
  name: string;
  description: string;
}

export interface SkillVersion {
  id: string;
  skillId: string;
  version: number;
  gitCommit?: string;
  localPath?: string;
  createdAt: string;
  changeSummary?: string;
}

export interface SkillVersionList {
  skillId: string;
  skillName: string;
  currentVersion: number;
  hasDraft: boolean;
  versions: SkillVersion[];
}

export interface SyncError {
  skill: string;
  error: string;
}

export interface SyncResult {
  added: string[];
  updated: string[];
  removed: string[];
  errors: SyncError[];
  totalLocal: number;
  totalPlugins: number;
  totalDb: number;
}

// MCP Server Types
export interface MCPServer {
  id: string;
  name: string;
  description?: string;
  connectionType: 'stdio' | 'sse' | 'http';
  config: Record<string, unknown>;
  allowedTools?: string[];
  rejectedTools?: string[];
  endpoint?: string;
  version?: string;
  createdAt: string;
  updatedAt: string;
}

export interface MCPServerCreateRequest {
  name: string;
  description?: string;
  connectionType: 'stdio' | 'sse' | 'http';
  config: Record<string, unknown>;
  allowedTools?: string[];
  rejectedTools?: string[];
}

export interface MCPServerUpdateRequest extends Partial<MCPServerCreateRequest> {}

// Chat/Message Types
export interface ChatSession {
  id: string;
  agentId: string;
  title: string;
  createdAt: string;
  lastAccessedAt: string;
  workDir?: string;
}

export interface ChatMessage {
  id: string;
  sessionId: string;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  model?: string;
  createdAt: string;
}

export interface TextContent {
  type: 'text';
  text: string;
}

export interface ToolUseContent {
  type: 'tool_use';
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResultContent {
  type: 'tool_result';
  toolUseId: string;
  content?: string;
  isError: boolean;
}

// AskUserQuestion types
export interface AskUserQuestionOption {
  label: string;
  description: string;
}

export interface AskUserQuestion {
  question: string;
  header: string;
  options: AskUserQuestionOption[];
  multiSelect: boolean;
}

export interface AskUserQuestionContent {
  type: 'ask_user_question';
  toolUseId: string;
  questions: AskUserQuestion[];
}

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
}

export type ContentBlock = TextContent | ToolUseContent | ToolResultContent | AskUserQuestionContent;

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  timestamp: string;
  model?: string;
}

export interface ChatRequest {
  agentId: string;
  message?: string;  // Optional if content is provided
  content?: ContentBlock[];  // Multimodal content array
  sessionId?: string;
  enableSkills?: boolean;
  enableMCP?: boolean;
  addDirs?: string[];  // Additional directories for Claude to access
}

// File Attachment Types
export type AttachmentType = 'image' | 'pdf' | 'text' | 'csv';

export interface FileAttachment {
  id: string;
  file: File;
  name: string;
  type: AttachmentType;
  size: number;
  preview?: string;  // Data URL for image preview
  base64?: string;   // Base64 encoded data (without prefix)
  mediaType: string; // MIME type
  error?: string;
  isLoading: boolean;
}

// Multimodal Content Block Types for API
export interface ImageSourceBase64 {
  type: 'base64';
  media_type: string;  // "image/png", "image/jpeg", etc.
  data: string;
}

export interface ImageContentBlock {
  type: 'image';
  source: ImageSourceBase64;
}

export interface DocumentSourceBase64 {
  type: 'base64';
  media_type: string;  // "application/pdf"
  data: string;
}

export interface DocumentContentBlock {
  type: 'document';
  source: DocumentSourceBase64;
}

// File size limits
export const FILE_SIZE_LIMITS = {
  image: 5 * 1024 * 1024,    // 5MB for images
  pdf: 10 * 1024 * 1024,     // 10MB for PDF
  text: 10 * 1024 * 1024,    // 10MB for TXT
  csv: 10 * 1024 * 1024,     // 10MB for CSV
} as const;

export const MAX_ATTACHMENTS = 5;

// Supported file types for attachment
export const SUPPORTED_FILE_TYPES = {
  image: ['image/png', 'image/jpeg', 'image/gif', 'image/webp'],
  pdf: ['application/pdf'],
  text: ['text/plain'],
  csv: ['text/csv', 'application/csv'],
} as const;

export interface StreamEvent {
  type: 'assistant' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'ask_user_question' | 'session_start' | 'session_cleared' | 'permission_request' | 'permission_decision' | 'permission_acknowledged' | 'heartbeat';
  content?: ContentBlock[];
  model?: string;
  sessionId?: string;
  durationMs?: number;
  totalCostUsd?: number;
  numTurns?: number;
  skillName?: string; // For skill creation result
  // AskUserQuestion fields
  toolUseId?: string;
  questions?: AskUserQuestion[];
  // PermissionRequest fields
  requestId?: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  reason?: string;
  options?: string[];
  // PermissionDecision fields (response to permission_request)
  decision?: 'approve' | 'deny';
  // SessionCleared fields (for /clear command)
  oldSessionId?: string;
  newSessionId?: string;
  // Heartbeat fields
  timestamp?: number;
  // Error fields
  error?: string;
  message?: string;
  code?: string;
  detail?: string;
  suggestedAction?: string;
}

// Human-in-the-Loop Permission Types
export interface PermissionRequest {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  options: string[];
}

export interface PermissionResponse {
  sessionId: string;
  requestId: string;
  decision: 'approve' | 'deny';
  feedback?: string;
}

// API Response Types
export interface ApiResponse<T> {
  data: T;
  message?: string;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
}

// Error Types
export interface ErrorResponse {
  code: string;
  message: string;
  detail?: string;
  suggestedAction?: string;
  requestId?: string;
}

export interface ValidationErrorField {
  field: string;
  error: string;
}

export interface ValidationErrorResponse extends ErrorResponse {
  code: 'VALIDATION_FAILED';
  fields: ValidationErrorField[];
}

export interface RateLimitErrorResponse extends ErrorResponse {
  code: 'RATE_LIMIT_EXCEEDED';
  retryAfter: number;
}

// Error code constants
export const ErrorCodes = {
  // Validation (400)
  VALIDATION_FAILED: 'VALIDATION_FAILED',
  // Authentication (401)
  AUTH_TOKEN_MISSING: 'AUTH_TOKEN_MISSING',
  AUTH_TOKEN_INVALID: 'AUTH_TOKEN_INVALID',
  AUTH_TOKEN_EXPIRED: 'AUTH_TOKEN_EXPIRED',
  // Authorization (403)
  FORBIDDEN: 'FORBIDDEN',
  // Not Found (404)
  AGENT_NOT_FOUND: 'AGENT_NOT_FOUND',
  SKILL_NOT_FOUND: 'SKILL_NOT_FOUND',
  MCP_SERVER_NOT_FOUND: 'MCP_SERVER_NOT_FOUND',
  SESSION_NOT_FOUND: 'SESSION_NOT_FOUND',
  // Conflict (409)
  DUPLICATE_RESOURCE: 'DUPLICATE_RESOURCE',
  // Rate Limit (429)
  RATE_LIMIT_EXCEEDED: 'RATE_LIMIT_EXCEEDED',
  // Server (500)
  SERVER_ERROR: 'SERVER_ERROR',
  AGENT_EXECUTION_ERROR: 'AGENT_EXECUTION_ERROR',
  AGENT_TIMEOUT: 'AGENT_TIMEOUT',
  // Service (503)
  SERVICE_UNAVAILABLE: 'SERVICE_UNAVAILABLE',
  DATABASE_UNAVAILABLE: 'DATABASE_UNAVAILABLE',
} as const;

export type ErrorCode = (typeof ErrorCodes)[keyof typeof ErrorCodes];

// Loading State Types
export type LoadingState = 'idle' | 'loading' | 'success' | 'error';

export interface LoadingStateInfo {
  state: LoadingState;
  error?: ErrorResponse;
}

// Workspace File Browser Types
export interface WorkspaceFile {
  name: string;
  type: 'file' | 'directory';
  size: number;
  modified: string;
}

export interface WorkspaceListResponse {
  files: WorkspaceFile[];
  currentPath: string;
  parentPath: string | null;
}

export interface WorkspaceFileContent {
  content: string;
  encoding: 'utf-8' | 'base64';
  size: number;
  mimeType: string;
}

// ============== Marketplace Types ==============

export interface AvailablePlugin {
  name: string;
  description?: string;
  version: string;
  author?: string;
  keywords: string[];
}

export interface Marketplace {
  id: string;
  name: string;
  description?: string;
  type: 'git' | 'http' | 'local';
  url: string;
  branch: string;
  isActive: boolean;
  lastSyncedAt?: string;
  cachedPlugins: AvailablePlugin[];
  createdAt: string;
  updatedAt: string;
}

export interface MarketplaceCreateRequest {
  name: string;
  description?: string;
  type: 'git' | 'http' | 'local';
  url: string;
  branch?: string;
}

export interface MarketplaceUpdateRequest {
  name?: string;
  description?: string;
  url?: string;
  branch?: string;
}

export interface MarketplaceSyncResponse {
  marketplaceId: string;
  marketplaceName: string;
  isMarketplace: boolean;
  pluginsFound: number;
  plugins: AvailablePlugin[];
  syncedAt: string;
}

// ============== Plugin Types ==============

export interface Plugin {
  id: string;
  name: string;
  description?: string;
  version: string;
  marketplaceId: string;
  marketplaceName?: string;
  author?: string;
  license?: string;
  installedSkills: string[];
  installedCommands: string[];
  installedAgents: string[];
  installedHooks: string[];
  installedMcpServers: string[];
  status: 'installed' | 'disabled' | 'error';
  installPath?: string;
  installedAt: string;
  updatedAt: string;
}

export interface PluginInstallRequest {
  pluginName: string;
  marketplaceId: string;
  version?: string;
}

export interface PluginUninstallResponse {
  pluginId: string;
  removedSkills: string[];
  removedCommands: string[];
  removedAgents: string[];
  removedHooks: string[];
}

// ============== Task Types ==============

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Task {
  id: string;
  agentId: string;
  sessionId: string | null;
  status: TaskStatus;
  title: string;
  model: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  error: string | null;
  workDir: string | null;
}

export interface TaskCreateRequest {
  agentId: string;
  message?: string;
  content?: ContentBlock[];
  enableSkills?: boolean;
  enableMcp?: boolean;
  addDirs?: string[];
}

export interface TaskMessageRequest {
  message?: string;
  content?: ContentBlock[];
}

export interface RunningTaskCount {
  count: number;
}
