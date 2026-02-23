import { useParams, useSearchParams } from 'react-router-dom';

/**
 * Hook to get workspaceId from either URL params (/workspaces/:workspaceId/...)
 * or search params (?workspaceId=...).
 *
 * URL params take precedence over search params.
 * When a defaultValue is provided, the return type is guaranteed to be string.
 *
 * Requirements: 15.2, 15.3
 */
export function useWorkspaceId(defaultValue: string): string;
export function useWorkspaceId(defaultValue?: undefined): string | undefined;
export function useWorkspaceId(defaultValue?: string): string | undefined {
  const { workspaceId: urlWorkspaceId } = useParams<{ workspaceId: string }>();
  const [searchParams] = useSearchParams();
  const searchWorkspaceId = searchParams.get('workspaceId');

  return urlWorkspaceId || searchWorkspaceId || defaultValue;
}
