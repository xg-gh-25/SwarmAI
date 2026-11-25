"""Versions (design section 11 item 4): old snapshots never change, old versions re-render the same.

A work goes through adopt, two cuts, a failed cut and a user version. After
every command, each file of every older snapshot must be byte for byte what it
was. Rendering an old version again must give slide PNGs with the sha256
recorded when it was cut.
"""
from __future__ import annotations

import hashlib
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    adopt_small, env, meta, need_browser, replace_in_slide, run, tree_hashes, version,
)

__all__ = ["env"]


def test_old_versions_immutable_and_rerender_identical(env, capsys):
    need_browser()
    d = env["tmp"] / "deck"
    ids = adopt_small(d, capsys)
    seen = {}

    def check_old_snapshots():
        for vid, hashes in seen.items():
            assert tree_hashes(d / ".versions" / vid) == hashes, f"{vid} changed"
        for v in meta(d)["versions"]:
            if v["id"] not in seen:
                seen[v["id"]] = tree_hashes(d / ".versions" / v["id"])

    check_old_snapshots()
    run(["open", "--dir", str(d), "--scope", ids[0]], capsys)
    replace_in_slide(d, ids[0], "Alpha", "Alpha one")
    assert run(["cut", "--dir", str(d), "--formats", "png"], capsys)[0] == 0
    check_old_snapshots()
    run(["open", "--dir", str(d), "--scope", ids[1]], capsys)
    replace_in_slide(d, ids[1], "Bravo", "Bravo two")
    replace_in_slide(d, ids[2], "Charlie", "Charlie out of scope")
    assert run(["cut", "--dir", str(d), "--formats", "png"], capsys)[0] == 1  # v003 failed
    check_old_snapshots()
    replace_in_slide(d, ids[2], "Charlie out of scope", "Charlie")
    assert run(["cut", "--dir", str(d), "--formats", "png"], capsys)[0] == 0  # v004
    check_old_snapshots()
    replace_in_slide(d, ids[3], "Delta", "Delta by hand")
    run(["open", "--dir", str(d), "--scope", ""], capsys)  # v004-user
    check_old_snapshots()
    assert [v["id"] for v in meta(d)["versions"]] == ["v001", "v002", "v003", "v004", "v004-user"]
    for path in (d / ".versions").rglob("*"):
        if path.is_file():
            assert not path.stat().st_mode & stat.S_IWUSR, f"{path} is writable"

    for vid in ("v002", "v004"):
        recorded = version(d, vid)["outputs"]["slides"]
        assert set(recorded) == set(ids)
        for attempt in (1, 2):
            out = env["tmp"] / f"again-{vid}-{attempt}"
            code, res = run(["render", "--dir", str(d), "--version", vid, "--out", str(out),
                             "--formats", "png"], capsys)
            assert code == 0, res
            got = {p.stem.split("-", 1)[1]: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (out / "slides").glob("*.png")}
            assert got == recorded, f"{vid} re-render {attempt} differs"
        check_old_snapshots()
    # the two versions differ where they should
    assert version(d, "v002")["outputs"]["slides"][ids[1]] != version(d, "v004")["outputs"]["slides"][ids[1]]
    assert version(d, "v002")["outputs"]["slides"][ids[2]] == version(d, "v004")["outputs"]["slides"][ids[2]]
