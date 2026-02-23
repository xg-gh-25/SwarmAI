---
inclusion: always
---

# SwarmAI Project Steering Rules

## Rule 1: Ignore Amazon Internal Steering
This project is NOT part of the Amazon internal stack. Do NOT apply rules from `amazon-builder-steering.md` or any Amazon-specific tooling guidance (Brazil, CRUX, Apollo, Pipelines, Taskei, etc.). This is an independent open-source project with its own build system and workflows.

## Rule 2: Align with Product Design Specs
When generating or reviewing requirements, designs, or specs for SwarmAI, you MUST align with the product design documents in `.kiro/specs/swarmai-product-design-specs/`:

### Reference Documents
- `swarmai-product-design-principles.md` - Core competitive design principles
- `swarmai-product-design.md` - Product architecture (5 Pillars)
- `swarmai-product-mockup.md` - UI/UX mockup specifications

### Key Principles to Follow
1. **Chat is the Command Surface** - Chat drives actions, not just conversations
2. **Execution Threads > Conversation Threads** - Tasks are structured execution units
3. **Workspace is Primary Memory Container** - Persistent cognitive containers for context
4. **Visible Planning Builds Trust** - Show AI reasoning and execution plans
5. **Multi-Agent Orchestration Should Be Visible** - Display active agents and roles
6. **Human Review Gates Are Essential** - Support configurable autonomy levels
7. **Context > Conversation** - Attachments are execution context, not extras
8. **Gradual Disclosure** - Start simple, progressively reveal complexity
9. **Artifacts Are True Output** - Outputs become first-class workspace artifacts
10. **Integration-First** - Skills and MCP integrations for extensibility

### Core Product Models
- **Swarm Workspace** = Persistent memory & knowledge container
- **Swarm ToDo** = Structured intent signal
- **Swarm Task** = Governed multi-agent execution unit
- **Chat** = Command interface (explore → delegate → review)
- **Artifacts** = Durable reusable outputs

### Design Validation Checklist
When creating specs, verify alignment with:
- [ ] Does this support the Command Center mental model?
- [ ] Does this maintain workspace persistence and memory?
- [ ] Does this enable visible, governed agent execution?
- [ ] Does this produce durable artifacts, not just messages?
- [ ] Does this follow progressive disclosure UX principles?

## Rule 3: Spec Mode Prompt Clarification Process
In "Spec" mode, use this prompt template to clarify user input and requirements for effective spec generation. ALWAYS follow these rules:

1. Your first response will be to ask me what the prompt should be about. I will provide my answer, but we will need to improve it through continual iterations by going through the next steps.

2. Based on my input, you will generate 2 sections:
   a) Revised prompt (provide your rewritten prompt, it should be clear, concise, and easily understood by you)
   b) Questions (ask any relevant questions pertaining to what additional information is needed from me to improve the prompt)

3. We will continue this iterative process with me providing additional information to you and you updating the prompt in the Revised prompt section until I say we are done.
