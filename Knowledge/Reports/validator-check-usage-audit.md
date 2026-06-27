# Validator Check-Usage Audit (C5, run_7cf9da85)

_Read-only. Data + verdict; no check deleted. Source: test-coverage of each
check's BLOCK path (run.json records carry no per-check verdict history —
a historical tally is impossible, so test-coverage is the earns-its-keep proxy)._

**16/17 checks have a test-proven BLOCK path (KEEP). 1 need REVIEW.**

| Check | Sev | Guards | BLOCK-test lines | Verdict |
|-------|-----|--------|-----------------:|---------|
| stage_order | hard | stage sequence per profile | 5 | **KEEP** |
| artifact_exists | hard | stage published an artifact | 8 | **KEEP** |
| artifact_schema | hard | required fields present | 1 | **KEEP** |
| decision_logged | advisory | >=1 classified decision | 11 | **KEEP** |
| budget_recorded | advisory | token_cost > 0 | 7 | **KEEP** |
| profile_respected | hard | stage in profile | 15 | **KEEP** |
| ddd_consistency | advisory | non-goals vs approach | 0 | **REVIEW** |
| quality_gate(8) | hard | smoke/litmus/ac_coverage/layers | 13 | **KEEP** |
| depth(9) | hard | field values indicate real work | 3 | **KEEP** |
| push_ready(10) | hard | binary push-ready verdict | 1 | **KEEP** |
| semantic(11) | advisory | content-quality heuristics | 2 | **KEEP** |
| skip_justified(12) | hard | skips need reason+no counter | 11 | **KEEP** |
| output_routing(13) | hard | consume declared upstream | 13 | **KEEP** |
| understanding_gate(G0) | hard | diagnosis observed not inferred | 1 | **KEEP** |
| ambiguity_scan(G0) | hard | spec ambiguity self-resolved | 1 | **KEEP** |
| working_backwards(G0) | hard | greenfield value framing | 1 | **KEEP** |
| repro_gate | hard | bug-class observation evidence | 20 | **KEEP** |

## Verdicts
- **KEEP** — at least one test asserts this check's BLOCK fires → it provably earns its keep.
- **REVIEW** — no dedicated BLOCK test found by the probe. NOT a delete order:
  either the check is reachable-but-untested (add a test) or genuinely dead
  (a separate cycle removes, with human approval — never blind-deleted here).

## Honest limitation
This is a TEST-COVERAGE proxy, not a production BLOCK tally. A check can be
load-bearing in prod yet show REVIEW here if its test lives under a different
probe string. Treat REVIEW as 'investigate', never 'delete'. (STEERING #3:
prefer deletion — but a gate is removed only with data + approval.)