"""Tests for scripts/sync_discussions.py — the docs/discussions mirror sync.

Methodology: a FAKE fetcher injects Discussion lists so the real
path-resolution / content-generation / classification / index-rebuild logic
runs WITHOUT network. Each test asserts a real behavior (not a data shape);
key invariants (idempotency, number-key reuse, --check no-write) are the ones
that would have prevented this session's drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sync_discussions as sd  # noqa: E402


def mk(number, title, body="Body text.", created="2026-05-18T00:00:00Z",
       last_edited=None, category="General"):
    return sd.Discussion(number=number, title=title, body=body,
                         created_at=created, last_edited_at=last_edited,
                         category=category)


# --------------------------------------------------------------------------- #
# slugify                                                                     #
# --------------------------------------------------------------------------- #

def test_slugify_basic():
    assert sd.slugify("Coding as Black Box — one requirement in") == "coding-as-black-box-one-requirement-in"


def test_slugify_preserves_cjk():
    assert "记忆" in sd.slugify("撑起 SwarmAI 记忆 / DDD 的那套 Ontology")


def test_slugify_trims_on_hyphen_boundary():
    s = sd.slugify("a" * 80)
    assert len(s) <= 60


def test_slugify_no_leading_trailing_hyphen():
    s = sd.slugify("!! Hello, World !!")
    assert not s.startswith("-") and not s.endswith("-")


# --------------------------------------------------------------------------- #
# updated_date — the idempotency pivot                                        #
# --------------------------------------------------------------------------- #

def test_updated_date_uses_last_edited_when_present():
    d = mk(5, "T", created="2026-05-18T00:00:00Z", last_edited="2026-07-10T09:00:00Z")
    assert d.updated_date == "2026-07-10"


def test_updated_date_falls_back_to_created():
    d = mk(5, "T", created="2026-05-18T00:00:00Z", last_edited=None)
    assert d.updated_date == "2026-05-18"


# --------------------------------------------------------------------------- #
# index_mirror_files — number-key                                             #
# --------------------------------------------------------------------------- #

def test_index_mirror_files_by_number(tmp_path):
    (tmp_path / "05-content-as-black-box.md").write_text("x")
    (tmp_path / "96-撑起-ontology.md").write_text("x")
    (tmp_path / "README.md").write_text("x")  # not numbered → ignored
    idx = sd.index_mirror_files(tmp_path)
    assert set(idx) == {5, 96}
    assert idx[96].name == "96-撑起-ontology.md"


# --------------------------------------------------------------------------- #
# render_file — frontmatter + preservation                                    #
# --------------------------------------------------------------------------- #

def test_render_file_has_lint_valid_frontmatter():
    d = mk(5, 'Title with "quotes"')
    out = sd.render_file(d, None)
    fm = sd.parse_frontmatter(out)
    assert fm.get("created") == "2026-05-18"
    assert fm.get("updated") == "2026-05-18"
    assert fm.get("title")  # non-empty
    assert out.startswith("---")
    assert f"<!-- GitHub Discussion #5:" in out


def test_render_preserves_created_and_editorial_header_on_update():
    existing = (
        "---\n"
        'title: "Old"\n'
        "created: 2026-05-18\n"
        "updated: 2026-05-18\n"
        "status: published\n"
        "---\n"
        "<!-- GitHub Discussion #5: url -->\n"
        "> 🌐 English | 中文版 → #6 · Series: #4\n"
        "\n"
        "old body\n"
    )
    d = mk(5, "New Title", body="new body", created="2099-01-01T00:00:00Z",
           last_edited="2026-07-10T00:00:00Z")
    out = sd.render_file(d, existing)
    # created preserved from existing (NOT the bogus 2099 live createdAt)
    assert sd.parse_frontmatter(out)["created"] == "2026-05-18"
    # editorial cross-ref line preserved
    assert "> 🌐 English | 中文版 → #6 · Series: #4" in out
    # body + title updated
    assert "new body" in out and "old body" not in out
    assert sd.parse_frontmatter(out)["title"] == "New Title"


# --------------------------------------------------------------------------- #
# classify — add / update / unchanged                                         #
# --------------------------------------------------------------------------- #

def test_editorial_header_only_captures_xref_not_body_blockquote():
    # header zone = the 🌐/#N cross-ref line ONLY; a body that leads with a
    # blockquote (`> **In one line**...`) must NOT be pulled into the header.
    existing = (
        "---\ntitle: \"T\"\ncreated: 2026-07-08\nupdated: 2026-07-08\nstatus: published\n---\n"
        "<!-- GitHub Discussion #95: url -->\n"
        "> 🌐 English | 中文版 → #96 · Related: #20\n"
        "\n"
        "> **In one line**: this is BODY, not header.\n"
        "\nrest of body\n"
    )
    hdr = sd.extract_editorial_header(existing)
    assert hdr == ["> 🌐 English | 中文版 → #96 · Related: #20"]
    assert not any("In one line" in l for l in hdr)


def test_render_does_not_duplicate_body_blockquote():
    existing = (
        "---\ntitle: \"T\"\ncreated: 2026-07-08\nupdated: 2026-07-08\nstatus: published\n---\n"
        "<!-- GitHub Discussion #95: url -->\n"
        "> 🌐 English | 中文版 → #96\n\n"
        "> **In one line**: body lead.\n\nrest\n"
    )
    d = mk(95, "T", body="> **In one line**: body lead.\n\nrest",
           created="2026-07-08T00:00:00Z", last_edited="2026-07-09T00:00:00Z")
    out = sd.render_file(d, existing)
    # the 🌐 line appears exactly once; the body blockquote appears exactly once
    assert out.count("🌐 English") == 1
    assert out.count("**In one line**") == 1


def test_body_leading_xref_blockquote_not_duplicated_on_update(tmp_path):
    """Gate-2 CRITICAL regression: a body that LEADS with a blockquote containing
    a #N ref (e.g. '> Related: #99') must NOT be pulled into the editorial header
    and duplicated on a genuine re-edit. This is the exact bug the adversarial
    gate caught that all 22 prior tests missed."""
    body_v1 = "> Related: #99\n\nreal body v1"
    d1 = mk(50, "T", body=body_v1, created="2026-05-18T00:00:00Z")
    p = tmp_path / "50-t.md"
    p.write_text(sd.render_file(d1, None), encoding="utf-8")

    # genuine live edit → UPDATE path re-renders from the file it just wrote
    d2 = mk(50, "T", body="> Related: #99\n\nreal body v2 EDITED",
            created="2026-05-18T00:00:00Z", last_edited="2026-07-10T00:00:00Z")
    c = sd.classify(d2, {50: p}, tmp_path)
    assert c.kind == "update"
    # the cross-ref blockquote must appear EXACTLY ONCE, not duplicated
    assert c.content.count("> Related: #99") == 1


def test_null_body_does_not_crash(tmp_path):
    """Gate-2 HIGH: an empty discussion (body=None) must not crash the sync."""
    d = mk(51, "Empty", body=None)
    out = sd.render_file(d, None)  # must not raise
    assert out.startswith("---")
    assert "<!-- GitHub Discussion #51:" in out


def test_rebuild_readme_preserves_adjacent_themes_table_without_separator():
    """Gate-2 MEDIUM: even with NO blank separator, an adjacent Themes table
    must not be consumed by the index-table rebuild."""
    readme = (
        "# H\n\n"
        "| # | Title | Category | Date |\n"
        "|---|-------|----------|------|\n"
        "| 2 | [Old](02-old.md) | General | 2026-05-18 |\n"
        "\n"
        "## Themes\n"
        "| Theme | Key Articles |\n"
        "|-------|-------------|\n"
        "| **Memory** | #3 |\n"
    )
    table = "| # | Title | Category | Date |\n|---|-------|----------|------|\n| 5 | [New](05-new.md) | Ideas | 2026-07-10 |"
    out = sd.rebuild_readme(readme, table)
    assert "**Memory** | #3" in out          # themes table survives
    assert "05-new.md" in out
    assert "02-old.md" not in out


def test_duplicate_number_prefix_raises(tmp_path):
    """Gate-2 LOW: two files for the same number → fail loud, not silent clobber."""
    (tmp_path / "2-foo.md").write_text("x")
    (tmp_path / "02-bar.md").write_text("x")
    with pytest.raises(ValueError, match="Duplicate mirror files for discussion #2"):
        sd.index_mirror_files(tmp_path)


def test_classify_add_for_new_number(tmp_path):
    d = mk(99, "Brand New")
    c = sd.classify(d, {}, tmp_path)
    assert c.kind == "add"
    assert c.path.name == "99-brand-new.md"


def test_classify_unchanged_is_idempotent(tmp_path):
    d = mk(5, "Stable")
    p = tmp_path / "05-stable.md"
    p.write_text(sd.render_file(d, None), encoding="utf-8")
    c = sd.classify(d, {5: p}, tmp_path)
    assert c.kind == "unchanged"


def test_classify_update_when_body_edited(tmp_path):
    d0 = mk(5, "T", body="v1")
    p = tmp_path / "05-t.md"
    p.write_text(sd.render_file(d0, None), encoding="utf-8")
    d1 = mk(5, "T", body="v2 edited", last_edited="2026-07-10T00:00:00Z")
    c = sd.classify(d1, {5: p}, tmp_path)
    assert c.kind == "update"
    assert "v2 edited" in c.content


def test_classify_reuses_existing_filename_not_new_slug(tmp_path):
    # existing file has a DIFFERENT slug than a fresh slugify would produce,
    # and a genuine live edit (so it classifies as update, not unchanged)
    p = tmp_path / "5-old-truncated-slug-i.md"
    d0 = mk(5, "A Much Longer Title That Would Slugify Differently Now")
    p.write_text(sd.render_file(d0, None), encoding="utf-8")
    d1 = mk(5, "A Much Longer Title That Would Slugify Differently Now",
            body="edited", last_edited="2026-07-10T00:00:00Z")
    c = sd.classify(d1, {5: p}, tmp_path)
    assert c.kind == "update"
    # must REUSE the existing filename — never create a second file
    assert c.path == p
    assert c.path.name == "5-old-truncated-slug-i.md"


def test_classify_legacy_skip_for_no_frontmatter_file(tmp_path):
    # a legacy mirror file with the old `# H1 + > 📎` format (no frontmatter)
    p = tmp_path / "10-legacy.md"
    p.write_text("# Legacy Title\n\n> 📎 [View](url) | General\n\nbody\n", encoding="utf-8")
    d = mk(10, "Legacy Title", last_edited="2026-07-10T00:00:00Z")
    c = sd.classify(d, {10: p}, tmp_path)
    assert c.kind == "legacy_skip"          # never auto-reformat legacy files
    assert c.content == ""                   # no desired content to write


def test_run_write_does_not_reformat_legacy(tmp_path):
    # legacy file present + a genuine live edit date → still NOT rewritten
    p = tmp_path / "10-legacy.md"
    original = "# Legacy Title\n\n> 📎 [View](url) | General\n\nbody\n"
    p.write_text(original, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# I\n\n| # | Title | Category | Date |\n|---|-------|----------|------|\n"
        "| 10 | [Legacy Title](10-legacy.md) | General | 2026-05-18 |\n\n## Themes\nkeep\n",
        encoding="utf-8",
    )
    fetch = lambda: [mk(10, "Legacy Title", last_edited="2026-07-10T00:00:00Z", created="2026-05-18T00:00:00Z")]
    sd.run("write", docs_dir=tmp_path, fetcher=fetch)
    assert p.read_text() == original         # legacy file untouched


# --------------------------------------------------------------------------- #
# README index rebuild                                                        #
# --------------------------------------------------------------------------- #

def test_rebuild_readme_replaces_table_preserves_themes():
    readme = (
        "# Header\n\n"
        "| # | Title | Category | Date |\n"
        "|---|-------|----------|------|\n"
        "| 2 | [Old](02-old.md) | General | 2026-05-18 |\n"
        "\n"
        "## Themes\n"
        "| Theme | Key Articles |\n"
        "| **Memory** | #3 |\n"
    )
    table = "| # | Title | Category | Date |\n|---|-------|----------|------|\n| 5 | [New](05-new.md) | Ideas | 2026-07-10 |"
    out = sd.rebuild_readme(readme, table)
    assert "05-new.md" in out
    assert "02-old.md" not in out           # old table row replaced
    assert "## Themes" in out               # themes preserved
    assert "**Memory** | #3" in out         # themes content preserved


def test_rebuild_readme_raises_if_no_table():
    with pytest.raises(ValueError):
        sd.rebuild_readme("# No table here\n", "| # | Title | Category | Date |\n")


# --------------------------------------------------------------------------- #
# run() — end-to-end with fake fetcher                                        #
# --------------------------------------------------------------------------- #

def _seed_docs(tmp_path):
    d5 = mk(5, "Existing", body="orig")
    (tmp_path / "05-existing.md").write_text(sd.render_file(d5, None), encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# I\n\n| # | Title | Category | Date |\n|---|-------|----------|------|\n"
        "| 5 | [Existing](05-existing.md) | General | 2026-05-18 |\n\n## Themes\nkeep\n",
        encoding="utf-8",
    )
    return d5


def test_run_check_writes_nothing(tmp_path):
    _seed_docs(tmp_path)
    new = mk(6, "Added Live")
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}
    rc = sd.run("check", docs_dir=tmp_path, fetcher=lambda: [mk(5, "Existing", body="orig"), new])
    after = {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert rc == 1                          # drift (missing #6)
    assert before == after                  # NOTHING written in check mode
    assert not (tmp_path / "06-added-live.md").exists()


def test_run_write_then_check_is_idempotent(tmp_path):
    _seed_docs(tmp_path)
    fetch = lambda: [mk(5, "Existing", body="orig"), mk(6, "Added Live")]
    rc1 = sd.run("write", docs_dir=tmp_path, fetcher=fetch)
    assert rc1 == 0
    assert (tmp_path / "06-added-live.md").exists()
    # second write → 0 changes; check → exit 0
    rc_check = sd.run("check", docs_dir=tmp_path, fetcher=fetch)
    assert rc_check == 0                     # idempotent: no drift after write


def test_run_write_confined_to_docs_dir(tmp_path):
    _seed_docs(tmp_path)
    fetch = lambda: [mk(5, "Existing", body="orig"), mk(7, "New")]
    sd.run("write", docs_dir=tmp_path, fetcher=fetch)
    # every file touched is under tmp_path (docs_dir) — nothing escapes
    for p in tmp_path.rglob("*"):
        assert tmp_path in p.parents or p == tmp_path
