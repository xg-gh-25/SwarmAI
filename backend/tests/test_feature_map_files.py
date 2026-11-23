"""Feature Map files: strict schemas (R2.1) and where the folder lives (R2.2-R2.4)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from core.feature_map import files

FEATURES = """\
schema: 1
features:
  - id: home-a1b2
    title: Home opens
    target: web
    spec: specs/home.spec.ts
"""
TARGET = "id: web\nkind: web\n"


def _folder(tmp_path: Path, features: str = FEATURES, target: str = TARGET) -> Path:
    f = tmp_path / "f"
    (f / "targets").mkdir(parents=True)
    (f / "specs").mkdir()
    (f / "specs" / "home.spec.ts").write_text("// spec\n")
    (f / "features.yaml").write_text(features)
    (f / "targets" / "web.yaml").write_text(target)
    return f


def test_unknown_key_names_file_and_key(tmp_path):
    ok = files.load_folder(_folder(tmp_path / "ok"))
    assert [f.id for f in ok.features] == ["home-a1b2"] and "web" in ok.targets
    cases = [
        (FEATURES.replace("    target: web\n", "    target: web\n    colour: red\n"), TARGET,
         "features.yaml", "features[0].colour", "unknown key"),
        (FEATURES.replace("    title: Home opens\n", ""), TARGET,
         "features.yaml", "features[0].title", "required key missing"),
        (FEATURES.replace("schema: 1", "schema: 3"), TARGET, "features.yaml", "schema", "newer"),
        (FEATURES, TARGET + "prepare: ['npm ci']\n", "targets/web.yaml", "prepare[0]",
         "never a shell string"),
        (FEATURES, TARGET + "speed: fast\n", "targets/web.yaml", "speed", "unknown key"),
        (FEATURES.replace("specs/home.spec.ts", "specs/none.spec.ts"), TARGET,
         "features.yaml", "features[0].spec", "no such file"),
        (FEATURES.replace("target: web", "target: api"), TARGET,
         "features.yaml", "features[0].target", "no target api"),
    ]
    for i, (features, target, file, key, words) in enumerate(cases):
        with pytest.raises(files.FeatureMapError) as exc:
            files.load_folder(_folder(tmp_path / str(i), features, target))
        assert exc.value.file == file and exc.value.key == key, (i, str(exc.value))
        assert words in exc.value.problem, (i, exc.value.problem)


def _brain(tmp_path: Path, worktree: Path) -> Path:
    p = tmp_path / "Projects" / "B"
    p.mkdir(parents=True)
    (p / "bindings.yaml").write_text(yaml.safe_dump({"bindings": [{
        "repo": "app", "kind": "external", "clone": "https://example.com/app.git",
        "worktree": str(worktree), "delivery_contract": {"remote_kind": "github-pr", "branch": "main",
        "review_path": "pr", "auto_send": "never"}}]}))
    return p


def test_location_recorded_once_and_followed(tmp_path, monkeypatch):
    checkout = tmp_path / "repo"
    checkout.mkdir()
    brain = _brain(tmp_path, checkout)
    # nothing recorded and no folder: no map
    assert files.location(brain, "app", checkout) == (None, "none")
    # a working checkout: repo, recorded on the binding
    monkeypatch.setattr(files, "rule", lambda _p, _r: "repo")
    folder, mode = files.choose_location(brain, "app", checkout)
    assert (folder, mode) == (checkout / ".swarmai" / "features", "repo")
    assert files.recorded(brain, "app") == "repo"
    assert list(brain.glob("bindings.yaml.bak-*")), "bindings.yaml backed up before the rewrite"
    from core.ddd_bindings import load_bindings
    assert load_bindings(brain / "bindings.yaml").bindings[0].features == "repo"
    # the recorded value is followed, not re-chosen (a mirror rule now says brain)
    monkeypatch.setattr(files, "rule", lambda _p, _r: "brain")
    assert files.choose_location(brain, "app", checkout)[1] == "repo"
    (folder / "targets").mkdir(parents=True)
    (folder / "features.yaml").write_text("schema: 1\nfeatures: []\n")
    assert files.location(brain, "app", checkout) == (folder, "repo")
    # a mirror (or the person asked): brain
    brain2 = _brain(tmp_path / "two", checkout)
    folder2, mode2 = files.choose_location(brain2, "app", checkout)
    assert mode2 == "brain" and folder2 == brain2 / ".artifacts" / "feature-map" / "app"
    brain3 = _brain(tmp_path / "three", checkout)
    monkeypatch.setattr(files, "rule", lambda _p, _r: "repo")
    assert files.choose_location(brain3, "app", checkout, keep_out_of_repo=True)[1] == "brain"
    # recorded brain wins over a repo folder that exists (after a relocate the value holds)
    (folder2 / "targets").mkdir(parents=True)
    (folder2 / "features.yaml").write_text("schema: 1\nfeatures: []\n")
    assert files.location(brain2, "app", checkout) == (folder2, "brain")


def test_rule_by_checkout_kind(tmp_path, monkeypatch):
    import core.repo_placement as rp
    monkeypatch.setattr(rp, "placement", lambda _p: {"app": {"checkout": "mirror"}})
    assert files.rule(tmp_path, "app") == "brain"
    monkeypatch.setattr(rp, "placement", lambda _p: {"app": {"checkout": "local"}})
    assert files.rule(tmp_path, "app") == "repo"
    monkeypatch.setattr(rp, "placement", lambda _p: {"app": {"checkout": "repos_dir:app"}})
    assert files.rule(tmp_path, "app") == "repo"


def test_content_hash_changes_with_the_spec(tmp_path):
    f = _folder(tmp_path)
    fmap = files.load_folder(f)
    h1 = files.content_hash(fmap, fmap.features[0])
    (f / "specs" / "home.spec.ts").write_text("// edited\n")
    assert files.content_hash(fmap, fmap.features[0]) != h1


def test_content_hash_follows_the_specs_local_imports(tmp_path):
    """A shared helper a spec imports changes the walk as much as the spec does; a spec
    without local imports keeps the hash it had before imports were counted."""
    f = _folder(tmp_path)
    fmap = files.load_folder(f)
    spec = f / "specs" / "home.spec.ts"
    spec.write_text("import { test } from '@playwright/test';\n")
    import hashlib, json
    plain = hashlib.sha256(json.dumps(fmap.features[0].raw, sort_keys=True, ensure_ascii=False).encode())
    plain.update(spec.read_bytes())
    assert files.content_hash(fmap, fmap.features[0]) == plain.hexdigest()[:16], "unchanged without imports"
    spec.write_text("import { test } from '@playwright/test';\nimport { open } from './helpers';\n")
    (f / "specs" / "helpers.ts").write_text("import { more } from './lib/more';\nexport const open = 1;\n")
    (f / "specs" / "lib").mkdir()
    (f / "specs" / "lib" / "more.ts").write_text("export const more = 1;\n")
    h1 = files.content_hash(fmap, fmap.features[0])
    (f / "specs" / "helpers.ts").write_text("import { more } from './lib/more';\nexport const open = 2;\n")
    h2 = files.content_hash(fmap, fmap.features[0])
    assert h2 != h1, "editing the helper starts the walk over"
    (f / "specs" / "lib" / "more.ts").write_text("export const more = 2;\n")
    assert files.content_hash(fmap, fmap.features[0]) != h2, "and a file the helper imports"
    outside = tmp_path / "outside.ts"
    outside.write_text("x")
    spec.write_text("import { x } from '../../../outside';\n")
    h3 = files.content_hash(fmap, fmap.features[0])
    outside.write_text("y")
    assert files.content_hash(fmap, fmap.features[0]) == h3, "nothing outside the folder is read"
