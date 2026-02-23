# SwarmAI Multi-Channel Signal Ingestion & Reply Design
## Cross-Channel ToDo/Task Extraction + Structured Reply Loop (Production-Grade)

---

## 1. Purpose

This document defines the architecture for **multi-channel signal ingestion and structured reply routing** in SwarmAI, enabling:

- Automatic extraction of **Signals (ToDos)** and **Tasks** from external channels:
  - Slack
  - Microsoft Teams
  - Email
  - Task centers (Jira, SIM, Taskei, etc.)
  - CRM (e.g. salesforce)
- Deterministic normalization into SwarmAI’s **DB-canonical entities**
- Intelligent routing into the correct **Workspace / SwarmWS**
- Automatic **structured acknowledgement replies** back to originating channels
- Full auditability, idempotency, and governance compliance
- Seamless integration with:
  - SwarmAI Context Engine (W-Frames)
  - Skills / MCP connectors
  - Multi-user collaboration sync/routing service

This closes the loop:
> External request → SwarmAI captures structured work item → SwarmAI replies with a clear, actionable confirmation.

---

## 2. Core Principles

### 2.1 Signals are canonical
All inbound work from channels is normalized into:
- **Signal** (UI term) → DB entity **ToDo**
- Optionally converted to **Task**

> “Signal is the unified inbox of work.”

### 2.2 Deterministic ingestion first, LLM second
- Deterministic parsers extract obvious tasks (mentions, keywords, assignments)
- LLM triages ambiguity, summarization, deduplication
- Prevents hallucination, improves auditability

### 2.3 Close the loop
Every successful capture should:
- Persist the structured entity
- Reply to the source channel with a **structured acknowledgement**

### 2.4 Local-first + collaboration ready
- Clients store canonical data locally (SQLite)
- Sync/routing service distributes assignments and events across users/devices
- Replies always originate from deterministic dispatcher, not free-form agent output

---

## 3. End-to-End Architecture

```

External Channels
(Slack / Teams / Email / Jira / SIM / Taskei)
↓
Connector Layer (Skills / MCP Servers)
↓
Ingestion Workers (Deterministic Parsing + LLM Triage)
↓
Normalize → Signals (ToDos) / Tasks in DB
↓
Routing Service (multi-user inbox + assignments)
↓
Context Engine builds W-Frame
↓
SwarmAgent triages / plans / converts
↓
Reply Dispatcher (deterministic)
↓
Structured Reply back to Original Channel

```

---

## 4. Connector Layer (Skills / MCP)

Each external system is integrated via:
- **MCP Server** (preferred for tools with APIs)
- or **Skill** (simpler, stateless logic)

Examples:
- Slack Connector MCP
- Microsoft Teams Connector MCP
- Email Connector MCP
- Jira / SIM / Taskei Connector MCP

Workspace configuration (intersection model) controls:
- which connectors are enabled
- privileged connectors requiring explicit confirmation
- governance enforcement

---

## 5. Signal Normalization Model

All inbound events map to a canonical **Signal (ToDo)** schema:

| Field | Description |
|------|-------------|
| id | Primary key |
| workspace_id | Target workspace (or SwarmWS default) |
| title | Short extracted task summary |
| description | Context snippet or summary |
| source_type | slack \| teams \| email \| jira \| sim \| integration |
| source_ref_id | FK to original message reference |
| status | pending \| overdue \| in_discussion \| handled \| cancelled |
| priority | high \| medium \| low \| none |
| due_date | Optional extracted deadline |
| assignee_user_id | Optional assignment |
| created_at / updated_at | Timestamps |

This provides a **single canonical inbox** regardless of source.

---

## 6. External Message Reference Tracking

### Table: `external_message_refs`

Tracks the origin of each captured signal/task.

| Field | Description |
|------|-------------|
| id | Primary key |
| source_type | slack \| teams \| email \| jira \| sim |
| external_thread_id | Channel thread/conversation ID |
| external_message_id | Message identifier |
| external_permalink | Link to original message |
| sender_external_id | Sender identity |
| received_at | Timestamp |
| raw_fingerprint | Hash for deduplication |

Used for:
- Idempotency
- Deep linking
- Audit trail
- Reply routing

---

## 7. Deterministic Ingestion Workers

### Responsibilities
1. Listen to connector events (webhooks/polling)
2. Parse deterministic patterns:
   - Mentions: `@user please do X`
   - Imperative phrases (“please prepare”, “need to”)
   - Jira assignment events
3. Create or update Signal (ToDo)
4. Generate `ReplyPlan` (see section 10)
5. Enqueue reply

### LLM role (secondary)
- Infer tasks from long threads
- Summarize email conversations
- Deduplicate similar signals
- Suggest workspace routing

---

## 8. Routing and Multi-User Delivery

### Assignment rule
When a Signal has `assignee_user_id`:
1. Create inbox item for that user (via sync/routing service)
2. Also keep original Signal in source workspace
3. Optionally land a copy in assignee’s **SwarmWS Signals**

This mirrors:
> “Assigned work shows up in your global cockpit.”

---

## 9. Idempotency and Deduplication

To avoid duplicate captures or replies:

### Fingerprint
```

raw_fingerprint = hash(source_type + external_message_id + normalized_text)

```

### Idempotency key
```

idempotency_key = hash(source_type + external_message_id + action + entity_id)

````

If already processed → skip creation or reply.

---

## 10. Reply Plan Contract (Agent → Dispatcher)

The agent does not directly send replies.  
Instead, it outputs a deterministic **ReplyPlan**:

```json
{
  "should_reply": true,
  "reply_type": "created_signal",
  "target": {
    "source_type": "slack",
    "thread_id": "T123",
    "message_id": "M456"
  },
  "related_entity": {
    "type": "todo",
    "id": "todo_789"
  },
  "content_md": "Structured markdown reply content"
}
````

Reply types:

* `created_signal`
* `created_task`
* `merged`
* `needs_clarification`
* `status_update`
* `error`

---

## 11. Reply Dispatcher (Deterministic Sender)

### Responsibilities

* Validate policy & connector permissions
* Enforce idempotency rules
* Format message per channel
* Send via MCP connector
* Persist outbound record
* Retry transient failures

This ensures:

* No duplicate posts
* No unauthorized posting
* Consistent formatting

---

## 12. Outbound Reply Tracking

### Table: `outbound_replies`

| Field               | Description                                           |
| ------------------- | ----------------------------------------------------- |
| id                  | Primary key                                           |
| source_type         | slack | teams | email | jira                          |
| external_thread_id  | Target thread                                         |
| external_message_id | Original message                                      |
| reply_message_id    | Sent message ID                                       |
| reply_type          | ack | created | status_update | clarification | error |
| content_md          | Sent content                                          |
| status              | queued | sent | failed | skipped                      |
| related_entity_type | todo | task                                           |
| related_entity_id   | Linked entity                                         |
| sent_at             | Timestamp                                             |
| error_message       | Failure details                                       |

---

## 13. Structured Reply Templates

### 13.1 Created Signal

```
✅ Captured as Signal
• Title: {title}
• Priority: {priority}
• Due: {due_date}
• Workspace: {workspace}
• SwarmAI: {deep_link}

Reply with:
- due:YYYY-MM-DD
- priority:high|medium|low
- workspace:<name>
```

### 13.2 Created Task

```
🚀 Created Execution Task
• Task: {title}
• Status: Draft
• Workspace: {workspace}
• Assignee: @{user}
• Link: {deep_link}
```

### 13.3 Merged/Deduped

```
🔁 Updated existing Signal
Linked to: {title}
Status: {status}
Link: {deep_link}
```

### 13.4 Needs Clarification

```
❓ Need one detail
I captured this as a Signal but missing:
- due date
- assignee
- workspace

Reply with:
due:YYYY-MM-DD | assign:@user | workspace:<name>
```

---

## 14. Channel-Specific Constraints

### Slack

* Prefer thread replies
* Use message blocks optionally
* Respect bot scopes

### Microsoft Teams

* Reply threading depends on message type
* Limited Markdown formatting

### Email

* Short structured summary
* Avoid quoting full threads
* Include deep link

### Jira / SIM / Taskei

* Reply = issue comment + optional field update

---

## 15. Governance and Privacy Controls

Before sending replies:

* Check connector is enabled in workspace configuration
* Enforce privileged capability confirmation if required
* Avoid leaking private workspace details in public channels
* Minimize echoed content (summaries only)
* Log all outbound replies to audit trail

---

## 16. Integration with Context Engine & W-Frames

When processing inbound messages, W-Frame includes:

* Source message excerpt
* Workspace scope
* Effective connector configuration
* Policy status (allowed to reply?)
* Reply template policy

SwarmAgent uses this to produce a safe **ReplyPlan**.

---

## 17. Two-Way Update Commands (Optional Enhancement)

Users can update captured items directly from channel replies:

Examples:

* `due:2026-02-24`
* `priority:high`
* `assign:@bob`
* `status:handled`

Ingestion parses these structured tokens and updates the linked Signal/Task, then confirms.

---

## 18. Summary

This design enables SwarmAI to:

1. Ingest work requests from multiple external channels
2. Normalize them into canonical **Signals (ToDos)** and Tasks
3. Route assignments across users/workspaces
4. Provide deterministic, structured acknowledgements back to channels
5. Maintain auditability, governance, and idempotent behavior
6. Seamlessly integrate with Context Engine, Skills/MCP connectors, and multi-user sync service

Result:

> A closed-loop, enterprise-grade work orchestration system where SwarmAI reliably captures, organizes, and communicates actionable work across all collaboration channels.

