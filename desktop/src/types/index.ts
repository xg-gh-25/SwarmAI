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
  sourceTier: 'built-in' | 'ddd' | 'user' | 'plugin';  // 'ddd' = DDD-owned domain skill (backend has it; was missing here)
  readOnly: boolean;        // true for built-in and plugin
  content?: string;         // only present in detail endpoint
  category: string;         // user-facing group (Capabilities domain); 'Utilities' fallback
  visibility: 'public' | 'internal';  // internal = owner-only (backend-filtered for non-owner)
  tier: 'always' | 'lazy';  // load tier (Capabilities panel faint marker); 'lazy' fallback
}

// Expert (R2 Experts visualization — the Capabilities "Experts" view). A read-only
// projection of the backend Expert Library (backend/experts/), one per adversarial
// review specialist. isValid is the health signal (false = malformed frontmatter,
// surfaced not dropped). No per-run D7 enforcement state here — that is a separate
// pipeline concern (kept out to avoid run-scoped coupling in a static registry view).
export interface Expert {
  expertId: string;         // frontmatter `expert:` — identity contract
  displayNameZh: string;
  displayNameEn: string;
  domain: string;           // grouping key (e.g. 'code')
  scope: string;            // one-line "what this expert reviews"
  visibility: string;       // 'public' | ...
  isValid: boolean;         // health: every required frontmatter field present
}
export type ExpertsByDomain = Record<string, Expert[]>;  // {domain: [Expert]}

// Skill health (Capabilities panel scannable dot, run_a85e6641). Qualitative status only
// on the row (no raw counts — R30#4); success_rate/last_used are for the detail drawer.
export type SkillHealthStatus = 'healthy' | 'low_success' | 'never_used' | 'stale';
export interface SkillHealth {
  status: SkillHealthStatus;
  success_rate: number | null;
  last_used: string | null;
  // Raw frequency — drives the Most-Used strip + within-group sort ORDER only; never
  // rendered as a count on a card (R30#4). null when the skill has never been used.
  invocation_count: number | null;
}
export type SkillHealthMap = Record<string, SkillHealth>;  // keyed by folderName

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
  role: 'user' | 'assistant' | 'system';
  content: ContentBlock[];
  model?: string;
  createdAt: string;
  /** Backend metadata — includes client_id for optimistic message dedup */
  metadata?: { client_id?: string; [key: string]: unknown };
  /** Which DB rows this message holds and at which transcript rev (backend
   *  `row_revs`, history reads only): a transcript change for one of them at or
   *  below that rev is already here (hooks/transcriptFeed skips it). */
  rowRevs?: Record<string, number>;
}

export interface TextContent {
  type: 'text';
  text: string;
  /** Marks this block as authoritative (from an assistant event), not provisional (from streaming delta). */
  _confirmed?: boolean;
  /** A provisional block's streamed block key (StreamEvent.block). */
  _streamBlock?: string;
}

export interface ToolUseContent {
  type: 'tool_use';
  id: string;
  name: string;
  summary: string;
  category?: string;
  /** Set on a SUB-AGENT's tool call: the Agent tool_use it ran under. Such a
   *  call is not a step of the main turn; it nests under that Agent step. */
  parentToolUseId?: string;
  /** What the chat strip shows (backend tool_summarizer.tool_display): the tool's own
   *  name, the call's object (redacted, up to 2000 chars: the hover shows it whole)
   *  and a key equal for calls with identical input. Absent on older history. */
  display?: { tool: string; object: string; key: string };
}

export interface ToolResultContent {
  type: 'tool_result';
  toolUseId: string;
  /** The (truncated) output — live only; the persisted form has no content. */
  content?: string;
  isError: boolean;
  truncated?: boolean;
  /** One-line result computed backend-side from the FULL output
   *  ("lines 1–240", "12 matches", last output line, error line). Live AND
   *  persisted (SessionRouter._compact_tool_results). */
  outcome?: string;
  /** Set on a sub-agent tool's result (see ToolUseContent.parentToolUseId). */
  parentToolUseId?: string;
  /** On an Agent step's result: its sub-agent's final status, step count,
   *  duration and summary. Live AND persisted — the only copy of the outcome
   *  after the stream (subagent_progress is never persisted). */
  subagent?: Pick<SubAgentProgress, 'status' | 'toolUses' | 'durationMs' | 'summary' | 'background'>;
}

/** Live status of a sub-agent, from the backend `subagent_progress` event
 *  (SDK Task* messages). Keyed by the Agent tool_use it runs under. Never
 *  persisted: kept on the streaming Message (`subAgentProgress`); the final
 *  state rides the Agent result (`ToolResultContent.subagent`). */
export interface SubAgentProgress {
  parentToolUseId: string;
  /** The SDK task id: what POST /chat/stop/{session}/task/{taskId} stops. */
  taskId?: string;
  status: 'running' | 'completed' | 'failed' | 'stopped' | (string & {});
  description?: string;
  lastToolName?: string;
  toolUses?: number;
  durationMs?: number;
  summary?: string;
  /** It was started in the background (run_in_background): set on the stored
   *  result once its end was written (backend core/background_agents.py). */
  background?: boolean;
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
  /**
   * The user's submitted answers, keyed by question text → selected label(s).
   * Written onto the block at submit time (ChatPage.handleAnswerQuestion) so the
   * renderer can show a read-only "answered" summary instead of a disabled form.
   * Absent while the question is still pending. Persisted on the block (not in
   * component state) so the summary survives tab-switch + store reconcile.
   */
  answers?: Record<string, string>;
}

export interface ThinkingContent {
  type: 'thinking';
  thinking: string;
  /** Marks this block as authoritative (from an assistant event), not provisional (from streaming delta). */
  _confirmed?: boolean;
  /** A provisional block's streamed block key (StreamEvent.block). */
  _streamBlock?: string;
}

export interface CmdPermissionContent {
  type: 'cmd_permission_request';
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  options?: string[];
  /** Set after user makes a decision — renders decided state inline. `expired`
   *  (backend core/permission_prompt, at read time): no decision was made and no
   *  hook waits on it any more (the turn stopped, the process restarted, it timed
   *  out) — never shown as live buttons. */
  decision?: 'approve' | 'deny' | 'expired';
}

export interface EscalationOption {
  label: string;
  description: string;
  recommended?: boolean;
}

export interface EscalationContent {
  type: 'escalation';
  id: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  reason: string;
  options: EscalationOption[];
  status: 'pending' | 'resolved';
  resolution?: string;
}

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
}

/**
 * A pipeline stage that run.json records as finished — written by the BACKEND
 * (backend/core/pipeline_stage_events.py) when an artifact_cli call returns, never
 * by the model. Persisted like any assistant content; keys arrive camelCase as-is.
 * `ordinal` is the progress display's circled number (①–⑩; ④/⑧ are gates, so
 * build is ⑤ and deliver ⑨). `summary` is model-written stage text: render plain.
 */
export interface PipelineStageContent {
  type: 'pipeline_stage';
  runId: string;
  stage: string;
  /** `in_progress`: the stage STARTED (its row opens here); a later `completed` /
   *  `skipped` block for the same run + stage updates that row in place. Older
   *  sessions have only the terminal block. */
  status: 'completed' | 'skipped' | 'in_progress';
  ordinal: number | null;
  /** 1-based index in the run's profile stage list (null: stage not in the profile). */
  position: number | null;
  total: number;
  profile: string | null;
  next: string | null;
  nextOrdinal: number | null;
  gate: { name: string; verdict: string } | null;
  summary: string | null;
  /** ISO time of the transition (an `in_progress` block: when the stage started). */
  at: string;
  /** The run document this stage wrote (REQUIREMENTS / DESIGN_SPEC / REVIEW,
   *  backend/core/pipeline_stage_events.py STAGE_DOCS) — on a completed row only. Kept for older rows: `docs`
   *  lists every document (DELIVER: REVIEW, FIXING_SPEC, REPORT). */
  doc?: PipelineDocRef | null;
  docs?: PipelineDocRef[] | null;
}

/** A run document on disk: `path` workspace-relative, `absolutePath` to open it. */
export interface PipelineDocRef {
  name: string;
  path: string;
  absolutePath: string;
  /** The document's first section (pipeline_stage_events.doc_excerpt): what the card shows folded. */
  excerpt?: string;
}

/** One of a finished run's four documents (pipeline_stage_events.summary_block). */
export interface PipelineSummaryDoc {
  name: string;
  purpose: string | null;
  path: string | null;
  absolutePath: string | null;
  /** The run's profile produces it (research has no design spec). */
  required: boolean;
  /** Why it is not there: skipped by the profile / stage, or missing. */
  reason: string | null;
  /** REVIEW.md only: experts, findings, findings settled. */
  stat?: { experts: number; findings: number; settled: number };
}

/** A run's execution summary — written by the BACKEND once, when run.json reaches an
 *  end status (a direct run too): how it ended, whether it landed, the run's documents. */
export interface PipelineSummaryContent {
  type: 'pipeline_summary';
  runId: string;
  profile: string | null;
  /** How the run ended: completed, abandoned, cancelled or failed (older blocks: absent). */
  status?: string | null;
  /** How the run's change landed: landed, human_override, not_landed, no_code (older
   *  blocks: absent). The pipeline gives no push verdict (XG 2026-10-26). */
  landing?: string | null;
  /** Why the change did not land (landing not_landed only). */
  notLandedReason?: string | null;
  /** What was asked (the run's requirement), what changed, where it landed. */
  intent?: string | null;
  solved?: { before: string; now: string } | null;
  landedSha?: string | null;
  stagesDone: number;
  docs: PipelineSummaryDoc[];
  at: string;
  /** Pipeline v1: the run's five-line audit card, rendered by the backend — shown as is
   *  when the block has no `audit` rows (older blocks). */
  auditCard?: string | null;
  /** The card's five rows as data (run_protocol.audit.card_rows; design
   *  2026-10-26-turn-cards-unified.md §5.1). */
  audit?: Array<{ dim: string; verdict: string; detail: string }> | null;
  /** Every repo's landed commit (a run can land several; landedSha is the last). */
  commits?: Array<{ repo: string; sha: string }> | null;
}

/**
 * A pipeline run's alignment — written by the BACKEND once per run per session
 * (pipeline_stage_events.run_block): the run's requirement (the AI's restatement of
 * the ask) and its planned stages. Model-written text: render plain.
 */
export interface PipelineRunContent {
  type: 'pipeline_run';
  runId: string;
  profile: string | null;
  stages: string[];
  requirement: string;
  /** The stage in progress when the run was announced (a resumed run, mid-way). */
  current?: string | null;
  /** The run's plan as the chat lists it: every stage and every gate the profile
   *  runs, in order (backend pipeline_stage_events.checklist). Empty until EVALUATE
   *  sets the profile; a later block of the same run then carries it. */
  checklist?: Array<{ stage: string; gate: boolean }>;
  at: string;
}

/** One thing in a run that needs a human judgement (structured fields only). */
export type PipelineAttentionItem =
  | { kind: 'self_answered'; id: string | null; question: string | null; answer: string | null; reason: string | null }
  | { kind: 'assumption'; id: string | null; statement: string | null }
  | { kind: 'gate'; name: string; verdict: string; detail: string | null }
  /** The run's change is not on main. `reasons`: the not-landed reason. */
  | { kind: 'not_landed'; reasons: string[]; reasonCodes?: (string | null)[]; detail?: string }
  /** Older blocks only (before 2026-10-26, when the pipeline still gave a push verdict):
   *  rendered as its reasons, with no verdict heading. */
  | { kind: 'not_push_ready'; reasons: string[]; reasonCodes?: (string | null)[]; detail?: string };

/** What in a run needs the user — written by the BACKEND with the DELIVER row
 *  (pipeline_stage_events.attention_block); absent when nothing does. */
export interface PipelineAttentionContent {
  type: 'pipeline_attention';
  runId: string;
  items: PipelineAttentionItem[];
  /** Items beyond the cap that are not listed. */
  more: number;
  at: string;
}

/** A file this turn produced (a content / knowledge file_changed). */
export interface TurnDeliverable {
  path: string;
  absolutePath: string;
  /** The emit gate's kind (backend router _DELIVERABLE_KINDS). */
  kind: 'content' | 'knowledge' | 'external-diff' | 'external-nodiff';
  operation: 'written';
}

/** One product of one call (backend core/turn_products, design
 *  2026-10-26-session-products.md §2): a file it wrote or a remote action it did,
 *  inserted by the backend after the call's successful result. camelCase as sent. */
export type TurnProductContent = {
  type: 'turn_product';
  toolUseId: string;
  parentToolUseId?: string;
} & (
  | { product: 'file'; path: string; absolutePath: string; fileKind: TurnDeliverable['kind'] }
  | { product: 'remote'; tool: string; action: string; target: string; url?: string }
);

/** A Swarm Out dispatch: what went to a Hive, or why nothing did (backend
 *  core/swarm_out_card, design docs/design/2026-10-30-swarm-out-dispatch-card.md §3).
 *  Written by the backend after the dispatch call's result, from this machine's
 *  dispatch log row, never from model text. camelCase as sent. */
export interface SwarmOutDispatchContent {
  type: 'swarm_out_dispatch';
  /** false: refused, nothing was sent (`error` says why) */
  ok: boolean;
  /** not ok, but the Hive may have taken it: it named no session (no answer in time, or the connection ended) */
  maybeSent?: boolean;
  dispatchId: string | null;
  sessionId: string | null;
  title: string;
  brief: string | null;
  hive: string | null;
  /** pick_hive's code: named / only_free / idle / least_busy:<n> */
  hiveReason: string | null;
  brain: string | null;
  mode: string | null;
  profile: string | null;
  /** how the task runs, chosen at dispatch (design 2026-10-30-run-spec §3); null on an
   *  older row */
  execution?: 'native' | 'pipeline' | null;
  /** refused at intake: nothing was asked of a Hive (dispatch-center §2.3);
   *  ``needsPrivate``: because it needs a private network; ``task``: the task, to do it
   *  on this machine instead */
  refused?: boolean;
  needsPrivate?: boolean;
  task?: string | null;
  executionWhy?: string | null;
  revision: string | null;
  /** what the Brain sync did: up to date / synced / restored … */
  brainSync: string | null;
  /** ``access``: how the Hive prepared it (read-only worktree or its branch); null on an older row */
  repos: { name: string; ref: string | null; sha: string | null; dirty: number; access?: 'read' | 'write' | null }[];
  acceptance: string | null;
  delivery: string | null;
  error: string | null;
  at: string;
  /** the same card as plain lines (a channel reply) */
  text: string;
}

/** The turn's products — written by the BACKEND just before `result`
 *  (SessionRouter._persist_stream_event). */
export interface TurnDeliverablesContent {
  type: 'turn_deliverables';
  files: TurnDeliverable[];
  /** files the block does not list (the backend keeps the first 12) */
  omitted?: number;
}

/** One file a turn changed in a code repo (core/turn_changes). */
export interface TurnChangeFile {
  /** Repo-relative path. */
  path: string;
  absolutePath: string;
  repo: string;
  status: 'added' | 'modified' | 'deleted';
  /** Lines; null for a binary file. */
  added: number | null;
  deleted: number | null;
  binary: boolean;
  /** False when the change is too large to offer as a diff ("file only"). */
  diffable: boolean;
  /** Where the diff is read on demand (GET /api/chat/turn-diff): the repo whose
   *  object store holds the two change trees, and the trees. */
  gitRoot: string;
  start: string;
  end: string;
}

/** One landing of this turn: a session worktree branch merged into the checkout's
 *  branch, read from the stamp `session_worktree land` writes into git
 *  (core/session_landings). Its rows diff `onto..new` like a turn's rows. */
export interface TurnLanding {
  repo: string;
  gitRoot: string;
  /** the branch it was merged into (the checkout's branch, not always `main`) */
  dest: string;
  branch: string;
  onto: string;
  new: string;
  count: number;
  subjects: { sha: string; subject: string }[];
  /** `on`: still on `dest`; `off`: `dest` was reset or rewound since */
  state: 'on' | 'off';
  files: TurnChangeFile[];
  omitted?: number;
}

/** The turn's code changes — written by the BACKEND just before `result`, from a
 *  change-tree snapshot taken when the turn started (core/turn_changes), plus the
 *  landings this turn made. */
export interface TurnChangesContent {
  type: 'turn_changes';
  files: TurnChangeFile[];
  /** Files beyond the row cap that are not listed. */
  omitted?: number;
  turnId?: string;
  landed?: TurnLanding[];
  /** more landings may exist than the card lists (see Canvas) */
  landedTruncated?: boolean;
  /** landing records that did not hold against git */
  unverified?: number;
  /** repos whose landings could not be read, and why */
  unknown?: { repo: string; reason: string }[];
}

/** How a turn ended — written by the BACKEND at every started turn's end
 *  (core/turn_outcome). A display block the chat does not render; Swarm Out reads it
 *  from a Hive to tell a finished run from a failed one. */
export interface TurnOutcomeContent {
  type: 'turn_outcome';
  ended: 'completed' | 'turn_limit' | 'error' | 'crashed' | 'paused' | 'none' | string;
  subtype?: string | null;
  code?: string | null;
  message?: string | null;
  exception?: string | null;
  duration_ms?: number | null;
  num_turns?: number | null;
  cli_cost_total?: number | null;
  cli_pid?: number | null;
  at?: string;
  repos?: unknown[];
}

/** Why a turn's last call did not finish — written by the BACKEND when it restarts
 *  the AI process mid-turn or a newer message takes over (core/turn_notice). */
export interface TurnNoticeContent {
  type: 'turn_notice';
  /** restarted: the process was restarted, the call it ran was lost.
   *  superseded: a newer message resumed the conversation. */
  reason: 'restarted' | 'superseded' | string;
  /** For restarted: stalled / oom / timeout / ... */
  cause?: string;
  at?: string;
}

/** What a user Stop stopped — written by the BACKEND at the moment of the Stop
 *  (core/turn_stop): the calls that were running, the background agents it ends.
 *  Persisted, and returned by POST /chat/stop (the live route). */
export interface TurnStopContent {
  type: 'turn_stop';
  at?: string;
  calls: Array<{ toolUseId: string; name: string; elapsedMs?: number; description?: string;
    /** false: it was waiting for the user's approval and never started. */
    ran?: boolean;
    /** Files its command names as write targets that exist now: it may have
     *  written them, fully or partly (a stopped call keeps no output). */
    writes?: string[];
    /** An Agent's own transcript: its calls so far, no conclusion. */
    transcript?: { path: string; toolCalls?: number } }>;
  agents: Array<{ toolUseId: string; description?: string; status: string; background?: boolean; summary?: string;
    transcript?: { path: string; toolCalls?: number } }>;
  /** Rows beyond the cap that are not listed. */
  omitted?: number;
  /** false: no turn was running; only background agents were stopped (the turn
   *  this lands on had already ended and is not "stopped"). */
  turn?: boolean;
}

/** At the top of a turn the CLI started to answer finished background agents
 *  (backend core/background_agents.py): which ones, how they ended. */
export interface BackgroundFinishedContent {
  type: 'background_finished';
  agents: Array<{
    toolUseId?: string;
    description?: string;
    status: string;
    durationMs?: number;
    toolUses?: number;
    /** The same terminal summary persisted onto the original Agent result. */
    summary?: string;
  }>;
}

/** A Swarm Run's card in its lead run's chat (backend core/swarm_run_card, design
 *  docs/design/2026-10-31-swarm-run.md §5.9): written once, by the call that sent the
 *  run's first children; the card reads the live runs. */
export interface SwarmRunContent {
  type: 'swarm_run';
  runId: string;
  project: string | null;
}

export type ContentBlock = TextContent | ToolUseContent | ToolResultContent | AskUserQuestionContent | CmdPermissionContent | ThinkingContent | EscalationContent | PipelineStageContent
  | PipelineRunContent | PipelineAttentionContent | PipelineSummaryContent | TurnDeliverablesContent | TurnChangesContent
  | TurnNoticeContent | BackgroundFinishedContent | TurnStopContent | TurnProductContent
  | TurnOutcomeContent | SwarmOutDispatchContent | SwarmRunContent;

export interface Message {
  id: string;
  /** Which DB rows this message holds and at which transcript rev (backend
   *  `row_revs`, history reads only): a transcript change for one of them at or
   *  below that rev is already here (hooks/transcriptFeed skips it). */
  rowRevs?: Record<string, number>;
  role: 'user' | 'assistant' | 'system';
  content: ContentBlock[];
  timestamp: string;
  model?: string;
  /** When true, the message ends with a terminal error notice; prior content stays normal. */
  isError?: boolean;
  /** Backend error `code` for an isError message (e.g. TOOL_CALL_LEAK_UNRECOVERED,
   *  AGENT_TIMEOUT). Read by AssistantMessageView → ChatErrorMessage to pick the coded label +
   *  severity colour. ABSENT (undefined) → the amber "Interrupted" fallback. Set by
   *  the SSE error-event handler via `errorFieldsFromEvent`; a code-less error (a
   *  genuine connection drop) correctly leaves this undefined. */
  errorCode?: string;
  /** Backend-sanitized error detail for an isError message (rendered as muted text). */
  errorDetail?: string;
  /** Backend "suggested action" for an isError message (rendered as the yellow hint). */
  suggestedAction?: string;
  /** Ephemeral — renders "Queued" badge. Never persisted to DB. */
  isQueued?: boolean;
  /** Ephemeral, user messages only: why a queued message waits (the send's
   *  receipt, then the snapshot's ``queued_reason``): ``question`` / ``permission``
   *  name what to answer first; ``busy`` or absent keeps the plain label. */
  queuedReason?: 'question' | 'permission' | 'busy';
  /** Ephemeral, user messages only: written into the running turn and not read
   *  yet (steer "Waiting", design 2026-10-25-chat-steer-frontend.md §4). Once read,
   *  the row's own ``metadata.steered`` says so. */
  steerWaiting?: boolean;
  /** Ephemeral, user messages only: 'failed' = the backend never accepted this
   *  send (no `accepted` receipt; e.g. it was unreachable). Renders "Not sent ·
   *  Retry". A DB row for it (it was persisted after all) replaces the mark. */
  sendState?: 'failed';
  /** Backend correlation metadata — carries `client_id` so a reconcile can match
   *  this message to its DB counterpart EVEN AFTER its display `id` has been
   *  renamed from the optimistic `local-*` id to the canonical DB id. This is a
   *  hidden correlation key (never rendered); the display id remains the DB id.
   *  Without it, the client_id fallback (which indexes only `local-*` ids) is
   *  consumed on the first reconcile and a later mid-turn-cut reconcile
   *  duplicates the bubble (run_f62f4b80). */
  metadata?: { client_id?: string; [key: string]: unknown };
  /** Ephemeral — latest status per running sub-agent, keyed by its Agent
   *  tool_use id. Written by the `subagent_progress` stream event; dropped by
   *  the turn-end reconcile (the persisted steps + results then tell the story). */
  subAgentProgress?: Record<string, SubAgentProgress>;
}

export interface ChatRequest {
  agentId: string;
  message?: string;  // Optional if content is provided
  content?: ContentBlock[];  // Multimodal content array
  sessionId?: string;
  enableSkills?: boolean;
  enableMCP?: boolean;
  /** Request-time snapshot of the agent's own UI state (proprioception, SENSE) —
   *  open file + Canvas state + which nav overlay is open. Superset of the former
   *  file-only descriptor; serialized via utils/uiContext.toEditorContextPayload. */
  editorContext?: import('../utils/uiContext').UiContextSnapshot;
  /** Attached integrated-terminal output (P2) — read-only context for the agent.
   *  Set only when the user explicitly attaches a terminal (terminal → session). */
  terminalContext?: { bufferTail: string; cwd: string };
  /** Correlation ID for optimistic message dedup — echoed in result event.
   *  REQUIRED (run_c598f640): every send MUST carry a key. This is the OT01
   *  key-chain invariant enforced at COMPILE TIME — a keyless persisted row is
   *  the source of the reconcile-tail duplicate bubble, and a keyless send path
   *  (a new one added in a hook) would escape the regex source-scan guard (which
   *  only scans ChatPage.tsx). Making the field required makes "every send carries
   *  a key" structurally impossible to violate, not merely caught. Continuation
   *  paths (streamAnswerQuestion / streamCmdPermissionContinue) use SEPARATE
   *  request types and are unaffected; channels use the backend run_conversation
   *  path (not ChatRequest) and are a distinct keyless-for-life axis. */
  clientId: string;
}

// ============== File Attachment Types ==============

/** Classified file type for attachment processing. */
export type AttachmentType = 'image' | 'pdf' | 'document' | 'audio' | 'video' | 'text' | 'csv' | 'terminal';

/** How a file is delivered to the backend/Claude SDK. `terminal_context` is not a
 *  content block: it rides the request's terminalContext (utils/terminalAttachment). */
export type DeliveryStrategy = 'base64_image' | 'base64_document' | 'inline_text' | 'path_hint' | 'terminal_context';

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
  /** type 'terminal' only: the pieces added (oldest first, each with its shell's
   *  cwd), and whether older output was cut. textContent is rendered from them. */
  terminal?: { cwd: string; lines: number; truncated: boolean; pieces?: { cwd: string; text: string }[] };
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
  pdf: 23 * 1024 * 1024,         // 23MB  — base64 overhead (×4/3) → ~30.7MB, under 32MB Bedrock limit
  document: 23 * 1024 * 1024,    // 23MB  — same as PDF (saved via backend, not sent to Claude API)
  audio: 500 * 1024 * 1024,      // 500MB — path_hint only, file stays local
  video: 1024 * 1024 * 1024,     // 1GB   — path_hint only, file stays local
  text: 5 * 1024 * 1024,         // 5MB   — path_hint saves to Attachments/ for large files
  csv: 5 * 1024 * 1024,          // 5MB   — path_hint saves to Attachments/ for large files
  terminal: 64 * 1024,           // terminal output: never a file; cut to 16000 chars (utils/terminalAttachment)
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

// NOTE: the `context_compacted` SSE event (CLI-native autocompact) is now rendered
// as a PERSISTENT in-stream system message (role=system, id prefix 'compact-'),
// appended to the per-tab MessageStore by useChatStreamingLifecycle and rendered as
// an informational divider by TabView. The former transient-toast types
// (`CompactionNotice` + `compactionToastPayload`) were removed 2026-10-14 — a 5s
// auto-dismiss toast left no reviewable trace of a current-session state change
// (XG: "不能用toast ... 针对当前session 做的"). `message` (COMPACT_NOTICE_TEXT) is a
// fixed backend constant; the frontend is a pure projection of it.

/** Pre-first-token step of a turn (backend core/turn_phase.py). */
export type TurnPhase = 'preparing' | 'starting' | 'waiting';
/** Whether a turn of a session is in progress and in which step (backend
 *  core/session_state ACTIVITIES, docs/design/chat-turn-liveness.md S1). */
export type SessionActivity =
  | 'idle' | 'preparing' | 'starting' | 'waiting' | 'streaming' | 'waiting_input' | 'stopping';

export interface StreamEvent {
  type: 'assistant' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'reconnecting' | 'session_resuming' | 'ask_user_question' | 'session_start' | 'session_cleared' | 'accepted' | 'cmd_permission_request' | 'cmd_permission_decision' | 'cmd_permission_acknowledged' | 'heartbeat' | 'agent_activity' | 'tool_invocation' | 'capability_activated' | 'sources_updated' | 'summary_updated' | 'context_warning' | 'context_compacting' | 'context_compacted' | 'compaction_guard' | 'mcp_health_warning' | 'recovery_exhausted' | 'still_working' | 'turn_phase' | (string & {});
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
  // Long-turn "still_working" notice fields
  elapsedSeconds?: number;
  // subagent_progress fields (`description` is the TSCC field below)
  parentToolUseId?: string;
  taskId?: string;
  status?: string;
  lastToolName?: string;
  toolUses?: number;
  summary?: string;
  // Error fields
  error?: string;
  message?: string;
  code?: string;
  detail?: string;
  suggestedAction?: string;
  // Queue timeout / SESSION_BUSY retry payload.
  // Root-1 SSOT Phase 2 (L2/2A): the backend now PREFERS to persist the message
  // server-side as pending (sent=0) and returns pendingSeq/pendingId instead of
  // retryPayload. retryPayload is sent ONLY as the persist-failed fallback. When
  // pendingSeq is present the frontend MUST NOT re-queue/re-send — the server-side
  // drain worker delivers it (a re-send would double-deliver). The existing
  // re-queue path no-ops safely on absent retryPayload.
  retryPayload?: {
    sessionId: string;
    agentId: string;
    userMessage: string | null;
    content: unknown[] | null;
    /** the handed-back send's client_id: re-send with it (backend core/send_requests) */
    clientId?: string | null;
    /** the handed-back send's attached terminal output (backend _hand_back_send) */
    terminalContext?: { bufferTail: string; cwd: string } | null;
  };
  /** Server-side pending message sequence (SESSION_BUSY / QUEUE_TIMEOUT). When
   *  present, the message is durably queued server-side and will auto-drain —
   *  do NOT re-send from the frontend. */
  pendingSeq?: number;
  /** DB id of the server-side pending message (pairs with pendingSeq). */
  pendingId?: string;
  // `accepted` (the send's receipt, backend core/send_requests): the message is the
  // server's. `duplicate`: this client_id was sent before; `queued`: its row still
  // waits to be delivered.
  duplicate?: boolean;
  queued?: boolean;
  userMessageId?: string;
  clientId?: string;
  // Context warning fields (context_warning event = the backend context-usage
  // snapshot, hooks/contextUsage.ts ContextUsageSnapshot). pct is null when the
  // backend has no context to report (source 'none').
  level?: 'ok' | 'warn' | 'critical';
  pct?: number | null;
  tokensEst?: number;
  source?: 'live' | 'transcript' | 'none';
  basis?: string;
  window?: number | null;
  measuredAt?: number;
  // MCP health warning fields (mcp_health_warning event)
  missing_servers?: string[];
  // Compaction guard fields (compaction_guard event)
  subtype?: string;
  contextPct?: number;
  patternDescription?: string;
  // Context compaction fields (context_compacting / context_compacted events).
  // context_compacting is emitted LIVE when the CLI starts (phase 'started') or
  // fails (phase 'failed'); context_compacted when it finishes, with sizes.
  trigger?: 'manual' | 'auto';
  /** context_compacting: 'started' | 'failed'. turn_phase (backend core/turn_phase.py):
   *  the pre-first-token step — 'preparing' | 'starting' | 'waiting'. */
  phase?: 'started' | 'failed' | TurnPhase;
  preTokens?: number;
  postTokens?: number;
  /** context_compacted: the stored compaction record (snake_case wire shape,
   *  backend core/compact_summary.wire) — the same object the history API puts
   *  in the synthetic `compact-<id>` message's metadata.compaction. Read it
   *  ONLY through pages/chat/compaction.ts `toCompactionInfo`. */
  compaction?: Record<string, unknown>;
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
  /** the streamed block a start/delta belongs to ("<message>:<index>", backend) */
  block?: string;
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
  /** SHA-256 revision used for optimistic saves of governed files. */
  revision: string;
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
export type ChannelStatus = 'active' | 'inactive' | 'error' | 'starting' | 'failed' | 'auth_error';
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
  /** Directory children. undefined = file/not-applicable, null = truncated (lazy-loadable), [] = empty dir. */
  children?: TreeNode[] | null;
  /** Git status of this file/folder, if known. */
  gitStatus?: GitStatus;
  /** True when this entry is a filesystem symlink (e.g., linked project). */
  isSymlink?: boolean;
  /** Directories: how many uncommitted entries sit under it (local main). */
  changedCount?: number;
  /** A dot entry in a repo location: hidden unless the user shows hidden items. */
  hidden?: boolean;
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
}

/** System prompt metadata returned by the system-prompt endpoint. */
export interface SystemPromptMetadata {
  files: SystemPromptFileInfo[];
  totalTokens: number;
  fullText: string;
  /** Dynamic per-model token budget actually applied (100K/50K/30K tier).
   *  Absent or 0 means the build did not report one — render "unknown" rather
   *  than assuming a tier, or the gauge lies by 2-3x on smaller models. */
  effectiveTokenBudget?: number;
  /** Fail-loud degradation reason from prompt assembly. Empty/absent when the
   *  prompt assembled completely. Optional because the SSE event spreads the
   *  backend metadata dict verbatim, which omits the key on a healthy build,
   *  while the HTTP endpoint always emits it (schema default ''). */
  degraded?: string;
}

/** One structured recalled hit — real hit that fed the injected block. */
export interface RecallHit {
  domain: string;
  source: string;
  score: number;
  hasScore: boolean;
  method: string;
  text: string;
}

/** Recalled-knowledge snapshot returned by the recall endpoint (read-only). */
export interface RecallSnapshot {
  ran: boolean;
  hits: RecallHit[];
  body: string;
  tokens: number;
  latencyMs: number;
  keywords: string[];
}

/** A single detector's verdict from the prompt security scan. */
export interface SecurityFinding {
  detector: string;
  severity: 'critical' | 'high' | 'medium' | 'info';
  status: 'pass' | 'warn';
  count: number;
  detail: string;
}

/** Structured verdict returned by the security-scan endpoint. */
export interface SecurityScanResult {
  grade: string;
  findings: SecurityFinding[];
  critical: number;
  high: number;
  medium: number;
  info: number;
  scannedFiles: number;
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

// ============== Streaming-State Read API (Root-1 SSOT Phase 3) ==============

/**
 * Per-session entry from `GET /api/chat/sessions/streaming-state` — the
 * authoritative backend mirror the frontend reads instead of inferring.
 *
 * Backend source: `chat.py:776-786` (snake_case). This is the camelCase mirror.
 * Phase 3 widened this from `{streaming, state}` to carry the 4 SSOT fields the
 * backend has emitted since Phase 2 but the frontend previously dropped:
 * `waitingInput`, `pendingCount`, `pendingQuestion`, `lastDrainedSeqs`.
 */
/** A command-permission prompt as the backend holds it (streaming-state). */
export interface PendingPermission {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  reason: string;
  options: string[];
}

export interface StreamingStateEntry {
  /** True iff backend state === 'streaming'. */
  streaming: boolean;
  /** Raw backend state value: 'cold' | 'idle' | 'streaming' | 'waiting_input'. */
  state: string;
  /** True iff state === 'waiting_input' (an AskUserQuestion / permission is open). */
  waitingInput: boolean;
  /** Count of server-side persisted-but-unsent (pending) messages for this session. */
  pendingCount: number;
  /** the `pending` list holds every queued row (an explicit count says so);
   *  absent = unknown, so a missing row means nothing */
  pendingComplete?: boolean;
  /**
   * Authoritative AskUserQuestion payload — lets the frontend re-surface a
   * question whose original SSE event was lost. Null unless waiting_input.
   */
  pendingQuestion: { toolUseId: string; questions: AskUserQuestion[] } | null;
  /**
   * Authoritative command-permission prompt the backend is blocked on — lets the
   * frontend re-surface a prompt whose live event the tab never got (a CLI turn
   * after a background agent, another tab, a reload). Null unless waiting_input.
   */
  pendingPermission?: PendingPermission | null;
  /** Pending-message seqs the server drained in its last drain pass. */
  lastDrainedSeqs: number[];
  /** Whether a turn is in progress (docs/design/chat-turn-liveness.md S1), and
   *  its seq: a larger seq is newer, whichever channel (push or poll) brought it.
   *  Absent from a backend older than the field. */
  activity?: SessionActivity;
  activitySeq?: number;
  /** The session's queued messages, oldest first (backend session_pending.list_pending,
   *  capped at 50): the one queue (docs/design/chat-turn-liveness.md S3). */
  pending?: PendingQueueEntry[];
  /** Messages written into the running turn and not read yet (chat-steer D5);
   *  absent when there are none. */
  steering?: SteeringEntry[];
  /** The running turn's segment keys (``{client_id}-asst``), in order; absent
   *  when the turn has had no steer. */
  segments?: string[];
  /** Why the queue waits (``question`` / ``permission`` / ``busy``), while it
   *  holds a message and the steer switch is on. */
  queuedReason?: 'question' | 'permission' | 'busy';
}

/** Turn event ``user_steered`` (backend TurnWriter, design chat-steer D4): the CLI
 *  read a steer here; what follows belongs to the segment ``rowKey``. */
export interface SteerReadEvent {
  type: 'user_steered';
  clientId: string;
  outcome: 'folded' | 'opened';
  rowKey: string;
  segments?: string[];
  turnId?: string;
}

/** One steer of a session, written into its running turn, not read yet. */
export interface SteeringEntry {
  clientId: string;
  preview: string;
}

/** One queued message of a session as the server holds it. ``claimed``: a drain
 *  has taken it (being delivered, no longer cancellable). */
export interface PendingQueueEntry {
  id: string;
  clientId: string | null;
  pendingSeq: number;
  preview: string;
  createdAt: string;
  claimed: boolean;
}

// ============== Health Monitor Types ==============

// 'degraded' (run_13094a88): the daemon PROCESS is alive but a /health probe was
// briefly missed (a transient >3s event-loop stall). The UI shows a reconnecting
// banner but keeps chat inputs ENABLED — only 'disconnected' (a proven death, or a
// persistent outage) disables them. This is the false-offline root-fix: a single
// transient stall can no longer nuke the UI.
export type BackendStatus = 'connected' | 'disconnected' | 'initializing' | 'degraded';

/** AWS credential status reported by GET /health (`auth` field).
 *  - 'valid':   STS GetCallerIdentity succeeded
 *  - 'expired': definitive — user must re-auth (mwinit -f)
 *  - 'unknown': non-definitive (network/timeout/startup) — never blocks, no banner */
export type AuthStatus = 'valid' | 'expired' | 'unknown';

export interface HealthState {
  status: BackendStatus;
  /** Credential status from /health. Undefined until the first poll that
   *  carried an auth field; treated as 'unknown' (no banner) when absent. */
  auth?: AuthStatus;
  lastCheckedAt: number | null;
  consecutiveFailures: number;
  /** Why it is not 'connected' (hooks/backendIssue.ts): a short label for the
   *  header and a sentence for the toast / tooltip. Absent when connected. */
  issue?: { kind: string; cause: string; short: string; detail: string };
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



// ============== Todo Types (canonical Todo entity) ==============

export * from './todo';
