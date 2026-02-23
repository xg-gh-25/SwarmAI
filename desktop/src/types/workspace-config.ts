export interface WorkspaceSkillConfig {
  id: string;
  workspaceId: string;
  skillId: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceMcpConfig {
  id: string;
  workspaceId: string;
  mcpServerId: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceKnowledgebaseConfig {
  id: string;
  workspaceId: string;
  sourceType: string;
  sourcePath: string;
  displayName: string;
  metadata?: Record<string, unknown>;
  excludedSources?: string[];
  createdAt: string;
  updatedAt: string;
}

export interface AuditLogEntry {
  id: string;
  workspaceId: string;
  changeType: string;
  entityType: string;
  entityId: string;
  oldValue?: string;
  newValue?: string;
  changedBy: string;
  changedAt: string;
}

export interface PolicyViolation {
  entityType: string;
  entityId: string;
  entityName: string;
  reason: string;
  suggestedAction: string;
}
