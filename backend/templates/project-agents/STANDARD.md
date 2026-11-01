# The Project Agent standard

A Brain's Project Agent tells a Hive run how to do the Brain's work. It lives in the Brain
as `project-agent.yaml`. The standard one is written when the Brain is created and drafted
from the Brain's knowledge; the agent keeps it current. Nothing in it is confirmed: a
dispatch uses the latest definition, and when admission finds something wrong the agent
fixes the definition and says so. The code is `core/project_agent_standard.py`; what counts
as an error is decided by `project_agent.validate`.

The actions a Project Agent session always asks the person for (push, opening a pull
request, publishing a release, sending a message outside) are the product's, the same for
every Brain. What comes back from a run (an answer, a document or commits) is chosen when
the task is dispatched.

## Two reference definitions

SwarmAI ships two definitions, one for each shape of Brain.

| Brain | Shape | File | Standard modes |
|---|---|---|---|
| **General** (Knowledge/Library) | no repo: its knowledge is documents | `runner.yaml`, the Runner Agent | research, write |
| **SwarmAI** (the default Brain) | no repo on a fresh install: its knowledge is the four seed documents | `swarmai.yaml` | research, write |

A Brain with a repo bound in `bindings.yaml` gets `code-change` and `research` instead
(draft it with `artifact_cli project-agent-draft --project <Brain>`).

## Fields

Only what a Hive run reads.

| Field | What it is for |
|---|---|
| `identity.summary` | One sentence: what the work in this Brain usually is. The first line of the run's project section, and what routing shows. |
| `intake.handles` | 3-5 typical requests, one sentence each. Routing compares a new task with them. |
| `intake.repos` | The repos the Brain binds (`bindings.yaml`). |
| `modes[].id` / `when` | One kind of work and the kind of request it handles. The agent picks the mode whose `when` matches the task. |
| `modes[].repos` | The repos this mode works in; the Hive clones them. |
| `modes[].setup` | Install steps run first, copied verbatim from the file the repo declares them in (`source`). |
| `modes[].validations` | Checks the run must pass before it finishes, copied verbatim from `validations.json` or CI (`source`). |
| `modes[].skills` | The Brain's skills the mode uses. A desktop-only skill is not listed on a Hive. |
| `gates.enforce` (and a mode's own `gates`) | The Brain's gates (`3-gates/`) run before each command and file write. A mode only adds gates. |
| `watches` | What every review of work here checks, each with the document it comes from. |
| `default_mode` | The mode used when a task names none. |

Before a dispatch the host checks that every `setup` / `validations` row's `source` is in
the repository; a row whose source is missing refuses the dispatch until it is fixed.

Keys an older definition may still carry (`status`, `reviewed_at`, `reviewed_by`,
`reserved_for_human`, `placements`, `delivery`, `pr_remote`, `confidence`, `sources`,
`identity.in_scope` / `out_of_scope`, `intake.proposed_aliases`, a mode's `profile` /
`profile_rules`) are not read, and the next write drops them.

A Brain gets a skeleton definition when it is created: the standard modes and its repos'
checks. It has no summary and no typical requests, so the Brain is not ready until the
drafting step fills them in.

## Modes

The standard modes of one shape of Brain never overlap:

| Mode | For | `when` |
|---|---|---|
| `code-change` | a Brain with a repo | a change to the code of a bound repo |
| `research` | every Brain | with a repo: a question, an investigation or a design, with no change to code. Without: a question or an investigation, answered in the conversation |
| `write` | a Brain without a repo | a document or a report as the deliverable |

The model may propose more modes (a release mode, a reporting mode), each with a `when`
and the source it comes from. How one task of a mode runs (native, or a pipeline profile)
is not part of the mode: it is picked for each request when the task is dispatched.
