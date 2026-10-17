# PLAN ref — Required Experts

Read when: choosing which review experts to declare, or a declared expert did not satisfy D7
at `run-commit`. The command and the D7 gate line are on the card (step 8, Gates).

## What to declare

At PLAN, declare the code-review experts (`backend/experts/code/`) this changeset MUST be
reviewed by: the experts whose domain the change touches. Examples: an endpoint/handler change
⇒ `security`; a concurrency/async change ⇒ `concurrency`; a state-machine change ⇒
`state-machine`. Use the registry's expert ids as the vocabulary
(`ExpertManager.scan_experts()`, or the 12 ids under `backend/experts/code/`: api-contract,
code-quality, concurrency, correctness, integration, operational, performance, red-team,
security, spec-compliance, state-machine, ux-test).

```bash
python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> \
    --add-required-expert security --add-required-expert concurrency
```

## Append-only (D7-SAFETY③)

PLAN sets the baseline. BUILD may ADD an expert if the changeset grows into a new domain.
Nothing removes one: a removable declaration would be a channel to skip a required review.

## Enforcement (D7)

`run-commit` refuses unless every declared expert left an adversarial-review marker for THIS
run. The marker is written on sub-agent completion only when:
- the spawn reads as ADVERSARIAL (the Gate-2 template's "Adversarially review this changeset —
  hunt for bugs…" wording), and
- the prompt carries an `EXPERT-ID: <id>` line (that is what credits the expert).

So a declared expert must run **in the DELIVER Gate-2 batch**, which takes the union of the
tier default and this list (see `stages/deliver.md`). An expert that only ran at REVIEW, e.g.
the Spec Compliance Gate (a per-AC check, not an attack), does NOT satisfy D7: the commit is
refused until that expert is re-spawned in a second, serial adversarial batch.

Declare the experts the change must be ATTACKED by. Don't declare `spec-compliance` to record
that REVIEW will run it; REVIEW always does.
