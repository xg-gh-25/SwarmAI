# The Project Agent standard

A Brain's Project Agent says how the Brain's work is taken in, done and delivered. It
lives in the Brain as `project-agent.yaml`. This page says how a definition is written by
default. The code that applies it is `core/project_agent_standard.py`. What counts as an
error is decided by `project_agent.validate`, not by this page.

Every definition starts as a **draft**. It acts only after a person confirms it on the
Brain Hub review card. Where a definition leaves this standard, the card shows a
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

## Shared part

Written once and inherited by every mode, in this order:

| Field | What it does | Written by | Default |
|---|---|---|---|
| `schema` | the definition format | code | `2` |
| `status` | `draft` until a person confirms; only `reviewed` goes to a Hive | code, then the person | `draft` |
| `reviewed_at` / `reviewed_by` | when and by whom it was confirmed | the review card | `null` |
| `drafted_at` | when the generator wrote it | code | the time |
| `identity.summary` | one sentence: what the work here usually is (routing and the card show it) | the model, from the Brain's documents | — |
| `identity.in_scope` / `out_of_scope` | what this Brain takes and what it refuses (routing excludes the latter) | the model | — |
| `identity.source` | the document the identity comes from | the model | — |
| `intake.handles` | 3-5 typical tasks, one sentence each: routing compares a new task with them | the model | — |
| `intake.repos` | the repos in `bindings.yaml`: work in them belongs to this Brain | code | the bound repos |
| `intake.proposed_aliases` | names people use for this Brain; confirming moves them into `aim.json` | the model | — |
| `confidence` | what to do when unsure (for example: research before changing code) | the model | — |
| `watches` | what a review of work here should look at, each with its source | the model | — |
| `gates.enforce` | the Brain's own gates (`3-gates/`) every session runs | code, the person may untick | all of them |
| `reserved_for_human` | actions a person approves every time | code, the person may remove | `open_pr`, `publish_release`, `push`, `send_external_message` |
| `placements` | where the work may run: `chat-tab`, `local-worker`, `hive` | code, the person may add `hive` | `chat-tab` |
| `landing` | how code comes back | code | `{approval: human}` |
| `modes` | the kinds of work (below) | code + the model | the standard modes |
| `default_mode` | the mode used when none is named | code | `code-change` with a repo, else `research` |
| `sources` | every document a row was taken from | code | — |

## Modes

A mode is one kind of work: when it is used, its pipeline profile, where it runs, its
repos, setup, checks, skills and delivery. A mode can only **narrow** the shared part
(placements a subset, reserved actions and gates only added), so the safety baseline is
reviewed once.

The agent picks the mode by matching the task with each mode's `when`. The standard
modes of one shape of Brain therefore never overlap:

| Mode | For | `when` | Profile |
|---|---|---|---|
| `code-change` | a Brain with a repo | a change to the code of a bound repo | `bugfix` (delivery `bundle`) |
| `research` | every Brain | with a repo: a question, an investigation or a design, with no change to code. Without: a question or an investigation, answered in the conversation | `research` |
| `write` | a Brain without a repo | a document or a report as the deliverable | `direct` |

The model may propose more modes (a release mode, a reporting mode), each with a `when`
and the source it comes from. Commands in `setup` and `validations` are copied verbatim
from a file the repo declares them in, never written by hand.

## Remarks the review card shows

- `identity.summary` or `intake.handles` is missing.
- An action is no longer reserved for a person.
- The Brain has a bound repo but no `code-change` mode.
