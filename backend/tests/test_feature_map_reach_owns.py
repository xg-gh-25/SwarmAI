"""Feature Map reach with a target's ``ignore`` and a feature's ``owns`` (design
2026-11-23-feature-map-replay-conditions §3; requirements R1-R5, R8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.feature_map import files, reach

REL = ".swarmai/features"


def _folder(tmp_path: Path, targets: dict[str, str], features: str, schema: int = 2) -> Path:
    f = tmp_path / ".swarmai" / "features"
    (f / "targets").mkdir(parents=True, exist_ok=True)
    (f / "specs").mkdir(exist_ok=True)
    for tid, body in targets.items():
        (f / "targets" / f"{tid}.yaml").write_text(body)
    (f / "features.yaml").write_text(f"schema: {schema}\nfeatures:\n{features}")
    return f


def _web(tmp_path: Path, owns_chat: str = "[app/pages/chat/]") -> files.FeatureMap:
    """One web target over app/ and api/, three features: chat owns its page, settings
    owns its page and route, home owns nothing."""
    for d in ("app/pages/chat", "app/pages/settings", "app/shell", "api/routes", "app/tests"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "api/routes/settings.py").write_text("")
    f = _folder(tmp_path, {
        "app": "id: app\nkind: command\nroot: app/\npaths: [api/]\n"
               "ignore: [\"app/tests/**\", \"**/*.md\"]\n"},
        f"  - {{id: chat-a1, title: Chat, target: app, run: [\"true\"], owns: {owns_chat}}}\n"
        "  - {id: settings-b2, title: Settings, target: app, run: [\"true\"],"
        " owns: [app/pages/settings/, api/routes/settings.py]}\n"
        "  - {id: home-c3, title: Home, target: app, run: [\"true\"]}\n")
    return files.load_folder(f)


def test_owns_narrows_only_when_every_path_is_owned(tmp_path):
    fmap = _web(tmp_path)
    # R2.1: an owned path selects its owner and the feature that owns nothing
    a = reach.affected(fmap, ["app/pages/chat/Chat.tsx"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "home-c3"]
    assert a["why"]["chat-a1"] == "it owns app/pages/chat/Chat.tsx"
    # two owned paths of two features: both, never the third owner-less path's rule
    a = reach.affected(fmap, ["app/pages/chat/Chat.tsx", "api/routes/settings.py"],
                       root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "settings-b2", "home-c3"]
    a = reach.affected(fmap, ["api/routes/settings.py"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["settings-b2", "home-c3"]
    # R2.2: a path no feature owns (the shell) selects every feature of the target
    a = reach.affected(fmap, ["app/pages/chat/Chat.tsx", "app/shell/App.tsx"],
                       root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "settings-b2", "home-c3"]
    assert a["why"]["settings-b2"] == "app/shell/App.tsx is owned by no feature of app"


def test_a_target_reached_through_needs_selects_all_its_features(tmp_path):
    # R2.3: web needs api; an api change reaches web, and every web feature, owned or not
    for d in ("web/chat", "web/other"):
        (tmp_path / d).mkdir(parents=True)
    f = _folder(tmp_path, {
        "web": "id: web\nkind: command\nroot: web/\nneeds: [api]\n",
        "api": "id: api\nkind: command\nroot: api/\n"},
        "  - {id: chat-a1, title: Chat, target: web, run: [\"true\"], owns: [web/chat/]}\n"
        "  - {id: other-d4, title: Other, target: web, run: [\"true\"], owns: [web/other/]}\n"
        "  - {id: api-b2, title: API, target: api, run: [\"true\"]}\n")
    fmap = files.load_folder(f)
    a = reach.affected(fmap, ["api/main.py"], root=tmp_path, folder_rel=REL)
    assert a["targets"] == ["api", "web"] and a["features"] == ["chat-a1", "other-d4", "api-b2"]
    # also when web is reached directly at the same time by a path only chat owns
    a = reach.affected(fmap, ["api/main.py", "web/chat/x.ts"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "other-d4", "api-b2"]
    # without the api change, the owned path narrows to its owner
    a = reach.affected(fmap, ["web/chat/x.ts"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1"]


def test_ignore_drops_a_path_and_lists_it(tmp_path):
    fmap = _web(tmp_path)
    # R1.1: an ignored path reaches nothing and is listed
    a = reach.affected(fmap, ["app/tests/test_chat.py", "app/pages/chat/README.md"],
                       root=tmp_path, folder_rel=REL)
    assert a["targets"] == [] and a["features"] == []
    assert a["ignored"] == ["app/pages/chat/README.md", "app/tests/test_chat.py"]
    assert a["unplaced"] == []
    # an ignored path beside a real change does not widen or narrow it
    a = reach.affected(fmap, ["app/tests/x.py", "app/pages/chat/Chat.tsx"],
                       root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "home-c3"] and a["ignored"] == ["app/tests/x.py"]


def test_a_path_every_root_target_ignores_is_ignored_not_unplaced(tmp_path):
    # R1.2: a repo-root target that ignores docs: a doc change is ignored, not unplaced
    f = _folder(tmp_path, {
        "whole": "id: whole\nkind: command\nignore: [\"docs/**\"]\n"},
        "  - {id: whole-c3, title: Whole, target: whole, run: [\"true\"]}\n")
    fmap = files.load_folder(f)
    a = reach.affected(fmap, ["docs/guide.md"], root=tmp_path, folder_rel=REL)
    assert a["ignored"] == ["docs/guide.md"] and a["unplaced"] == [] and a["targets"] == []
    a = reach.affected(fmap, ["src/main.go"], root=tmp_path, folder_rel=REL)
    assert a["unplaced"] == ["src/main.go"] and a["targets"] == ["whole"]


def test_a_stale_prefix_falls_back_to_the_whole_target(tmp_path):
    # R3.1: chat's owned folder was moved away: chat owns nothing, so it is always selected
    fmap = _web(tmp_path, owns_chat="[app/pages/chat-old/]")
    a = reach.affected(fmap, ["api/routes/settings.py"], root=tmp_path, folder_rel=REL)
    assert a["stale"] == [{"feature": "chat-a1", "path": "app/pages/chat-old"}]
    assert a["features"] == ["chat-a1", "settings-b2", "home-c3"]
    # and the moved code itself, owned by nobody now, selects every feature
    a = reach.affected(fmap, ["app/pages/chat/Chat.tsx"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["chat-a1", "settings-b2", "home-c3"]


def test_the_rules_do_not_depend_on_a_language(tmp_path):
    # R8.1: a command target over a Go-style tree, with the same declarations
    for d in ("cmd/serve", "internal/store", "internal/api"):
        (tmp_path / d).mkdir(parents=True)
    f = _folder(tmp_path, {
        "cli": "id: cli\nkind: command\nroot: \"\"\npaths: [cmd/, internal/]\n"
               "ignore: [\"**/*_test.go\"]\n"},
        "  - {id: serve-a1, title: Serve, target: cli, run: [\"true\"], owns: [cmd/serve/]}\n"
        "  - {id: store-b2, title: Store, target: cli, run: [\"true\"], owns: [internal/store/]}\n")
    fmap = files.load_folder(f)
    a = reach.affected(fmap, ["internal/store/db.go", "internal/store/db_test.go"],
                       root=tmp_path, folder_rel=REL)
    assert a["features"] == ["store-b2"] and a["ignored"] == ["internal/store/db_test.go"]
    a = reach.affected(fmap, ["internal/api/h.go"], root=tmp_path, folder_rel=REL)
    assert a["features"] == ["serve-a1", "store-b2"]


def test_owns_is_not_part_of_the_walk_identity(tmp_path):
    fmap = _web(tmp_path)
    before = files.content_hash(fmap, fmap.feature("chat-a1"))
    # R4.1: tuning owns keeps the hash
    fmap2 = _web(tmp_path, owns_chat="[app/pages/chat/, app/shell/]")
    assert files.content_hash(fmap2, fmap2.feature("chat-a1")) == before
    # R4.2: any other field changes it
    text = (tmp_path / REL / "features.yaml").read_text().replace("title: Chat,", "title: Chats,")
    (tmp_path / REL / "features.yaml").write_text(text)
    fmap3 = files.load_folder(tmp_path / REL)
    assert files.content_hash(fmap3, fmap3.feature("chat-a1")) != before


@pytest.mark.parametrize("where", ["owns", "ignore"])
def test_the_new_fields_need_schema_2(tmp_path, where):
    # R5.1: schema 1 with a schema-2 field is refused naming the field and schema 2
    target = "id: app\nkind: command\nroot: app/\n" + ("ignore: [x/]\n" if where == "ignore" else "")
    feature = ("  - {id: a-a1, title: A, target: app, run: [\"true\"]"
               + (", owns: [app/]}\n" if where == "owns" else "}\n"))
    f = _folder(tmp_path, {"app": target}, feature, schema=1)
    with pytest.raises(files.FeatureMapError) as e:
        files.load_folder(f)
    assert e.value.key.endswith(where) and "needs schema: 2" in e.value.problem
    # R5.2: the same folder reads at schema 2, and schema 1 without the fields reads too
    _folder(tmp_path, {"app": target}, feature, schema=2)
    assert files.load_folder(f).feature("a-a1")
    _folder(tmp_path, {"app": "id: app\nkind: command\nroot: app/\n"},
            "  - {id: a-a1, title: A, target: app, run: [\"true\"]}\n", schema=1)
    assert files.load_folder(f).feature("a-a1").owns == ()
    # a schema newer than this reader is still refused as newer
    _folder(tmp_path, {"app": "id: app\nkind: command\n"},
            "  - {id: a-a1, title: A, target: app, run: [\"true\"]}\n", schema=3)
    with pytest.raises(files.FeatureMapError, match="newer"):
        files.load_folder(f)


def test_glob_match():
    assert reach.glob_match("**/*.md", "README.md")
    assert reach.glob_match("**/*.md", "a/b/c.md")
    assert not reach.glob_match("**/*.md", "a/b/c.mdx")
    assert reach.glob_match("app/tests/**", "app/tests/a/b.py")
    assert not reach.glob_match("app/tests/**", "app/tests")      # a trailing ** is what is under it
    assert reach.glob_match("**", "x") and reach.glob_match("a/**/b", "a/b")
    assert not reach.glob_match("app/tests/**", "app/testsx/b.py")
    assert reach.glob_match("a/*/c.py", "a/b/c.py") and not reach.glob_match("a/*/c.py", "a/b/x/c.py")
    assert reach.glob_match("docs", "docs/x.md") and not reach.glob_match("docs", "docsx/y")


def test_a_planted_glob_cannot_stall_the_match():
    import time
    path = "/".join(["d"] * 40) + "/y"
    t0 = time.monotonic()
    for pattern in ("/".join(["**"] * 30) + "/x", "*a*a*a*a*a*a*a*a*a*a*b"):
        assert not reach.glob_match(pattern, path)
        assert not reach.glob_match(pattern, "a" * 60)
    assert time.monotonic() - t0 < 0.5


def test_ignore_is_bounded(tmp_path):
    many = "[" + ", ".join(f"\"p{i}/**\"" for i in range(51)) + "]"
    f = _folder(tmp_path, {"app": f"id: app\nkind: command\nignore: {many}\n"},
                "  - {id: a-a1, title: A, target: app, run: [\"true\"]}\n")
    with pytest.raises(files.FeatureMapError, match="at most 50"):
        files.load_folder(f)
    _folder(tmp_path, {"app": "id: app\nkind: command\nignore: [\"" + "a" * 201 + "\"]\n"},
            "  - {id: a-a1, title: A, target: app, run: [\"true\"]}\n")
    with pytest.raises(files.FeatureMapError, match="longer than 200"):
        files.load_folder(f)
