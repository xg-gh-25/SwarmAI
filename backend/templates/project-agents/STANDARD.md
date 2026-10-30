# The Project Agent standard

A Brain's Project Agent says how the Brain's work is taken in, done and delivered. It
lives in the Brain as `project-agent.yaml`. This page says how a definition is written by
default. The code that applies it is `core/project_agent_standard.py`. What counts as an
error is decided by `project_agent.validate`, not by this page.

Every definition starts as a **draft**. It acts only after a person confirms it on the
Brain Hub form. Where a definition leaves this standard, the form shows a
remark ("standard: ..."). A remark never blocks a confirm or a dispatch: you may keep a
change on purpose.

## Two reference definitions

SwarmAI ships two definitions, one for each shape of Brain. Both are drafts that you
confirm yourself.

| Brain | Shape | File | Standard modes |
|---|---|---|---|
| **General** (Knowledge/Library) | no repo: its knowledge is documents | `runner.yaml`, the Runner Agent | research, write |
| **SwarmAI** (the default Brain) | no repo on a fresh install: its knowledge is the four seed documents | `swarmai.yaml` | research, write |

A Brain with a repo bound in `bindings.yaml` gets `code-change` and `research` instead
(draft it with `artifact_cli project-agent-draft --project <Brain>`).

## The form

Every Brain's definition is the same form: eight sections, always in this order. The
Brain Hub shows it; each field says what it is for and who changes it.

1. **Identity**: Who this agent is and what it is attached to. Routing reads the summary and scope.
2. **Capabilities**: What the Brain gives it to work with. Defined in the Brain; the agent only uses it.
3. **Modes**: The kinds of work it does. The agent picks the mode whose "used when" matches the request.
4. **Guardrails**: What it never does on its own, and what checks it. Every mode inherits these and can only add to them.
5. **Runs in**: Where its work may run. A mode that needs this machine stays here even when the cloud is allowed.
6. **Intake**: How a request reaches it: a new request is compared with these.
7. **Judgment**: When it decides on its own and when it brings you in.
8. **Pipeline profiles**: For reference: what each profile runs. How a task runs (native, or one of these profiles) is picked for each request when it is dispatched, not set here.

Who changes a field:

- **Editable**: drafted from the Brain's documents; change it on the form.
- **From the Brain**: read from the Brain's files; change it there (some rows may be dropped here).
- **Your choice**: only a person sets it.
- **Fixed**: the product's, the same for every Brain.

<!-- TEMPLATE:BEGIN (generated from core/project_agent_standard.TEMPLATE; a test keeps it equal) -->
| Section | Field | What it is for | Who changes it | Stored as |
|---|---|---|---|---|
| Identity | Name | The agent is named after its Brain. | From the Brain | read from the Brain |
| Identity | Brain | The Brain (DDD) it works from: its knowledge, skills and gates. | From the Brain | read from the Brain |
| Identity | Repositories | The code it may change: where it is checked out here, and where it lives remotely. From bindings.yaml. | From the Brain | read from the Brain |
| Identity | Summary (required) | One sentence: what the work in this Brain usually is. Shown wherever the agent is listed. | Editable | `identity.summary` |
| Identity | Takes on | The kinds of work it accepts. | Editable | `identity.in_scope` |
| Identity | Refuses | Work that must go elsewhere. Routing excludes it. | Editable | `identity.out_of_scope` |
| Capabilities | Skills | The Brain's own skills (4-capabilities/). Which mode may use each is set in Modes. | From the Brain | read from the Brain |
| Capabilities | Tools and MCP | Scripts and MCP servers the Brain declares (aim.json). | From the Brain | read from the Brain |
| Capabilities | Jobs | Scheduled work this Brain owns. | From the Brain | read from the Brain |
| Modes | Used when (required) | The kind of request this mode handles. Modes must not overlap. | Editable | `modes[].when` |
| Modes | Repositories | The repos this mode works in. | From the Brain | `modes[].repos` |
| Modes | Setup | Installs run before the checks, copied from the file the repo declares them in. | From the Brain (rows may be dropped) | `modes[].setup` |
| Modes | Checks a run must pass | Commands from the repo's validations.json or CI, copied verbatim. | From the Brain (rows may be dropped) | `modes[].validations` |
| Modes | Skills | Skills of this Brain the mode may use. | Editable | `modes[].skills` |
| Modes | Delivers | How code comes back: bundle = you review the commits, then they land on main. | Editable | `modes[].delivery` |
| Modes | Default mode | The mode used when a request names none. | Editable | `default_mode` |
| Guardrails | Always ask me before | Each time, it stops and waits for your approval. | Your choice | `reserved_for_human` |
| Guardrails | Brain gates | Checks from this Brain's 3-gates/, run on every command. Untick one to stop enforcing it. | From the Brain (rows may be dropped) | `gates.enforce` |
| Guardrails | SwarmAI guards | The product's own guards. Every session runs them. | Fixed | the product |
| Guardrails | Code lands | Who approves code before it reaches main. | Fixed | the product |
| Runs in | Where it may run | Hive lets it run unattended on your cloud SwarmAI. | Your choice | `placements` |
| Intake | Typical requests (required) | 3-5 requests it handles, one sentence each. | Editable | `intake.handles` |
| Intake | Other names | Names people use for this Brain. Each routes a request here; the ones you keep move into aim.json when you confirm. | Your choice | `intake.proposed_aliases` |
| Judgment | When unsure | What it does before acting on a guess. | Editable | `confidence` |
| Judgment | Review focus | What every review of work here checks, each with the document it comes from. | Editable | `watches` |
| Pipeline profiles | Profiles | The profiles a pipeline run can use. A dispatch picks native or one of them per request. | Fixed | the product |
<!-- TEMPLATE:END -->

A draft also records `schema` (`2`), `status` (`draft` until a person confirms; only
`reviewed` goes to a Hive), `reviewed_at` / `reviewed_by`, the bound repos
(`intake.repos`) and `sources` (every document a row was taken from).

A Brain gets a skeleton definition when it is created: the standard modes, its repos'
checks, every reserved action, a chat tab. It has no summary and no typical requests, so
the Brain is not ready until the drafting step fills them in.

## Modes

A mode is one kind of work: when it is used, where it runs, its repos, setup, checks,
skills and delivery. How one task of it runs (native, or a pipeline profile) is not part
of the mode: it is picked for each request when the task is dispatched, by the same rules
for every Brain. A mode can only **narrow** the shared part
(placements a subset, reserved actions and gates only added), so the safety baseline is
reviewed once.

The agent picks the mode by matching the task with each mode's `when`. The standard
modes of one shape of Brain therefore never overlap:

| Mode | For | `when` |
|---|---|---|
| `code-change` | a Brain with a repo | a change to the code of a bound repo (delivery `bundle`) |
| `research` | every Brain | with a repo: a question, an investigation or a design, with no change to code. Without: a question or an investigation, answered in the conversation |
| `write` | a Brain without a repo | a document or a report as the deliverable |

The model may propose more modes (a release mode, a reporting mode), each with a `when`
and the source it comes from. A mode whose checks need a tool only one place has (a Rust
toolchain on the desktop, a local daemon) narrows its own `placements`, so the person
can add `hive` to the shared part without sending that work where it cannot run. Commands in `setup` and `validations` are copied verbatim
from a file the repo declares them in, never written by hand.

## Remarks the form shows

- `identity.summary` or `intake.handles` is missing.
- An action is no longer reserved for a person.
- The Brain has a bound repo but no `code-change` mode.
- A mode still names a pipeline profile (`profile`, `profile_rules`): it no longer has an
  effect and can be removed.
