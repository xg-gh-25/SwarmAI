# PLAN ref — design_doc artifact shape and legacy publish path

Read when: `stage-complete` rejects the artifact shape, or you are on the legacy
publish/advance path. The field gates are on the card; the ready-to-pass example comes from
`stage-start` (or `python backend/scripts/artifact_cli.py schema --stage plan`).

## Fields

| field | validator | notes |
|-------|-----------|-------|
| `acceptance_criteria` | REQUIRED | carried from EVALUATE, refined |
| `exit_criteria` | REQUIRED at plan stage-complete on strict profiles | see `exit-contract.md` |
| `security_boundaries` | REQUIRED iff `cross_boundary.value == true`; omit otherwise | see `security-boundaries.md` |
| `approach`, `data_model` | recommended (WARN) | |
| `boundaries`, `success_criteria` | recommended (WARN, not BLOCK) | `[MUST]` by discipline |
| `file_discovery`, `change_spec`, `test_strategy` | not schema-checked | `[MUST]` by discipline |
| `api_contract`, `files_to_change`, `impact_projection` | optional | |

## Full data shape

```json
{"approach": "...", "acceptance_criteria": ["AC1: ..."],
 "boundaries": {"always": [], "ask_first": [], "never": []},
 "success_criteria": ["..."],
 "file_discovery": [{"file": "...", "category": "MODIFY|TEST|VERIFY", "finding": "..."}],
 "change_spec": [{"order": 1, "file": "...", "change": "...", "depends_on": [], "ac": "AC1"}],
 "test_strategy": [{"ac": "...", "how": "...", "mock_boundary": "...", "input": "..."}],
 "data_model": "...", "api_contract": "...", "files_to_change": ["..."],
 "exit_criteria": [{"id": "X1", "kind": "test", "ref": "backend/tests/test_x.py", "expect": "pass"}],
 "security_boundaries": [{"boundary": "...", "trust_assumption": "...", "enforcement": "path:line"}]}
```

`category` takes one literal (MODIFY, TEST or VERIFY). `security_boundaries` only when
`cross_boundary.value == true`.

For `stage-complete`, wrap it: `{"artifact": <the object above>, "record": {"decisions":
[{"description": "...", "classification": "mechanical|taste|judgment", "reasoning": "..."}]}}`
(one classification literal per decision). Errors come back structured (`code`, `path`,
`expected`); fix the named field, don't read `artifact_cli.py` source.

## Legacy path (still works; prefer stage-complete)

```bash
python backend/scripts/artifact_cli.py publish --project <P> --run-id <R> \
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" --stage plan \
  --data '<the JSON object above>'
python backend/scripts/artifact_cli.py advance --project <P> --state build --run-id <R>
```

The legacy publish path never requires `exit_criteria`; stage-complete does on strict
profiles. The card's continuation contract applies after `advance` exactly as after
stage-complete: read `next_stage_doc` and keep going in the same turn.
