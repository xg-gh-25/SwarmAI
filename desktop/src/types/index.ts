/**
 * Shared TypeScript type definitions and constants for the SwarmAI desktop app.
 *
 * Key exports:
 * - ``UnifiedAttachment``   — Unified file attachment representation (all input sources)
 * - ``AttachmentType``      — Classified file type ('image' | 'pdf' | 'text' | 'csv')
 * - ``DeliveryStrategy``    — How a file is delivered to the backend/Claude SDK
 * - ``SIZE_LIMITS``         — Per-type maximum file size in bytes
 * - ``SIZE_THRESHOLD``      — Text size threshold for inline vs path_hint delivery
 * - ``MAX_ATTACHMENTS``     — Maximum attachments per message (10)
 * - ``SUPPORTED_FILE_TYPES``— Recognized MIME types and file extensions
 * - ``FileAttachment``      — (deprecated) Legacy attachment type, use UnifiedAttachment
 * - ``FILE_SIZE_LIMITS``    — (deprecated) Alias for SIZE_LIMITS
 *
 * Also exports interfaces for agents, chat sessions, messages, content blocks,
 * workspaces, plugins, channels, tasks, projects, and various API types.
 */

// Sandbox types REMOVED — sandbox is app-level (config.json), not per-agent.
// All sandbox config lives in AppConfigManager.DEFAULT_CONFIG.

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
  // sandboxEnabled removed — sandbox is app-level (config.json), not per-agent
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
  // sandboxEnabled removed — sandbox is app-level (config.json), not per-agent
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

// MCP Catalog Types (Optional MCP servers users can install)
export interface MCPCatalogEnvField {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  default?: string;
}

export interface MCPCatalogPreset {
  label: string;
  env: Record<string, string>;
  setup_hint: string;
}

export interface MCPCatalogEntry {
  id: string;
  name: string;
  description: string;
  connection_type: 'stdio' | 'sse' | 'http';
  category: string;
  package: string;
  runtime?: string;
  config: Record<string, unknown>;
  required_env: MCPCatalogEnvField[];
  optional_env: MCPCatalogEnvField[];
  presets: Record<string, MCPCatalogPreset>;
  setup_command?: string | null;
  setup_docs_url?: string;
  installed: boolean;
}

export interface MCPCatalogInstallRequest {
  catalog_id: string;
  env: Record<string, string>;
}

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

export interface ThinkingContent {
  type: 'thinking';
  thinking: string;
}

export interface CmdPermissionContent {
  type: 'cmd_permission_request';
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  options?: string[];
  /** Set after user makes a decision — renders decided state inline. */
  decision?: 'approve' | 'deny';
}

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
}

export type ContentBlock = TextContent | ToolUseContent | ToolResultContent | AskUserQuestionContent | CmdPermissionContent | ThinkingContent;

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  timestamp: string;
  model?: string;
  /** When true, the message contains an error and should be visually distinguished (red border). */
  isError?: boolean;
  /** Ephemeral — renders "Queued" badge. Never persisted to DB. */
  isQueued?: boolean;
  /** When set, this message represents an evolution SSE event and should be rendered with EvolutionMessage. */
  evolutionEvent?: {
    eventType: string;
    data: Record<string, unknown>;
  };
}

export interface ChatRequest {
  agentId: string;
  message?: string;  // Optional if content is provided
  content?: ContentBlock[];  // Multimodal content array
  sessionId?: string;
  enableSkills?: boolean;
  enableMCP?: boolean;
  /** Currently open file in the editor panel — injected as agent context */
  editorContext?: { filePath: string; fileName: string };
}

// ============== File Attachment Types ==============

/** Classified file type for attachment processing. */
export type AttachmentType = 'image' | 'pdf' | 'document' | 'audio' | 'video' | 'text' | 'csv';

/** How a file is delivered to the backend/Claude SDK. */
export type DeliveryStrategy = 'base64_image' | 'base64_document' | 'inline_text' | 'path_hint';

/**
 * Unified attachment representation used by the useUnifiedAttachments hook.
 * Replaces the split FileAttachment / LayoutContext.attachedFiles state with
 * a single type that covers all input sources (File Picker, Workspace Explorer,
 * OS Finder drop, clipboard paste).
 */
export interface UnifiedAttachment {
  id: string;
  name: string;
  type: AttachmentType;
  deliveryStrategy: DeliveryStrategy;
  size: number;
  mediaType: string;
  base64?: string;
  textContent?: string;
  workspacePath?: string;
  preview?: string;
  isLoading: boolean;
  error?: string;
}

/** @deprecated Use UnifiedAttachment instead. Kept for backward compatibility. */
export interface FileAttachment {
  id: string;
  file: File;
  name: string;
  type: AttachmentType;
  size: number;
  preview?: string;
  base64?: string;
  mediaType: string;
  error?: string;
  isLoading: boolean;
}

// Multimodal Content Block Types for API
export interface ImageSourceBase64 {
  type: 'base64';
  media_type: string;
  data: string;
}

export interface ImageContentBlock {
  type: 'image';
  source: ImageSourceBase64;
}

export interface DocumentSourceBase64 {
  type: 'base64';
  media_type: string;
  data: string;
}

export interface DocumentContentBlock {
  type: 'document';
  source: DocumentSourceBase64;
}

// ============== Attachment Size Limits ==============

/** Per-type maximum file size in bytes. */
export const SIZE_LIMITS = {
  image: 20 * 1024 * 1024,       // 20MB  — Claude API max per image
  pdf: 25 * 1024 * 1024,         // 25MB  — Bedrock 32MB payload minus base64 overhead
  document: 25 * 1024 * 1024,    // 25MB  — same as PDF (base64_document)
  audio: 500 * 1024 * 1024,      // 500MB — path_hint only, file stays local
  video: 1024 * 1024 * 1024,     // 1GB   — path_hint only, file stays local
  text: 2 * 1024 * 1024,         // 2MB   — inlined into prompt, keep conservative
  csv: 2 * 1024 * 1024,          // 2MB   — inlined into prompt, keep conservative
} as const;

/** @deprecated Use SIZE_LIMITS instead. */
export const FILE_SIZE_LIMITS = SIZE_LIMITS;

/** Text files above this threshold use path_hint instead of inline_text. */
export const SIZE_THRESHOLD = 50 * 1024; // 50KB

/** Maximum number of attachments per message. */
export const MAX_ATTACHMENTS = 10;

// ============== Supported File Types ==============

/**
 * Recognized MIME types and file extensions for attachment classification.
 * Extensions cover common code, config, data, and document formats.
 */
export const SUPPORTED_FILE_TYPES = {
  image: ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'image/bmp', 'image/tiff', 'image/heic', 'image/heif'],
  pdf: ['application/pdf'],
  document: [
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',        // .docx
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',              // .xlsx
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',      // .pptx
    'application/msword',                                                              // .doc
    'application/vnd.ms-excel',                                                        // .xls
    'application/vnd.ms-powerpoint',                                                   // .ppt
    'application/rtf',                                                                 // .rtf
  ],
  audio: ['audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/ogg', 'audio/flac', 'audio/aac', 'audio/webm'],
  video: ['video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska', 'video/webm', 'video/mpeg'],
  text: ['text/plain', 'text/html', 'text/markdown', 'application/json', 'application/xml'],
  csv: ['text/csv', 'application/csv'],
  /** Recognized code/config file extensions (classified as 'text'). */
  codeExtensions: [
    '.py', '.ts', '.tsx', '.js', '.jsx',
    '.rs', '.go', '.java', '.c', '.cpp', '.h', '.rb', '.sh',
    '.md', '.txt', '.log', '.env', '.cfg', '.ini', '.conf',
    '.json', '.yaml', '.yml', '.toml',
    '.sql', '.html', '.css', '.scss', '.xml',
    '.kt', '.swift', '.r', '.lua', '.pl', '.php',
    '.dart', '.scala', '.zig', '.tf', '.hcl',
    '.proto', '.graphql', '.gql',
  ],
  /** Recognized image file extensions. */
  imageExtensions: ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.ico', '.heic', '.heif'],
  /** Recognized PDF extensions. */
  pdfExtensions: ['.pdf'],
  /** Recognized Office document extensions (classified as 'document'). */
  documentExtensions: ['.docx', '.xlsx', '.pptx', '.doc', '.xls', '.ppt', '.rtf', '.odt', '.ods', '.odp'],
  /** Recognized audio extensions. */
  audioExtensions: ['.mp3', '.m4a', '.wav', '.ogg', '.flac', '.aac', '.wma', '.opus', '.weba'],
  /** Recognized video extensions. */
  videoExtensions: ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mpeg', '.mpg', '.wmv', '.flv', '.3gp', '.m4v'],
  /** Recognized data file extensions (classified as 'csv'). */
  csvExtensions: ['.csv'],
} as const;

/** Compaction guard event from the backend compaction amnesia loop detector. */
export interface CompactionGuardEvent {
  subtype: 'soft_warn' | 'hard_warn' | 'kill';
  contextPct: number;
  message: string;
  patternDescription?: string;
}

export interface StreamEvent {
  type: 'assistant' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'reconnecting' | 'session_resuming' | 'ask_user_question' | 'session_start' | 'session_cleared' | 'cmd_permission_request' | 'cmd_permission_decision' | 'cmd_permission_acknowledged' | 'heartbeat' | 'agent_activity' | 'tool_invocation' | 'capability_activated' | 'sources_updated' | 'summary_updated' | 'context_warning' | 'context_compacted' | 'compaction_guard' | (string & {});
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
  // Queue timeout retry payload
  retryPayload?: {
    sessionId: string;
    agentId: string;
    userMessage: string | null;
    content: unknown[] | null;
  };
  // Context warning fields (context_warning event)
  level?: 'ok' | 'warn' | 'critical';
  pct?: number;
  tokensEst?: number;
  // Compaction guard fields (compaction_guard event)
  subtype?: string;
  contextPct?: number;
  patternDescription?: string;
  // Context compaction fields (context_compacted event)
  trigger?: 'manual' | 'auto';
  // Usage/caching fields (result event)
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
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
  // Streaming delta fields (partial message updates)
  text?: string;         // text_delta: incremental text chunk
  thinking?: string;     // thinking_delta: incremental thinking chunk
  index?: number;        // content block index for deltas
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

export type ChannelType = 'slack';
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
  /** True when this entry is a filesystem symlink (e.g., linked project). */
  isSymlink?: boolean;
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

/** Live cognitive state for a single thread. */
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

// ============== Toast Notification Types ==============

export type ToastSeverity = 'success' | 'info' | 'warning' | 'error';

export interface ToastOptions {
  severity: ToastSeverity;
  message: string;
  autoDismiss?: boolean;
  durationMs?: number;
  id?: string;
  action?: { label: string; onClick: () => void };
}

export interface ToastItem extends ToastOptions {
  id: string;
  createdAt: number;
}

// ============== Health Monitor Types ==============

export type BackendStatus = 'connected' | 'disconnected' | 'initializing';

export interface HealthState {
  status: BackendStatus;
  lastCheckedAt: number | null;
  consecutiveFailures: number;
}

// ============== Rate Limit Types ==============

export interface RateLimitEntry {
  endpoint: string;
  expiresAt: number;
  retryAfterSec: number;
}

// ============== Validation Error Display Types ==============

export interface FieldErrorMap {
  [fieldName: string]: string;
}



// ============== Radar Types (Swarm Radar Redesign) ==============

export * from './radar';
