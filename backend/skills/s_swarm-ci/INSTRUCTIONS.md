# Swarm CI — Instructions

Check GitHub Actions CI status for the SwarmAI repository. Provides structured
diagnosis of failures instead of raw log dumps.

## Stage 0: PROJECT GUARD (blocking)

```
Check:
  - Active project == SwarmAI? If not → ABORT.
  - GitHub CLI authenticated? Run: gh auth status
```

---

## Stage 1: LIST RUNS (10s)

Get recent CI run status with structured output.

```bash
cd $SWARMAI_ROOT

# Recent runs (paginated — GitHub API requires pagination for full count)
gh run list --limit 10 --json databaseId,status,conclusion,name,headBranch,createdAt,updatedAt \
  | python3 -c "
import sys, json
runs = json.load(sys.stdin)
print(f'Recent CI Runs ({len(runs)}):')
print(f'{\"ID\":<12} {\"Status\":<12} {\"Conclusion\":<12} {\"Branch\":<10} {\"Workflow\":<20} {\"Age\"}')
print('-' * 80)
for r in runs:
    age = r.get('createdAt', '')[:16]
    print(f'{r[\"databaseId\"]:<12} {r[\"status\"]:<12} {(r.get(\"conclusion\") or \"running\"):<12} {r.get(\"headBranch\",\"\"):<10} {r[\"name\"]:<20} {age}')
"

# Summary counts
gh run list --limit 20 --json conclusion \
  | python3 -c "
import sys, json
from collections import Counter
runs = json.load(sys.stdin)
counts = Counter(r.get('conclusion') or 'in_progress' for r in runs)
print(f'\\nLast 20 runs: {dict(counts)}')
total = len(runs)
success = counts.get('success', 0)
print(f'Success rate: {success}/{total} ({100*success//total}%)')
"
```

**Report format:**
```
Stage 1 LIST RUNS:
  Last 10 runs: 8 success, 1 failure, 1 in_progress
  Success rate: 80%
  Latest: success (main, 2h ago)
  Failures: run 12345678 (backend, 5h ago)
```

---

## Stage 2: DIAGNOSE (per failed run, 30s each)

For each failed run found in Stage 1, fetch failure details.

```bash
# Get failed run details
RUN_ID="<from stage 1>"

# Fetch failed job logs (not full log — just the failed step)
gh run view $RUN_ID --json jobs \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for job in data.get('jobs', []):
    if job.get('conclusion') == 'failure':
        print(f'Failed job: {job[\"name\"]}')
        for step in job.get('steps', []):
            if step.get('conclusion') == 'failure':
                print(f'  Failed step: {step[\"name\"]}')
                print(f'  Started: {step.get(\"startedAt\", \"unknown\")}')
"

# Get log excerpt for the failed job
gh run view $RUN_ID --log-failed 2>&1 | tail -40
```

**Diagnosis patterns:**

| Log Pattern | Likely Cause | Suggested Fix |
|-------------|--------------|---------------|
| `ModuleNotFoundError` | Missing dep in CI env | Add to pyproject.toml, `uv lock` |
| `import fcntl` | Unix-only import on Windows | Use cross-platform `utils/file_lock.py` |
| `sed -i ''` | BSD sed on Linux CI | Use Python for config edits |
| `FAILED tests/` | Test regression | Run locally: `pytest tests/<file> --timeout=60` |
| `isDesktop is not defined` | Tauri API mismatch | Check `__TAURI_INTERNALS__` guard |
| `pip install` missing | build-hive step incomplete | Add `pip install -r requirements.txt` |
| `timeout` | Slow test or deadlock | Check for xdist issues |

**Report format:**
```
Stage 2 DIAGNOSE (run 12345678):
  Workflow: backend-tests
  Failed job: test-ubuntu
  Failed step: Run pytest
  Error: ModuleNotFoundError: No module named 'sqlite_vec'
  Pattern: Missing native extension in CI environment
  Fix: Add sqlite-vec to CI install step or mark test as platform-specific
```

---

## Stage 3: SUMMARIZE (5s)

Produce a concise CI health report.

```
CI HEALTH REPORT
  Repository: xg-gh-25/SwarmAI
  Branch: main
  
  Status: 🟢 GREEN (or 🔴 RED, 🟡 MIXED)
  Success rate: 8/10 (80%)
  
  Current failures:
    • run 12345678 — ModuleNotFoundError: sqlite_vec (Ubuntu only)
    • run 12345679 — timeout in test_streaming (xdist deadlock)
  
  Recommendations:
    1. sqlite_vec: add to CI apt-get or mark @pytest.mark.skipif(not macos)
    2. test_streaming: known xdist issue — add --timeout=60 to CI
  
  Last green: 2h ago (run 12345680)
  Streak: 2 failures after 5 successes
```

**Health status logic:**
- 🟢 GREEN: Latest run on main is success
- 🟡 MIXED: Latest is success but failures in last 5
- 🔴 RED: Latest run on main is failure

---

## Quick Commands

The user may ask for specific subsets:

| User says | Execute |
|-----------|---------|
| "is CI green?" | Stage 1 only → report latest status |
| "why did CI fail?" | Stage 1 + Stage 2 for failures |
| "CI report" | All 3 stages |
| "check if my commit passed" | `gh run list --commit <HEAD> --json conclusion` |

---

## Notes

- **Pagination:** GitHub API returns max 30 items per page. For full failure count, may need `--limit 30` or multiple pages. (LL20: paginated fetch required for accurate counts)
- **Rate limits:** gh CLI handles auth automatically. If rate-limited, wait and retry.
- **Cross-platform failures:** SwarmAI CI runs on Ubuntu. macOS-only features (fcntl, launchd, Tauri) may fail there. This is expected — check if the failure is platform-appropriate before flagging.
