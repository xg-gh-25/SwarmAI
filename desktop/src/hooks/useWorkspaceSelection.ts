/**
 * Singleton workspace selection hook.
 *
 * Returns a hardcoded SwarmWS workspace reference. The multi-workspace
 * selection logic and ``swarmWorkspacesService`` dependency have been removed.
 * Once the new workspace service is created (task 13.2), this hook will
 * fetch the actual config from ``GET /api/workspace``.
 *
 * Exports:
 * - ``useWorkspaceSelection`` — Hook returning ``{ selectedWorkspace, workDir }``
 */

/**
 * Returns the singleton SwarmWS workspace info.
 * No workspace switching — there is only one workspace.
 */
export function useWorkspaceSelection() {
  // Hardcoded singleton until the new workspace service (task 13.2) is wired
  const selectedWorkspace = {
    id: 'swarmws',
    name: 'SwarmWS',
    filePath: '',
    context: '',
    isDefault: true,
    createdAt: '',
    updatedAt: '',
  };

  return {
    selectedWorkspace,
    workDir: selectedWorkspace.filePath || null,
  };
}
