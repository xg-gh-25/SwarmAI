"""Feature Map replay on the real pinned runtime (R1.3, R3.3, R4.1-R4.4, R4.6).

Real Node, real Playwright, real Chromium against a page served in the sandbox: what is
proved here is the product's own behaviour, so nothing is stubbed. Not in the CI BVT
allowlist (no browser runtime in CI); run locally and in the TEST of a change to
core/feature_map (see tests/feature_map_fixtures.py)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from core.feature_map import files, ledger
from core.feature_map.replay import Selection, replay
from tests.feature_map_fixtures import app_data, git, make_repo, pinned_runtime, runtime_env  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the test page server is POSIX")

FEATURES = """\
schema: 1
features:
  - {id: title-a1, title: Title shows, target: web, spec: specs/title.spec.ts}
"""
TITLE = """\
import { test, expect } from '@playwright/test';
test('title', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('title')).toHaveText('Hello');
});
"""


def _cli(root, *args):
    proc = subprocess.run([sys.executable, "-m", "scripts.feature_map", *args, "--repo-root", str(root),
                           "--project", "B", "--repo", "app"], capture_output=True, text=True,
                          cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]), timeout=600)
    return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])


def test_pinned_playwright_wins_over_repo_copy(tmp_path, app_data, runtime_env):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"title.spec.ts": TITLE}, features=FEATURES, extra={
        # the repo's own @playwright/test: loading it would throw
        "node_modules/@playwright/test/package.json": '{"name":"@playwright/test","version":"0.0.0","main":"index.js"}',
        "node_modules/@playwright/test/index.js": "throw new Error('WRONG COPY')",
        ".swarmai/features/node_modules/@playwright/test/index.js": "throw new Error('WRONG COPY')",
    })
    fmap = files.load_folder(root / ".swarmai/features")
    results, sb = replay(fmap, root, Selection(list(fmap.features)))
    shutil.rmtree(sb, ignore_errors=True)
    assert {(r["project"], r["result"]) for r in results} == {("regular", "pass"), ("compact", "pass")}, results


def test_browser_network_blocked(tmp_path, app_data, runtime_env):  # noqa: F811
    spec = """\
    import { test, expect } from '@playwright/test';
    for (const url of ['http://example.com/', 'http://1.1.1.1/']) {
      test(`blocked ${url}`, async ({ page }) => {
        await page.goto('/');
        const r = await page.evaluate((u) => fetch(u, { mode: 'no-cors' }).then(() => 'reached', () => 'blocked'), url);
        expect(r).toBe('blocked');
      });
    }
    """
    root = make_repo(tmp_path / "r", specs={"net.spec.ts": spec}, features=FEATURES.replace(
        "title.spec.ts", "net.spec.ts"))
    fmap = files.load_folder(root / ".swarmai/features")
    results, sb = replay(fmap, root, Selection(list(fmap.features)))
    shutil.rmtree(sb, ignore_errors=True)
    assert len(results) == 4 and all(r["result"] == "pass" for r in results), results
    # the loopback page itself was reachable (a broken proxy would fail page.goto)


def test_verify_and_results(tmp_path, app_data, runtime_env):  # noqa: F811
    specs = {
        "title.spec.ts": TITLE,
        "pop.spec.ts": """\
            import { test, expect } from '@playwright/test';
            test('popover in window', async ({ page }) => {
              await page.goto('/');
              await page.getByTestId('open').click();
              await expect(page.getByTestId('pop')).toBeInViewport({ ratio: 1 });
            });
            """,
        "flaky.spec.ts": """\
            import { test, expect } from '@playwright/test';
            test('flaky once', async ({ page }, info) => {
              await page.goto('/');
              expect(info.retry).toBeGreaterThan(0);
            });
            """,
        "state.spec.ts": """\
            import { test, expect } from '@playwright/test';
            test('marked state', { tag: '@start:marked' }, async ({ page }) => {
              const r = await page.goto('/marked.txt');
              expect(r?.status()).toBe(200);
            });
            """,
    }
    features = """\
    schema: 1
    features:
      - {id: title-a1, title: Title shows, target: web, spec: specs/title.spec.ts}
      - {id: pop-b2, title: Popover stays in the window, target: web, spec: specs/pop.spec.ts}
      - {id: flaky-c3, title: Flaky, target: web, spec: specs/flaky.spec.ts}
      - {id: state-d4, title: Start state, target: web, spec: specs/state.spec.ts}
      - {id: cmd-e5, title: Command passes, target: web, run: [python3, -c, "import sys; sys.exit(0)"]}
      - {id: cmd-f6, title: Command fails, target: web, run: [python3, -c, "import sys; sys.exit(4)"]}
    """
    root = make_repo(tmp_path / "r", specs=specs, features=features)
    # the popover's compact walk is quarantined (3 flakes): still replayed, matched by its
    # exact key, so its regular walk and every other feature still run
    fmap = files.load_folder(root / ".swarmai/features")
    h_pop = files.content_hash(fmap, fmap.feature("pop-b2"))
    q_key = ledger.walk_key("pop-b2", "popover in window", "compact")
    ledger.append("B", "app", [{"feature": "pop-b2", "walk": "popover in window",
                                "project": "compact", "key": q_key, "hash": h_pop,
                                "trigger": "replay", "result": "flaky"}] * 3)
    code, out = _cli(root, "replay")
    assert out["quarantined"] == [q_key]
    by = {(r["feature"], r["project"]): r["result"] for r in out["results"]}
    assert by[("title-a1", "regular")] == by[("title-a1", "compact")] == "pass"
    # R4.3: fits 1440×900, not 1024×640
    assert by[("pop-b2", "regular")] == "pass" and by[("pop-b2", "compact")] == "fail"
    # R4.4: fail then pass on the retry is flaky, never pass
    assert by[("flaky-c3", "regular")] == "flaky" and by[("flaky-c3", "compact")] == "flaky"
    # a start state's setup ran before its tagged test
    assert by[("state-d4", "regular")] == "pass"
    # R4.6: a command walk is decided by its exit code
    assert by[("cmd-e5", "command")] == "pass" and by[("cmd-f6", "command")] == "fail"
    assert code == 1 and out["counts"]["fail"] == 2
    # a failed command walk keeps its output as evidence
    cmd = next(r for r in out["results"] if r["feature"] == "cmd-f6")
    assert any(f.endswith(".log") for f in cmd["evidence_files"]), cmd
    # evidence: screenshots copied out of the (removed) sandbox
    shots = [f for r in out["results"] for f in r["evidence_files"] if f.endswith(".png")]
    assert shots and all(__import__("pathlib").Path(f).is_file() for f in shots)
    # R4.2: renaming the test id the spec uses fails replay, naming the step, both viewports
    page = (root / "site/index.html").read_text().replace('data-testid="title"', 'data-testid="heading"')
    (root / "site/index.html").write_text(page)
    code, out = _cli(root, "verify", "title-a1")
    rows = [r for r in out["results"] if r["feature"] == "title-a1"]
    assert code == 1 and {r["project"] for r in rows} == {"regular", "compact"}
    assert all(r["result"] == "fail" and "getByTestId('title')" in r["error"] for r in rows), rows
    # R4.1: verify writes ledger lines with trigger verify; a green verify is what is saved
    lines = [r for r in ledger.read("B", "app") if r["trigger"] == "verify"]
    assert lines and all(r["result"] == "fail" for r in lines)
    (root / "site/index.html").write_text(page.replace('data-testid="heading"', 'data-testid="title"'))
    code, out = _cli(root, "verify", "title-a1")
    assert code == 0 and all(r["result"] == "pass" for r in out["results"])


def test_exit_codes_flaky_only_and_not_run(tmp_path, app_data, runtime_env):  # noqa: F811
    root = make_repo(tmp_path / "r", specs={"flaky.spec.ts": """\
        import { test, expect } from '@playwright/test';
        test('flaky once', async ({ page }, info) => { await page.goto('/'); expect(info.retry).toBeGreaterThan(0); });
        """}, features=FEATURES.replace("title.spec.ts", "flaky.spec.ts"))
    code, out = _cli(root, "replay")
    assert code == 3 and out["counts"]["flaky"] == 2
    # a target that cannot start: nothing judged, exit 2, not_run with the reason
    bad = make_repo(tmp_path / "b", specs={"title.spec.ts": TITLE}, features=FEATURES,
                    target='id: web\nkind: web\nprepare: [["false"]]\n')
    code, out = _cli(bad, "replay")
    assert code == 2 and {r["result"] for r in out["results"]} == {"not_run"}
    assert {r["reason"] for r in out["results"]} == {"dependencies missing"}


# A page server whose in-memory value a start state's setup changes (POST /value), and
# whose GET /state says that value, whether {state_dir}/marker exists, and its own pid.
# Each start appends its pid to {sandbox}/pids.log. FAIL_IN_STATE: exit in that state.
STATE_SVC = """\
import http.server, json, os, sys
port, state_dir, pids = int(sys.argv[1]), sys.argv[2], sys.argv[3]
if os.path.basename(state_dir) == os.environ.get("FAIL_IN_STATE"):
    raise SystemExit(3)
open(pids, "a").write(f"{os.getpid()}\\n")
value = {"v": "initial"}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            body = json.dumps({"value": value["v"], "pid": os.getpid(),
                               "marker": os.path.exists(os.path.join(state_dir, "marker"))}).encode()
        else:
            body = b"<!doctype html><h1 data-testid='title'>Hello</h1>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        value["v"] = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
"""

STATE_TARGET = """\
id: web
kind: web
root: site/
prepare:
  - [python3, -c, "open('{sandbox}/prepare.log', 'a').write('x\\\\n')"]
services:
  - id: ui
    argv: [python3, "{checkout}/svc.py", "{port.ui}", "{state_dir}", "{sandbox}/pids.log"]
    ready: "http://127.0.0.1:{port.ui}/"
base_url: "http://127.0.0.1:{port.ui}/"
env: {FAIL_IN_STATE: "%s"}
start_states:
  a:
    setup:
      - [python3, -c, "open('{state_dir}/marker', 'w').write('1')"]
      - [python3, -c, "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:{port.ui}/value', data=b'changed', method='POST'))"]
  b: {}
  c:
    setup:
      - [python3, -c, "open('{state_dir}/marker', 'w').write('1')"]
"""


def _state_spec(state: str, value: str, marker: bool) -> str:
    return f"""\
import {{ test, expect }} from '@playwright/test';
test('sees state {state}', {{ tag: '@start:{state}' }}, async ({{ page }}) => {{
  const s = await (await page.goto('/state'))!.json();
  expect(s).toMatchObject({{ value: '{value}', marker: {str(marker).lower()} }});
}});
"""


STATE_FEATURES = """\
schema: 1
features:
  - {id: state-a1, title: State a, target: web, spec: specs/a.spec.ts}
  - {id: state-b2, title: State b, target: web, spec: specs/b.spec.ts}
  - {id: state-c4, title: State c, target: web, spec: specs/c.spec.ts}
  - {id: cmd-c3, title: Command walk in default, target: web, run: [python3, -c, "import json, sys, urllib.request as u; s = json.load(u.urlopen('http://127.0.0.1:{port.ui}/state')); sys.exit(0 if s['value'] == 'initial' and not s['marker'] else 1)"]}
"""


def test_start_states_start_from_fresh_services(tmp_path, app_data, runtime_env):  # noqa: F811
    """State a's setup writes a file under {state_dir} and changes the running service's
    in-memory value: a walk of state b sees neither, a walk of state a sees both; the
    command walk runs in state default, so neither a's nor the last state c's setup
    reaches it; every state ran on a new process, and prepare ran once."""
    root = make_repo(tmp_path / "r", target=STATE_TARGET % "", features=STATE_FEATURES,
                     specs={"a.spec.ts": _state_spec("a", "changed", True),
                            "b.spec.ts": _state_spec("b", "initial", False),
                            "c.spec.ts": _state_spec("c", "initial", True)},
                     extra={"svc.py": STATE_SVC})
    fmap = files.load_folder(root / ".swarmai/features")
    results, sb = replay(fmap, root, Selection(list(fmap.features)))
    try:
        by = {(r["feature"], r["project"]): r for r in results}
        for project in ("regular", "compact"):
            assert by[("state-a1", project)]["result"] == "pass", by[("state-a1", project)]
            assert by[("state-b2", project)]["result"] == "pass", by[("state-b2", project)]
            assert by[("state-c4", project)]["result"] == "pass", by[("state-c4", project)]
        assert by[("cmd-c3", "command")]["result"] == "pass", by[("cmd-c3", "command")]
        pids = (sb / "pids.log").read_text().split()
        assert len(pids) == 4 and len(set(pids)) == 4, f"one fresh service per state: {pids}"
        assert (sb / "prepare.log").read_text().split() == ["x"], "prepare runs once per target"
        assert (sb / "state/a/marker").is_file() and not (sb / "state/b/marker").exists()
        assert sorted(p.name for p in (sb / "state").iterdir()) == [".prepare", "a", "b", "c", "default"]
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def test_a_state_whose_services_fail_to_start_is_not_run(tmp_path, app_data, runtime_env):  # noqa: F811
    """The services do not come up for state b: b's walks are not_run with the start's
    reason; the states before and after it (a, c) start again and are judged."""
    root = make_repo(tmp_path / "r", target=STATE_TARGET % "b", features=STATE_FEATURES,
                     specs={"a.spec.ts": _state_spec("a", "changed", True),
                            "b.spec.ts": _state_spec("b", "initial", False),
                            "c.spec.ts": _state_spec("c", "initial", True)},
                     extra={"svc.py": STATE_SVC})
    fmap = files.load_folder(root / ".swarmai/features")
    results, sb = replay(fmap, root, Selection(list(fmap.features)))
    shutil.rmtree(sb, ignore_errors=True)
    by = {r["feature"]: r for r in results}
    assert by["state-a1"]["result"] == "pass" and by["cmd-c3"]["result"] == "pass", results
    assert by["state-c4"]["result"] == "pass", by["state-c4"]
    rows = [r for r in results if r["feature"] == "state-b2"]
    assert rows and all(r["result"] == "not_run" and r["reason"] == "launch failed" for r in rows), rows
