import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { SwarmWorkspace } from '../types';
import { swarmWorkspacesService } from '../services/swarmWorkspaces';

interface UseWorkspaceSelectionOptions {
  selectedAgentId: string | null;
  onWorkspaceChange?: (workspace: SwarmWorkspace | null) => void;
}

interface UseWorkspaceSelectionReturn {
  selectedWorkspace: SwarmWorkspace | null;
  setSelectedWorkspace: (workspace: SwarmWorkspace | null) => void;
  swarmWorkspaces: SwarmWorkspace[];
  workDir: string | null;
  isRestoringWorkspace: boolean;
}

/**
 * Custom hook for managing workspace selection with localStorage persistence
 */
export function useWorkspaceSelection({
  selectedAgentId,
  onWorkspaceChange,
}: UseWorkspaceSelectionOptions): UseWorkspaceSelectionReturn {
  const [selectedWorkspace, setSelectedWorkspaceState] = useState<SwarmWorkspace | null>(null);
  const isRestoringWorkspaceRef = useRef(false);
  const prevWorkspaceIdRef = useRef<string | null | undefined>(undefined);

  // Fetch swarm workspaces
  const { data: swarmWorkspaces = [] } = useQuery({
    queryKey: ['swarmWorkspaces'],
    queryFn: swarmWorkspacesService.list,
  });

  // Wrapper to set selectedWorkspace and track if it's a user action
  const setSelectedWorkspace = useCallback((value: SwarmWorkspace | null, isRestoring = false) => {
    isRestoringWorkspaceRef.current = isRestoring;
    setSelectedWorkspaceState(value);
  }, []);

  // Load selected workspace from localStorage when agent changes
  useEffect(() => {
    if (selectedAgentId && swarmWorkspaces.length > 0) {
      const savedWorkspaceId = localStorage.getItem(`selectedWorkspaceId_${selectedAgentId}`);
      if (savedWorkspaceId) {
        const workspace = swarmWorkspaces.find((ws) => ws.id === savedWorkspaceId);
        if (workspace) {
          setSelectedWorkspace(workspace, true);
          return;
        }
      }
      // Auto-select default workspace if no saved selection
      const defaultWorkspace = swarmWorkspaces.find((ws) => ws.isDefault);
      if (defaultWorkspace) {
        setSelectedWorkspace(defaultWorkspace, true);
      }
    } else if (!selectedAgentId) {
      setSelectedWorkspace(null, true);
    }
  }, [selectedAgentId, swarmWorkspaces, setSelectedWorkspace]);

  // Persist selected workspace ID to localStorage when it changes
  useEffect(() => {
    if (selectedAgentId) {
      if (selectedWorkspace) {
        localStorage.setItem(`selectedWorkspaceId_${selectedAgentId}`, selectedWorkspace.id);
      } else {
        localStorage.removeItem(`selectedWorkspaceId_${selectedAgentId}`);
      }
    }
  }, [selectedAgentId, selectedWorkspace]);

  // Handle workspace change callback
  useEffect(() => {
    if (isRestoringWorkspaceRef.current) {
      isRestoringWorkspaceRef.current = false;
      prevWorkspaceIdRef.current = selectedWorkspace?.id ?? null;
      return;
    }

    const currentWorkspaceId = selectedWorkspace?.id ?? null;
    if (prevWorkspaceIdRef.current !== undefined && prevWorkspaceIdRef.current !== currentWorkspaceId) {
      prevWorkspaceIdRef.current = currentWorkspaceId;
      onWorkspaceChange?.(selectedWorkspace);
    } else {
      prevWorkspaceIdRef.current = currentWorkspaceId;
    }
  }, [selectedWorkspace, onWorkspaceChange]);

  // Derive workDir from selectedWorkspace for backward compatibility
  const workDir = selectedWorkspace?.filePath ?? null;

  return {
    selectedWorkspace,
    setSelectedWorkspace: (ws) => setSelectedWorkspace(ws, false),
    swarmWorkspaces,
    workDir,
    isRestoringWorkspace: isRestoringWorkspaceRef.current,
  };
}
