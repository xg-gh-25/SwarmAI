---
created: 2026-10-30
updated: 2026-10-30
title: "Swarm Out: dispatch without a confirm click, and a dispatch card from the record"
status: implemented 2026-10-30 (native)
---

# Swarm Out: dispatch without a confirm click, and a dispatch card from the record

Owner decision (2026-10-30): the person is asked only when a request cannot be turned
into one run spec; everything else follows the autonomy principle (SOUL P10). After a
dispatch the person is told whether it went out, with a way to open the run's session.
Every place a task can be sent from (the Swarm Out tab, any chat tab, a Slack DM) works
the same; Slack cannot open the Hive tab, which is accepted. Mockup:
`Knowledge/Designs/mockups/2026-10-30-swarm-out-dispatch-card.html` (workspace).

## 1. When the person is asked

| Situation | What happens |
|---|---|
| `route` is not certain of the Brain | asked (the likely Brains + General) |
| the task fits two modes whose results differ (a report vs commits) | asked |
| no checkable acceptance can be written from what was said | asked |
| which Hive | not asked: the one named, else `pick_hive` (§2) |
| profile, title, the one-line brief | not asked: the definition's rule, the agent writes them |
| uncommitted files | not asked: said on the card |
| a refusal (a draft or changed definition, no running Hive) | not asked: a failure card says why and what to do |

Desktop asks with the AskUserQuestion card; a channel session asks the same question as
numbered text. The skill (`s_swarm-out`) carries this table.

## 2. Which Hive

`swarm_out.pick_hive(fleet)`, deterministic: running and reachable, not saturated; fewest
streaming sessions, then the lowest memory, then the name. None → the dispatch is
refused ("no running Hive can take a task"). `--hive` names one and is used as is. The
row keeps why as a code (`named`, `only_free`, `idle`, `least_busy:<n>`); each renderer
words it (`swarm_out_card.hive_reason_text`, the desktop card's locale).

## 3. The dispatch card

A `swarm_out_dispatch` block written by the backend, not by the agent. The streaming
orchestrator watches a Bash call of `scripts.swarm_out dispatch` (`swarm_out_card.watched`):

- **Success** (the call printed a dispatch row): the card is built from **this machine's
  dispatch log row** with that `dispatch_id` (`swarm_out_card.from_row`), never from the
  printed text: Hive, Brain, mode, profile, revision, Brain sync, repositories at their
  sha with the uncommitted count, acceptance, delivery, the session id, the brief.
- **Failure** (`{"ok": false, "detail": ...}`): a failure card with the detail and the
  Brain / title the command named; it says nothing was sent.
- **Not sure** (`"maybe_sent": true`): the Hive did not name the session in time
  (`SwarmOutMaybeSent`, the router's 504) or the CLI stopped waiting. The task may be
  running there, so the card says that and never "nothing was sent"; its row is in the
  dispatch log without a session, as before.

The card is this call's: the output is read from its first JSON object that starts a
line (what the CLI printed; anything the command prints after it cannot replace it), and
a row is shown only when it was sent after the call was seen (an older dispatch's id
printed again is no card). A call that printed no JSON at all (the Bash tool stopped it,
or it ran in the background) may have gone out: a "not sure" card. An argparse usage
error ran nothing: no card.

The row's own fields always win over what the card adds (`brief`, `hive_reason`,
`brain_sync`, `repo_notes`), so the card fields cannot change what was recorded.

Block: `{"type": "swarm_out_dispatch", "ok", "maybeSent", "dispatchId", "sessionId", "title",
"brief", "hive", "hiveReason", "brain", "mode", "profile", "revision", "brainSync",
"repos": [{name, ref, sha, dirty}], "acceptance", "delivery", "error", "at", "text"}`.
`text` is the same card in plain text (Slack). The block is not conversation
(context_injector) and is persisted as its own row like a pipeline stage row.

Desktop (`pages/chat/components/DispatchCard.tsx`, the ItemBox look of the turn cards):
a first-level zone after the call (`turnView` unit `dispatch`, like products), status
bar, title + brief, one row per field with a grey reason, a live state from one runs
poll shared by every card on screen (only for a card that went out), "Open Hive
session" (`openHiveSession`) and "View in Swarm Out" (`showSwarmOut`, both through the
overlay bridge ChatPage publishes). A refused card offers "Open <Brain>" in the Brain
Hub. Slack: the gateway appends the card's text to the reply (both reply loops, also
after an auto-answered question); the whole card only in a direct message, in a group or
channel its first and last line (no brief, acceptance or repositories for everyone
there). The text carries no channel markup (`<` `>` are replaced: an error can carry a
Hive's words). No link to the Hive tab, the dispatch id instead.

## 4. Who may dispatch

Only the owner (desktop, the owner's own channel DM). A non-owner channel session is
refused twice, like the knowledge command: the CLI refuses when `RESTRICTED_ENV` is set,
and the Bash permission handler denies a `swarm_out` command there
(`security_hooks.owner_command_denial`). Like the knowledge guard this is a usability
guard, not a boundary: the agent controls its command line and could reach the daemon's
loopback API directly (the word check is passed by an encoded path such as
`swarm%2Dout`, and the loopback routes do not know their caller). A boundary needs the
dispatch route to know the calling session (for example a token per session, withheld
where `RESTRICTED_ENV` is set); recorded as a known issue. Also not verified: whether a
non-owner channel session's permission mode makes the SDK call the Bash permission
handler at all (the knowledge guard has the same dependency).

## 5. Verification

- A clear request dispatches without a question and yields a success card whose fields
  equal the dispatch log row; a fake printed row never becomes a card (the log decides).
- A refusal yields a failure card that says nothing was sent; a dispatch the Hive may
  have taken says it may be running, never that nothing was sent.
- `pick_hive`: named wins; saturated / unreachable / stopped skipped; ties by streaming,
  memory, name; none → refused.
- The block is non-conversational; the gateway's reply carries `text`.
- A non-owner channel session: the CLI refuses and the hook denies.
- Desktop: the card renders in any chat tab, "Open Hive session" opens the Hive tab.
- Each guard reverted turns its test red (`scripts.mutation_check`).
