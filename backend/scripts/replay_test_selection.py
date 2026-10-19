"""Replay: import-based test selection (what the runner chose) vs symbol-based selection,
on real runs. For each run: changed symbols from its landed commits (AST of the file at
that commit, innermost def/class around each changed line, plus same-file direct
callers), then keep a selected test file only if it is the module's own test file,
changed in the run, or mentions a changed symbol. A changed module with a module-level
change (or no symbol found) keeps every selected test that imports it. Reports file
counts, test-time share, and any NEW (non pre-existing) failure the new rule misses."""
import ast, json, os, re, subprocess, sys
from pathlib import Path

REPO = "/Users/gawan/Desktop/SwarmAI-Workspace/swarmai"
RUNS = Path.home() / ".swarm-ai/SwarmWS/Projects/SwarmAI/.artifacts/runs"


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout


def changed_lines(sha):
    """{path: [new-side line numbers]} for backend non-test .py files in a commit."""
    out, cur = {}, None
    for line in git("show", "-U0", "--format=", sha).splitlines():
        if line.startswith("+++ "):
            p = line[6:] if line.startswith("+++ b/") else None
            cur = p if p and p.endswith(".py") and p.startswith("backend/") and "/tests/" not in p else None
            if cur:
                out.setdefault(cur, [])
        elif cur and line.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out[cur] += list(range(start, start + max(n, 1)))
    return out


def symbols(sha, path, lines):
    """(symbols, module_level) for the changed lines of one file."""
    try:
        tree = ast.parse(git("show", f"{sha}:{path}"))
    except SyntaxError:
        return set(), True
    defs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    syms, module_level = set(), False
    top = list(tree.body)
    for ln in lines:
        inner = [d for d in defs if d.lineno <= ln <= (d.end_lineno or d.lineno)]
        if not inner:
            stmt = next((t for t in top if t.lineno <= ln <= (t.end_lineno or t.lineno)), None)
            if stmt is None or isinstance(stmt, (ast.Import, ast.ImportFrom)) or (
                    isinstance(stmt, ast.Expr) and isinstance(getattr(stmt, "value", None), ast.Constant)):
                continue  # blank / comment / import / docstring
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                tg = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                syms |= {n.id for t in tg for n in ast.walk(t) if isinstance(n, ast.Name)}
                continue
            module_level = True
            continue
        d = max(inner, key=lambda d: d.lineno)
        syms.add(d.name)
    # one hop: same-file functions that call a changed symbol
    for d in defs:
        if d.name in syms:
            continue
        names = {n.id for n in ast.walk(d) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(d) if isinstance(n, ast.Attribute)}
        if names & syms:
            syms.add(d.name)
    syms = {s for s in syms if len(s) > 3 and not s.startswith("__")}
    return syms, module_level


def main(run_ids):
    total_old = total_new = 0
    for rid in run_ids:
        rd = RUNS / rid
        ev = rd / "evidence/tests/latest-verify.json"
        st = json.loads((rd / "run.json").read_text())
        if not ev.exists():
            print(rid, "no verify evidence"); continue
        v = json.loads(ev.read_text())
        old = [f for f in v.get("selected_files") or [] if f.endswith(".py")]
        changed_tests = {f for f in v.get("changed_files") or [] if "/tests/" in f}
        shas = [c["sha"] for c in st.get("commits") or [] if isinstance(c, dict) and c.get("sha")]
        mods = {}
        for sha in shas:
            for path, lines in changed_lines(sha).items():
                s, ml = symbols(sha, path, lines)
                prev = mods.get(path, (set(), False))
                mods[path] = (prev[0] | s, prev[1] or ml)
        keep = set(changed_tests)
        for f in old:
            p = Path(REPO) / f
            try:
                text = p.read_text()
            except OSError:
                keep.add(f); continue
            for path, (syms, ml) in mods.items():
                stem = Path(path).stem
                modname = path[len("backend/"):-3].replace("/", ".")
                imports = modname in text or re.search(rf"\b{re.escape(stem)}\b", text)
                if Path(f).name.startswith(f"test_{stem}"):
                    keep.add(f)
                elif ml and imports:
                    keep.add(f)
                elif syms and re.search(r"\b(" + "|".join(map(re.escape, syms)) + r")\b", text):
                    keep.add(f)
        new = [f for f in old if f in keep]
        newfail = [x["nodeid"].split("::")[0] for x in v.get("failures") or []
                   if x.get("classification") != "pre-existing"]
        missed = sorted({f for f in newfail if f not in keep})
        dur = {}
        for r in v.get("results") or []:
            if isinstance(r, dict) and r.get("nodeid"):
                f = r["nodeid"].split("::")[0]; dur[f] = dur.get(f, 0) + (r.get("duration") or 0)
        t_old = sum(dur.get(f, 0) for f in old); t_new = sum(dur.get(f, 0) for f in new)
        if not mods:
            print(f"{rid}: no backend source change (selection {len(old)} files)"); continue
        mls = [Path(p).name for p, (_, ml) in mods.items() if ml]
        print(f"{rid}: backend test files {len(old)} -> {len(new)}"
              f"  time {t_old:.0f}s -> {t_new:.0f}s  modules {len(mods)} (module-level: {mls or '-'})"
              f"  new failures {len(set(newfail))}, missed {missed or 0}")
        total_old += len(old); total_new += len(new)
    print(f"TOTAL files {total_old} -> {total_new}")


if __name__ == "__main__":
    main(sys.argv[1:])
