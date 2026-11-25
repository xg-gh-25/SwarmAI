# Card: revise

Every round after the first. The rule: **open with a declared scope → edit only that
scope → cut.** The person's own edits always win.

## 1. Find the work

- The person names a file or folder: use it. If it has no `work.json`, register it in place:
  `work.py adopt --dir D --kind deck [--src <existing html>]`. Do not move or copy it.
- They say "the deck" or "this deck": `work.py list` and pick the one that fits; if two fit,
  ask which one.

## 2. Map their words to slide ids

Read `work.json` (the slide order) and the current source. Turn each request into ids:

| They say | Scope |
|---|---|
| "slide 3 font is too small" | the id at position 3 now, e.g. `s-7f3a` |
| "merge slides 3 and 4" | both ids, plus `--remove` for the one that goes away |
| "add a slide about pricing after slide 5" | `--add 1` (and the neighbor only if you must change it) |
| "the subtitle must not wrap; check the other slides too" | every slide that has a subtitle |
| "change the footer on all slides" (a shared file) | the slides, and `--shared` on `open` |
| "I adjusted slide 2, have a look" | nothing to change yet: `open` saves their edit; read it, then answer |

If one request could mean two different sets of slides, ask once. Otherwise decide and say it.

## 3. Open

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" open --dir D --scope s-7f3a,s-91c0 [--add N] [--remove id,id] --note "what and why"
```

If the files on disk differ from the current version, `open` first saves them as a
`vNNN-user` version and uses it as the base. That is the person's hand edit. Never redo or
"clean up" anything in it.

If those edits are yours (you changed the source before opening), add `--mine`: they stay
in this revision as your work instead of becoming the person's version. `open --mine`
refuses when any of them falls outside `--scope`. If a slide it names was the person's
while your own edits are also on disk, do not sort them by guessing: undo your own edits
(you know what you changed), open without `--mine` so theirs is stored as their version,
then redo yours inside the revision.

## 4. Edit only the scope

- Read the source fresh from disk after `open`; never write from a copy you held earlier.
- Change only the sections whose `data-slide-id` is in the scope. Keep every other section
  byte for byte, including its images, portraits and notes.
- A new slide gets a new id (4–8 lowercase hex, never reused).
- Do not reorder slides unless the person asked; a move outside the scope fails the guard.
- Keep template slides (cover, base slides with a footer) as they are.

## 5. Cut and deliver

`work.py cut --dir D` (cards/cut.md), review if needed, then deliver (cards/deliver.md).
In the delivery paragraph, list the slides this version changed, by number and id.
