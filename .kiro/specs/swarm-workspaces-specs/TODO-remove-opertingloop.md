I would update all the swarmws-* related specs to follow below "Prompt: Refactor SwarmWS Structure — Remove Operating Loop, Enhance Shared Knowledge, and Update SwarmWS Position

You are refactoring the SwarmWS workspace structure specification, initialization scaffolding, and related UI assumptions.

Apply the following changes strictly and consistently across:

Design specifications

Workspace initialization logic

Explorer UI structure assumptions

Migration rules

1. Objective

This refactor has three goals:

Remove the entire Operating Loop (System) concept and folders

Enhance the Shared Knowledge domain with new system default files

Update the positioning and structural role of SwarmWS in the UI and architecture

2. Remove Operating Loop Concept Completely

2.1 Remove Conceptual Model

Delete all references to:

“Operating Loop (System)”

“Daily Work Operating Loop”

The loop model:

Signals → Plan → Execute → Communicate → Reflection

These loops remain internal agent logic only and must not be exposed as filesystem structure.

2.2 Delete Operating Loop Folders

Remove the following folders from the canonical workspace structure:

Signals/ Plan/ Execute/ Communicate/ Reflection/ 

Also remove:

Depth guardrail rules specific to these folders

Explorer UI grouping labeled “Operating Loop (System)”

Any system scaffolding logic that auto-creates these folders

After this change, SwarmWS must no longer expose internal agent execution loops as visible directories.

3. Update SwarmWS Positioning (Critical)

3.1 New Positioning Principle

SwarmWS is no longer a “process-driven workspace”. It is now positioned as:

A persistent, local-first knowledge and project operating root for all user work.

SwarmWS should be understood as:

The single root workspace (non-deletable)

A structural container for:

Shared Knowledge domain

Active Projects domain

Global semantic context files

It should NOT be framed as a workflow pipeline.

3.2 Explorer Structural Role

The left Explorer must reflect this new positioning:

SwarmWS should appear as a stable structural root, not an operational dashboard.

Only long-term, structural memory containers should appear under SwarmWS.

4. Add Default System Files Under “Shared Knowledge”

Under the Knowledge/ domain (Shared Knowledge), add the following system-managed default files:

These files must be:

Non-deletable

Editable

Automatically created during workspace initialization

Knowledge/ ├── context-L0.md ├── context-L1.md ├── index.md └── knowledge-map.md 

5. Definitions of Shared Knowledge Default Files

5.1 context-L0.md

Ultra-concise semantic abstract (~100 tokens)

Used by agents for fast relevance detection and routing decisions across knowledge domains

5.2 context-L1.md

Structured overview (~2k tokens)

Describes:

Knowledge scope and boundaries

Core domains and topics

Relationships between major knowledge areas

Organizational structure of knowledge assets

5.3 index.md

Human-readable knowledge entry page

Provides:

Key knowledge domains

Core reusable assets

Frequently referenced notes

Guidance on how to navigate and use the knowledge base

5.4 knowledge-map.md

Markdown-based semantic relationship map of major concepts

Captures high-level conceptual relationships for both human understanding and AI reasoning

Serves as a lightweight knowledge graph representation (without requiring graph databases)

6. System Management Rules for Knowledge Default Files

For the following files:

context-L0.md context-L1.md index.md knowledge-map.md 

Enforce:

Auto-created on workspace initialization

Cannot be deleted or structurally renamed

Content remains fully editable by the user

If missing at startup, they must be recreated without overwriting existing user content

7. Updated Canonical SwarmWS Structure (Final)

After refactor, the canonical workspace structure must be:

SwarmWS/ ├── system-prompts.md ├── context-L0.md ├── context-L1.md  ├── Knowledge/ │   ├── context-L0.md │   ├── context-L1.md │   ├── index.md │   ├── knowledge-map.md │   ├── Knowledge Base/ │   └── Notes/  └── Projects/     ├── context-L0.md     ├── context-L1.md     ├── Project-A/     ├── Project-B/     └── ... 

This structure reflects:

SwarmWS as a stable root memory container

Knowledge as a first-class semantic domain

Projects as primary execution containers

8. Semantic Zone Model (Updated)

Replace previous zones with:

Shared Knowledge

Knowledge/

Active Work

Projects/

Remove any zone labeled:

“Operating Loop (System)”

9. Migration Rules for Existing Workspaces

If upgrading from a structure that contains:

Signals/ Plan/ Execute/ Communicate/ Reflection/ 

Then: delete user content inside these folders directly



10. Expected Final Outcome

After applying this refactor:

SwarmWS is positioned as a stable knowledge + project root

No internal agent workflow loops are exposed as folders

Shared Knowledge becomes a first-class semantic domain with:

L0/L1 context anchors

Human-readable index

Concept relationship map

Agents rely on hierarchical semantic context instead of process-driven folder structures" let me if you have questions