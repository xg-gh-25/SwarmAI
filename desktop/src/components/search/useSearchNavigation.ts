/**
 * Hook for navigating to search result detail views.
 * Requirements: 38.7, 38.8
 */
import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import type { SearchResultItem } from '../../services/search';

/** Map entity types to their route paths */
const ENTITY_ROUTE_MAP: Record<string, string> = {
  todos: '/signals',
  tasks: '/execute',
  planItems: '/plan',
  communications: '/communicate',
  artifacts: '/artifacts',
  reflections: '/reflection',
};

export function useSearchNavigation() {
  const navigate = useNavigate();

  const navigateToResult = useCallback((item: SearchResultItem) => {
    const basePath = ENTITY_ROUTE_MAP[item.entityType];
    if (basePath) {
      // Navigate to the section page with workspace context
      const params = new URLSearchParams();
      if (item.workspaceId) {
        params.set('workspaceId', item.workspaceId);
      }
      const queryString = params.toString();
      navigate(`${basePath}${queryString ? `?${queryString}` : ''}`);
    }
  }, [navigate]);

  return { navigateToResult };
}
