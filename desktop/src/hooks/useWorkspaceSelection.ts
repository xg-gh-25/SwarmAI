import { useQuery } from '@tanstack/react-query';
import { swarmWorkspacesService } from '../services/swarmWorkspaces';

/**
 * Simplified hook — returns the single default SwarmWS path.
 * No workspace switching logic; there is only one workspace.
 */
export function useWorkspaceSelection() {
  const { data: workspaces } = useQuery({
    queryKey: ['swarmWorkspaces'],
    queryFn: () => swarmWorkspacesService.list(),
  });

  const defaultWorkspace = workspaces?.find(w => w.isDefault) ?? workspaces?.[0];
  const workDir = defaultWorkspace?.filePath ?? null;

  return {
    selectedWorkspace: defaultWorkspace ?? null,
    workDir,
  };
}
