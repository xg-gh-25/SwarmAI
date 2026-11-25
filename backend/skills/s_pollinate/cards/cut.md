# Card: cut a version

A version is what you hand over. Cut one when you are ready to show the person, not after
every edit. While you work, `work.py render --dir D` gives you outputs to look at without
making a version.

## Run it

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" cut --dir D [--formats html,pdf,png,pptx]
```

`cut` does, in order: render → measure → craft checks → guard → write the new version.
`current` moves to the new version only when all of them pass. The open scope is cleared.
Leave out `--formats` to get the kind's usual outputs; ask for `pptx` only when the person
wants a PPTX.

## Read the result

| Exit | Meaning | What you do |
|---|---|---|
| 0 | The version is cut | review it (INSTRUCTIONS.md § 6), then deliver (cards/deliver.md) |
| 1 | A craft check or the guard failed | read `.versions/<id>/checks.json` and `guard.json`; fix; cut again |
| 2 | Usage or environment error | fix the command or install what is missing; the content is not the problem |

**A craft check failed.** Each finding names the slide, the check and where. Group the
findings by cause (one too-small caption style on six slides is one cause), fix each cause
once, on every slide it touches, and cut again. A fix outside the open scope needs the
slide in the scope: open again with it, or ask if the person did not want that slide
touched.

**The guard failed.** You changed a slide, a shared file or the slide order outside the
scope. Undo that change. Do not widen the scope only to make the guard pass.

**It fails again and again.** After three cuts that fail on the same check, stop. Deliver
the best passing version (or none), name the check and why it still fails, and ask the
person one clear question.

## Look at it yourself

Before review, open `review/` (contact sheet first, then single frames for the slides you
changed) and look once at each image. Check what no script measures: does each slide say
one thing, does the deck tell the thesis, does it look like the chosen design system.
Read a PNG only to judge it; to show it to the person, link it in the chat instead.
