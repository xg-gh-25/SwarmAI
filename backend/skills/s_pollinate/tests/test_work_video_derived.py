"""work.py adopt --kind video --derived-from records which earlier film a new cut was made from.

- the earlier project's absolute path is stored as ``derived_from`` in work.json and in the
  registry entry; without the option neither has the key;
- the option is refused (exit 2, nothing written) for a non-video kind, for a folder that does
  not exist, and for the work itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import env, meta, run, work  # noqa: E402

__all__ = ["env"]


def _registry(env) -> list:
    p = env["tmp"] / "ws" / ".pollinate" / "works.json"
    return json.loads(p.read_text(encoding="utf-8"))["works"] if p.exists() else []


def _film(env, name: str) -> Path:
    d = env["tmp"] / name
    d.mkdir()
    (d / "storyboard.json").write_text(json.dumps({"title": name, "width": 1920, "height": 1080,
                                                   "scenes": [{"id": "S01"}]}), encoding="utf-8")
    return d


def test_adopt_video_records_derived_from(env, capsys):
    old = _film(env, "short-cut")
    new = _film(env, "long-cut")
    code, out = run(["adopt", "--dir", str(new), "--kind", "video", "--derived-from", str(old)], capsys)
    assert code == 0, out
    assert meta(new)["derived_from"] == str(old.resolve())
    entry = next(w for w in _registry(env) if w["path"] == str(new.resolve()))
    assert entry["derived_from"] == str(old.resolve())


def test_adopt_video_without_derived_from_has_no_key(env, capsys):
    d = _film(env, "film")
    code, out = run(["adopt", "--dir", str(d), "--kind", "video"], capsys)
    assert code == 0, out
    assert "derived_from" not in meta(d)
    assert all("derived_from" not in w for w in _registry(env))


def test_derived_from_refused_for_bad_input(env, capsys):
    new = _film(env, "long-cut")
    capsys.readouterr()
    assert work.main(["adopt", "--dir", str(new), "--kind", "video", "--derived-from", str(env["tmp"] / "nope")]) == 2
    assert "not a folder" in capsys.readouterr().err
    assert work.main(["adopt", "--dir", str(new), "--kind", "video", "--derived-from", str(new)]) == 2
    assert "itself" in capsys.readouterr().err
    deck = env["tmp"] / "deck"
    (deck / "src").mkdir(parents=True)
    (deck / "src" / "deck.html").write_text("<html><body><section>a</section></body></html>", encoding="utf-8")
    assert work.main(["adopt", "--dir", str(deck), "--kind", "deck", "--derived-from", str(new)]) == 2
    assert "--kind video" in capsys.readouterr().err
    assert not (new / "work.json").exists() and not (deck / "work.json").exists()
    assert _registry(env) == []
