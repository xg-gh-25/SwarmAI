# COMPLETE — history and rationale

Not read at runtime. Kept for humans and the evolution pipeline.

## Why COMPLETE is a fresh-read stage doc (F004)

Every other stage (evaluate…reflect) reads its `stages/<stage>.md` at the moment it
executes, so the spec is fresh in the reading path at the decision point. COMPLETE used
to be the one exception: its format lived ~750 lines deep inside INSTRUCTIONS.md, far
past the file-head execution entry. By the time a run reached COMPLETE the spec had
decayed out of the attention window (F004) and the completion summary silently
"disappeared." Moving it to `stages/complete.md`, read fresh at INSTRUCTIONS Step 6,
fixed that.

## From hand-written summary to `_display_block` (design §4-E)

The summary used to be hand-typed by the model from a template inside a code fence.
Paths in a fence are not clickable, intent was the model's rewrite, and token cost was
an estimate. The summary is now rendered by `render_delivery_summary_md` from recorded
data and returned by `run-update --status completed` as `_display_block`; the model
relays it verbatim and only adds next steps.

## LOCAL_PR.md removal

Source changes were once aggregated into a single LOCAL_PR.md opened in Canvas. That
was removed: `surface_run_outputs` now emits one OUTPUTS row per committed file
(kind=source-final), and the deliver-stage flag was renamed to `outputs_surfaced`.

## Exec-card split (design §4-C)

The ~5.7K doc was split into a ≤5K exec card, `ref/complete/report-ordering.md`, and
this file. The card's old ordering note claimed `reflect.md` step 10 was the only
report generator; `stage-complete --stage deliver` now auto-generates a missing
REPORT.md (`report_generated`), so the card says so and keeps `run-report --force`
only for a hand-written or stale report.
