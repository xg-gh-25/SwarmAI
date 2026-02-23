export type ArtifactType = 'plan' | 'report' | 'doc' | 'decision' | 'other';

export interface Artifact {
  id: string;
  workspaceId: string;
  taskId?: string;
  artifactType: ArtifactType;
  title: string;
  filePath: string;
  version: number;
  createdBy: string;
  tags?: string[];
  createdAt: string;
  updatedAt: string;
}

export interface ArtifactCreateRequest {
  workspaceId?: string;
  taskId?: string;
  artifactType?: ArtifactType;
  title: string;
  filePath?: string;
  createdBy?: string;
  tags?: string[];
}

export interface ArtifactUpdateRequest {
  title?: string;
  artifactType?: ArtifactType;
  tags?: string[];
}
