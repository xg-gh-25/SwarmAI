# Requirements Document — SwarmWS Explorer UX (Cadence 3 of 4)

## Introduction

This is **Cadence 3 of 4** for the SwarmWS redesign. It covers the Workspace Explorer UX redesign: semantic zone grouping, header and layout changes, progressive disclosure, focus mode, search, visual design, and scalability. This cadence depends on Cadence 1 (`swarmws-foundation`) and Cadence 2 (`swarmws-projects`) being completed first.

See the parent spec at `.kiro/specs/swarmws-redesign/requirements.md` for the full glossary and architectural context.

## Cross-References

This spec is part of the SwarmWS Redesign, split into 4 implementation cadences:

| Cadence | Spec | Requirements | Focus |
|---------|------|-------------|-------|
| 1 | `swarmws-foundation` | 1, 2, 3, 6, 7, 8, 17, 19, 20, 23, 24, 25, 28, 29, 30 | Single workspace, folder structure, Knowledge domain, backend data model, dead code removal |
| 2 | `swarmws-projects` | 4, 5, 18, 21, 22, 27, 31, 32 | Project CRUD, template, metadata, frontend types/services |
| 3 | `swarmws-explorer-ux` | 9, 10, 11, 12, 13, 14, 15 | Workspace Explorer UX redesign |
| 4 | `swarmws-intelligence` | 16, 26, 33 | Context assembly, chat threads, preview API |

Parent spec: `.kiro/specs/swarmws-redesign/requirements.md`

## Glossary

- **SwarmWS**: The single, non-deletable root workspace. Serves as the persistent memory container for all SwarmAI work. Located at `{app_data_dir}/SwarmWS`.
- **Project**: A self-contained execution and knowledge container under `Projects/`. Each project has its own context files, instructions, chats, research, and reports. Replaces the concept of custom workspaces.
- **Knowledge**: The shared knowledge domain at the workspace root representing workspace-level shared semantic memory. Contains `Knowledge Base/` for durable reusable assets, `Notes/` for evolving working knowledge, and `Memory/` for persistent semantic memory distilled from user interactions. Replaces the former `Artifacts/` and `Notebooks/` folders.
- **Knowledge_Base**: A subfolder under `Knowledge/` for durable, reusable, high-confidence knowledge assets.
- **Notes**: A subfolder under `Knowledge/` for evolving working knowledge and exploratory documents.
- **Memory**: A subfolder under `Knowledge/` for persistent semantic memory automatically distilled from user chat history and interactions. Contains long-term, user-specific memory reflecting preferences, patterns, recurring goals, and accumulated insights derived from conversations.
- **Context_L0**: An ultra-concise semantic abstract file (~1000 tokens) used for fast relevance detection and routing decisions. Named `context-L0.md`.
- **Context_L1**: A structured overview file (~4k tokens) describing scope, structure, goals, key knowledge, and relationships. Named `context-L1.md`.
- **Depth_Guardrail**: A maximum folder nesting limit enforced by the system to maintain usability and agent reasoning consistency.
- **System_Managed_Item**: A file or folder that is created and maintained by the system. System_Managed_Items cannot be deleted or structurally renamed by users. Users may edit the content of system-managed files.
- **User_Managed_Item**: A file or folder created by the user. User_Managed_Items support full CRUD operations within depth guardrail limits.
- **Semantic_Zone**: A visual grouping in the workspace explorer that organizes the tree into two conceptual areas: Shared Knowledge and Active Work.
- **Focus_Mode**: A project-centric explorer view that auto-expands the active project and keeps Knowledge visible.
- **Project_Metadata**: A hidden `.project.json` file inside each project directory containing system metadata (creation date, status, tags).
- **Workspace_Explorer**: The middle-column UI component that displays the SwarmWS tree structure with semantic zone grouping.
- **Sample_Data**: Realistic onboarding content pre-populated in SwarmWS on first launch to demonstrate intended usage of Knowledge and project structure.

## Requirements

### Requirement 9: Workspace Explorer Redesign — Header and Layout

**User Story:** As a knowledge worker, I want the workspace explorer to clearly present SwarmWS as my single workspace with easy search access, so that I can navigate my work efficiently.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display "SwarmWS" as the header title, replacing the previous "Explorer" header.
2. THE Top_Bar SHALL display a centered global search bar for fuzzy search across projects, folders, and files within SwarmWS (consistent with the three-column layout spec where the Top Bar spans the full application width above all three columns).
3. THE Workspace_Explorer SHALL remove the workspace dropdown selector.
4. THE Workspace_Explorer SHALL remove the "Show Archived Workspaces" checkbox.
5. THE Workspace_Explorer SHALL remove the Global|SwarmWS toggle switch.
6. THE Workspace_Explorer SHALL remove the "New Workspace" button.
7. THE Workspace_Explorer SHALL remove the add-context area that previously appeared under the workspace selector.

### Requirement 10: Workspace Explorer — Semantic Zone Grouping

**User Story:** As a knowledge worker, I want the workspace tree organized into semantic zones, so that I can quickly distinguish between shared knowledge and my active projects.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display the workspace tree grouped into two Semantic_Zones with visual separators:
   - "Shared Knowledge" — containing `Knowledge/`
   - "Active Work" — containing `Projects/`
2. THE Workspace_Explorer SHALL display zone labels as subtle, non-interactive separators between groups.
3. THE Workspace_Explorer SHALL display root-level files (`system-prompts.md`, `context-L0.md`, `context-L1.md`) above the first zone separator.
4. THE Workspace_Explorer SHALL collapse all subfolders by default on initial load.
5. THE Workspace_Explorer SHALL persist expand/collapse state per session.

### Requirement 11: Workspace Explorer — Progressive Disclosure

**User Story:** As a knowledge worker, I want the explorer to start simple and reveal detail on demand, so that I am not overwhelmed by the full workspace structure.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display only top-level sections and zone separators in the default collapsed view.
2. WHEN a folder is clicked, THE Workspace_Explorer SHALL expand or collapse that folder to show or hide its contents.
3. THE Workspace_Explorer SHALL use subtle expand/collapse animations (150–200ms duration).
4. THE Workspace_Explorer SHALL preserve scroll position when expanding or collapsing folders.
5. THE Workspace_Explorer SHALL lazy-load deep folder contents to maintain responsiveness.

### Requirement 12: Workspace Explorer — Focus Mode

**User Story:** As a knowledge worker, I want a Focus Mode that highlights my current project, so that I can concentrate on active work without distraction from other sections.

#### Acceptance Criteria

1. WHEN a user opens or selects a project, THE Workspace_Explorer SHALL auto-expand the selected project's tree.
2. WHEN Focus_Mode is active, THE Workspace_Explorer SHALL collapse non-active project trees.
3. WHEN Focus_Mode is active, THE Workspace_Explorer SHALL keep the `Knowledge/` folder visible (collapsed but accessible).
4. THE Workspace_Explorer SHALL provide a toggle control labeled "Focus on Current Project" to enable or disable Focus_Mode.
5. WHEN Focus_Mode is disabled, THE Workspace_Explorer SHALL restore the previous expand/collapse state.

### Requirement 13: Workspace Explorer — Search

**User Story:** As a knowledge worker, I want a global search bar in the explorer, so that I can quickly find projects, folders, and files by name.

#### Acceptance Criteria

1. THE Top_Bar SHALL display a centered global search bar (see Requirement 9, AC 2). Search results SHALL be reflected in the Workspace_Explorer tree.
2. THE Search SHALL support fuzzy matching across project names, folder names, and file names within the SwarmWS filesystem. THE Search scope SHALL NOT include DB-canonical entities (chat threads, ToDos, tasks).
3. WHEN search results are displayed, THE Workspace_Explorer SHALL auto-expand the path to each matched node.
4. THE Workspace_Explorer SHALL highlight matched nodes in the tree.
5. WHEN the search query is cleared, THE Workspace_Explorer SHALL restore the previous expand/collapse state.

### Requirement 14: Workspace Explorer — Visual Design

**User Story:** As a knowledge worker, I want the explorer to feel calm and readable, so that I can work without visual fatigue.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL use consistent indentation per depth level with optional indentation guides.
2. THE Workspace_Explorer SHALL use slight font-weight differences to distinguish hierarchy levels.
3. THE Workspace_Explorer SHALL use calm, neutral background tones with soft separators instead of heavy borders.
4. THE Workspace_Explorer SHALL reserve accent colors for User_Managed_Items only.
5. THE Workspace_Explorer SHALL use a minimal icon set (e.g., `+`, `⋯`) for actions, shown only on hover.
6. THE Workspace_Explorer SHALL use CSS variables in `--color-*` format for all colors (no hardcoded color values).

### Requirement 15: Workspace Explorer — Scalability

**User Story:** As a knowledge worker, I want the explorer to remain responsive even with hundreds of projects and thousands of files, so that performance does not degrade as my workspace grows.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL use virtualized tree rendering to handle large file trees efficiently.
2. THE Workspace_Explorer SHALL maintain smooth scrolling and interaction responsiveness with at least 500 visible tree nodes.
3. THE Workspace_Explorer SHALL use efficient state management to minimize re-renders when expanding or collapsing folders.