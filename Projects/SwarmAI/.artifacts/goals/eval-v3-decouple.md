# Goal: Eval System v3 — Decouple + Restructure (run_69b1c644)

## DoD
- [~] eval_service split-write (origin-tagged dual-file) — DONE, committed 042ca29b
- [x] eval_runner.load_golden_set merges public+private — DONE 732d7239
- [x] .gitignore: golden_set.private.yaml ignored — DONE 732d7239 (verified real-repo)
- [x] migrate 161 cases: 33 public / 128 private — DONE 952cea29a (loss-less, public scanned clean)
- [ ] code_digest + bvt{green=total>0,passed>0,failed==0,error==0} + ci_eval_gate.py
- [ ] s_golden-case skill + 4-gate validator (privacy scan incl instance-paths)
- [ ] prod.sh build step0 wire (XG sign-off pending)

## Cycle log
- Cycle 1 (042ca29b): origin-tagged dual-file split-write in eval_service. Fixes Gate-1
  CRITICAL (private→public leak). 5 new tests + 59 eval tests green. THE safety primitive.

## Notes
- Gate-1 BLOCKs being addressed structurally first (XG: "不然你会补不完的坑").
- LLM-judge DDD-read intentional (skeptic) — NOT touched. Decouple is at the data/ship boundary.

- Cycle 2 (732d7239): eval_runner merge-load + .gitignore. 4 new tests, 127 eval tests green. DECOUPLE HALF COMPLETE.

- Cycle 3 (952cea29a): split migration. 33 public/128 private, fail-closed. Caught live golden_set tracks in SwarmWS → fixed gitignore both repos. 38 tests green.
