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
