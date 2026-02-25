/**
 * Projects service — dedicated module for project CRUD and history operations.
 *
 * Re-exports ``projectService`` and conversion helpers from ``workspace.ts``
 * for a cleaner import path. Components can import from either module.
 *
 * Key exports:
 * - ``projectsService``           — Project CRUD + history (list, get, create, update, delete, getHistory, getProjectByName)
 * - ``projectToCamelCase``        — snake_case project API response → camelCase
 * - ``projectUpdateToSnakeCase``  — camelCase project update → snake_case
 * - ``historyEntryToCamelCase``   — snake_case history entry → camelCase
 */
export {
  projectService as projectsService,
  projectToCamelCase,
  projectUpdateToSnakeCase,
  historyEntryToCamelCase,
} from './workspace';
