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
  allowedSkills: string[];
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
  sandboxEnabled: boolean;
  sandbox?: SandboxConfig;
  isDefault: boolean;
  isSystemAgent: boolean;
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
  allowedSkills?: string[];
  allowAllSkills?: boolean;
  mcpIds?: string[];
  allowedTools?: string[];
  enableBashTool?: boolean;
  enableFileTools?: boolean;
  enableWebTools?: boolean;
  globalUserMode?: boolean;
  enableHumanApproval?: boolean;
  sandboxEnabled?: boolean;
  sandbox?: SandboxConfigRequest;
}

export interface AgentUpdateRequest extends Partial<AgentCreateRequest> {}

// Skill Types — filesystem-based model (no DB UUIDs)
export interface Skill {
  folderName: string;       // primary identifier (kebab-case directory name)
  name: string;
  description: string;
  version: string;
  sourceTier: 'built-in' | 'user' | 'plugin';
  readOnly: boolean;        // true for built-in and plugin
  content?: string;         // only present in detail endpoint
}

export interface SkillCreateRequest {
  folderName: string;
  name: string;
  description: string;
  content: string;
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
  // Source tracking
  sourceType: 'user' | 'plugin' | 'marketplace' | 'system';
  isSystem: boolean;
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
  summary: string;
  category?: string;
}

export interface ToolResultContent {
  type: 'tool_result';
  toolUseId: string;
  content?: string;
  isError: boolean;
  truncated: boolean;
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
  /** When true, the message contains an error and should be visually distinguished (red border). */
  isError?: boolean;
}

export interface ChatRequest {
  agentId: string;
  message?: string;  // Optional if content is provided
  content?: ContentBlock[];  // Multimodal content array
  sessionId?: string;
  enableSkills?: boolean;
  enableMCP?: boolean;
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
  type: 'assistant' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'ask_user_question' | 'session_start' | 'session_cleared' | 'cmd_permission_request' | 'cmd_permission_decision' | 'cmd_permission_acknowledged' | 'heartbeat' | 'agent_activity' | 'tool_invocation' | 'capability_activated' | 'sources_updated' | 'summary_updated' | (string & {});
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
  // CmdPermissionRequest fields
  requestId?: string;
  toolName?: string;
  toolInput?: Record<string, unknown>;
  reason?: string;
  options?: string[];
  // CmdPermissionDecision fields (response to cmd_permission_request)
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
  // TSCC telemetry fields
  threadId?: string;
  agentName?: string;
  description?: string;
  capabilityType?: string;
  capabilityName?: string;
  label?: string;
  sourcePath?: string;
  origin?: string;
  keySummary?: string[];
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

export interface PolicyViolationDetail {
  entityType: string;
  entityId: string;
  message: string;
  suggestedAction: string;
}

export interface PolicyViolationErrorResponse extends ErrorResponse {
  code: 'POLICY_VIOLATION';
  policyViolations: PolicyViolationDetail[];
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
  POLICY_VIOLATION: 'POLICY_VIOLATION',
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
  /** True when the file is a system-default context file (user_customized=False). */
  readonly?: boolean;
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

// ============== Channel Types ==============

export type ChannelType = 'feishu' | 'slack' | 'discord' | 'web_widget';
export type ChannelStatus = 'active' | 'inactive' | 'error' | 'starting' | 'failed';
export type ChannelAccessMode = 'open' | 'allowlist' | 'blocklist';

export interface Channel {
  id: string;
  name: string;
  channelType: ChannelType;
  agentId: string;
  agentName?: string;
  config: Record<string, unknown>;
  status: ChannelStatus;
  errorMessage?: string;
  accessMode: ChannelAccessMode;
  allowedSenders: string[];
  blockedSenders: string[];
  rateLimitPerMinute: number;
  enableSkills: boolean;
  enableMcp: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ChannelCreateRequest {
  name: string;
  channelType: ChannelType;
  agentId: string;
  config?: Record<string, unknown>;
  accessMode?: ChannelAccessMode;
  allowedSenders?: string[];
  rateLimitPerMinute?: number;
  enableSkills?: boolean;
  enableMcp?: boolean;
}

export interface ChannelUpdateRequest {
  name?: string;
  config?: Record<string, unknown>;
  agentId?: string;
  accessMode?: ChannelAccessMode;
  allowedSenders?: string[];
  blockedSenders?: string[];
  rateLimitPerMinute?: number;
  enableSkills?: boolean;
  enableMcp?: boolean;
}

export interface ChannelStatusResponse {
  channelId: string;
  status: string;
  uptimeSeconds?: number;
  messagesProcessed: number;
  activeSessions: number;
  errorMessage?: string;
}

export interface ChannelSession {
  id: string;
  channelId: string;
  externalChatId: string;
  externalSenderId?: string;
  externalThreadId?: string;
  sessionId: string;
  senderDisplayName?: string;
  messageCount: number;
  lastMessageAt?: string;
  createdAt: string;
}

export interface ChannelTypeInfo {
  id: string;
  label: string;
  description: string;
  configFields: {
    key: string;
    label: string;
    type: string;
    required: boolean;
    options?: string[];
  }[];
  available: boolean;
}

// ============== Task Types ==============

export type TaskStatus = 'draft' | 'wip' | 'blocked' | 'completed' | 'cancelled';

export interface Task {
  id: string;
  workspaceId: string | null;
  agentId: string;
  sessionId: string | null;
  status: TaskStatus;
  title: string;
  description: string | null;
  priority: string | null;
  sourceTodoId: string | null;
  blockedReason: string | null;
  model: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  error: string | null;
  workDir: string | null;
  /** Always false in initial release — risk-assessment deferred. */
  reviewRequired: boolean;
  /** Always null in initial release — risk-assessment deferred. */
  reviewRiskLevel: string | null;
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

// ============== Swarm Workspace Types ==============

// ============== Workspace Config Types (Single-Workspace Model) ==============

/** Workspace configuration for the singleton SwarmWS. */
export interface WorkspaceConfig {
  id: string;
  name: string;
  filePath: string;
  icon?: string;
  context?: string;
  createdAt: string;
  updatedAt: string;
}

/** Request to update the singleton workspace config. */
export interface WorkspaceConfigUpdateRequest {
  icon?: string;
  context?: string;
}

// ============== Project Types ==============

/** Project metadata from .project.json (Cadence 2 enriched). */
export interface Project {
  id: string;
  name: string;
  description: string;
  path: string;
  createdAt: string;
  updatedAt: string;
  status: 'active' | 'archived' | 'completed';
  priority?: 'low' | 'medium' | 'high' | 'critical';
  tags: string[];
  schemaVersion: string;
  version: number;
  contextL0?: string;
  contextL1?: string;
}

/** A single entry in the project update history. */
export interface ProjectHistoryEntry {
  version: number;
  timestamp: string;
  action: string;
  changes: Record<string, { from: unknown; to: unknown }>;
  source: 'user' | 'agent' | 'system' | 'migration';
}

/** Request to create a new project. */
export interface ProjectCreateRequest {
  name: string;
}

/** Request to update a project. */
export interface ProjectUpdateRequest {
  name?: string;
  description?: string;
  status?: 'active' | 'archived' | 'completed';
  tags?: string[];
  priority?: 'low' | 'medium' | 'high' | 'critical' | null;
}

// ============== Workspace Explorer Types (Cadence 3) ==============

/** A node in the workspace filesystem tree.
 *
 * Returned by `GET /api/workspace/tree` and used by the VirtualizedTree
 * component to render the workspace explorer.
 *
 * - `path` is relative to the workspace root (e.g. "Knowledge/Notes/README.md").
 * - All files are user-manageable — no lock badges or system-managed restrictions.
 * - `children` is present only for directory nodes that have been expanded
 *   within the requested depth.
 */
export /**
 * Git status indicator for file tree nodes.
 *
 * - `added`       — newly staged file (green)
 * - `modified`    — changed since last commit (amber)
 * - `deleted`     — removed (red)
 * - `renamed`     — moved or renamed (teal)
 * - `untracked`   — new file not yet staged (green, dimmer)
 * - `conflicting` — merge conflict (bright red)
 * - `ignored`     — git-ignored (gray)
 */
type GitStatus =
  | 'added'
  | 'modified'
  | 'deleted'
  | 'renamed'
  | 'untracked'
  | 'conflicting'
  | 'ignored';

export interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: TreeNode[];
  /** Git status of this file/folder, if known. */
  gitStatus?: GitStatus;
}


// Context Assembly Preview Types (SwarmWS Intelligence)

/** A single layer in the context assembly preview. */
export interface ContextLayer {
  layerNumber: number;
  name: string;
  sourcePath: string;  // workspace-relative, never absolute
  tokenCount: number;
  contentPreview: string;
  truncated: boolean;
  truncationStage: number;
}

/** Full context assembly preview response. */
export interface ContextPreview {
  projectId: string;
  threadId: string | null;
  layers: ContextLayer[];
  totalTokenCount: number;
  budgetExceeded: boolean;
  tokenBudget: number;
  truncationSummary: string;
  etag: string;
}

/** Thread binding request. */
export interface ThreadBindRequest {
  taskId?: string;
  todoId?: string;
  mode: 'replace' | 'add';
}

/** Thread binding response. */
export interface ThreadBindResponse {
  threadId: string;
  taskId: string | null;
  todoId: string | null;
  contextVersion: number;
}

// ============== TSCC (Thread-Scoped Cognitive Context) Types ==============

/** Thread lifecycle state tracking execution phase. */
export type ThreadLifecycleState = 'new' | 'active' | 'paused' | 'failed' | 'cancelled' | 'idle';

/** Operational scope: workspace-level or project-level. */
export type ScopeType = 'workspace' | 'project';

/** The five telemetry event types emitted via SSE. */
export type TelemetryEventType =
  | 'agent_activity'
  | 'tool_invocation'
  | 'capability_activated'
  | 'sources_updated'
  | 'summary_updated';

/** Scope and thread metadata for the Current Context module. */
export interface TSCCContext {
  scopeLabel: string;
  threadTitle: string;
  mode?: string;
}

/** Grouped capability lists activated during thread execution. */
export interface TSCCActiveCapabilities {
  skills: string[];
  mcps: string[];
  tools: string[];
}

/** A source file or material referenced during execution. */
export interface TSCCSource {
  path: string;
  origin: string;
}

/** Live cognitive state for a single thread (all five modules). */
export interface TSCCLiveState {
  context: TSCCContext;
  activeAgents: string[];
  activeCapabilities: TSCCActiveCapabilities;
  whatAiDoing: string[];
  activeSources: TSCCSource[];
  keySummary: string[];
}

/** Full TSCC state for a chat thread. */
export interface TSCCState {
  threadId: string;
  projectId: string | null;
  scopeType: ScopeType;
  lastUpdatedAt: string;
  lifecycleState: ThreadLifecycleState;
  liveState: TSCCLiveState;
}

/** Point-in-time capture of TSCC state, stored as JSON file. */
export interface TSCCSnapshot {
  snapshotId: string;
  threadId: string;
  timestamp: string;
  reason: string;
  lifecycleState: ThreadLifecycleState;
  activeAgents: string[];
  activeCapabilities: TSCCActiveCapabilities;
  whatAiDoing: string[];
  activeSources: TSCCSource[];
  keySummary: string[];
}

// ============== System Prompt Metadata Types ==============

/** Metadata for a single context file loaded into the system prompt. */
export interface SystemPromptFileInfo {
  filename: string;
  tokens: number;
  truncated: boolean;
}

/** System prompt metadata returned by the system-prompt endpoint. */
export interface SystemPromptMetadata {
  files: SystemPromptFileInfo[];
  totalTokens: number;
  fullText: string;
}

// ============== Radar Types (Swarm Radar Redesign) ==============

export * from './radar';
