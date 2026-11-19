"""Feature Map reach: which features a change can touch, by target (R6.1, R6.2)."""
from __future__ import annotations

from pathlib import Path

from core.feature_map import files, reach
from tests.feature_map_fixtures import git


def _map(tmp_path: Path) -> files.FeatureMap:
    f = tmp_path / ".swarmai" / "features"
    (f / "targets").mkdir(parents=True)
    (f / "specs").mkdir()
    for name in ("ui.spec.ts", "api.spec.ts"):
        (f / "specs" / name).write_text("// s\n")
    (f / "targets" / "web.yaml").write_text("id: web\nkind: web\nroot: desktop/\npaths: [shared/ui/]\nneeds: [api]\n")
    (f / "targets" / "api.yaml").write_text("id: api\nkind: command\nroot: backend/\n")
    (f / "targets" / "whole.yaml").write_text("id: whole\nkind: command\n")
    (f / "features.yaml").write_text(
        "schema: 1\nfeatures:\n"
        "  - {id: ui-a1, title: UI, target: web, spec: specs/ui.spec.ts}\n"
        "  - {id: api-b2, title: API, target: api, run: [\"true\"]}\n"
        "  - {id: whole-c3, title: Whole, target: whole, run: [\"true\"]}\n")
    return files.load_folder(f)


def test_affected_by_target(tmp_path):
    fmap = _map(tmp_path)
    rel = ".swarmai/features"
    # under a root: that target
    a = reach.affected(fmap, ["desktop/src/App.tsx"], folder_rel=rel)
    assert a["targets"] == ["web"] and a["features"] == ["ui-a1"] and a["unplaced"] == []
    # under another target's extra paths
    assert reach.affected(fmap, ["shared/ui/button.tsx"], folder_rel=rel)["targets"] == ["web"]
    # a target that needs a reached one is reached too
    a = reach.affected(fmap, ["backend/main.py"], folder_rel=rel)
    assert a["targets"] == ["api", "web"] and set(a["features"]) == {"ui-a1", "api-b2"}
    # R6.2: a path under no target is unplaced, reaches the repo-root target, never "none"
    a = reach.affected(fmap, ["README.md"], folder_rel=rel)
    assert a["unplaced"] == ["README.md"] and a["targets"] == ["whole"]
    assert a["features"] == ["whole-c3"]
    # a change to a spec reaches its feature; features.yaml reaches all
    # a file in the folder no feature names (a helper, a stray spec): every feature
    assert len(reach.affected(fmap, [f"{rel}/specs/api.spec.ts"], folder_rel=rel)["features"]) == 3
    assert reach.affected(fmap, [f"{rel}/specs/ui.spec.ts"], folder_rel=rel)["features"] == ["ui-a1"]
    assert len(reach.affected(fmap, [f"{rel}/features.yaml"], folder_rel=rel)["features"]) == 3


def test_changed_paths_count_untracked(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a")
    base = git(root, "rev-parse", "HEAD")
    (root / "a.txt").write_text("changed")
    (root / "new.txt").write_text("untracked")
    assert reach.changed_paths(root, base) == ["a.txt", "new.txt"]
