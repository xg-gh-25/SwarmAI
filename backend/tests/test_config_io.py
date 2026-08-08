"""Tests for jobs/config_io — the single serialization authority for config.yaml.

Both the /api/community/feeds write endpoints AND self_tune write through
mutate_config(), so they serialize on ONE sidecar flock (R27) and never clobber
each other. The write is atomic (tmp + os.replace) and preserves the header.

Methodology: tmp config.yaml (mutate_config takes an explicit path so tests never
touch the live workspace config).
"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

from jobs.config_io import mutate_config, read_config, CONFIG_HEADER


def _seed(path: Path, feeds: list[dict]) -> None:
    path.write_text(
        CONFIG_HEADER + yaml.dump({"feeds": feeds, "defaults": {}}, sort_keys=False)
    )


def test_mutate_config_applies_mutator(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [{"id": "a", "name": "A"}])

    def add_b(config: dict) -> None:
        config["feeds"].append({"id": "b", "name": "B"})

    mutate_config(add_b, config_path=cfg)
    data = yaml.safe_load(cfg.read_text())
    ids = [f["id"] for f in data["feeds"]]
    assert ids == ["a", "b"]


def test_mutate_config_preserves_header(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [{"id": "a"}])
    mutate_config(lambda c: None, config_path=cfg)
    text = cfg.read_text()
    # The header comment block survives the write (not lost, not doubled).
    assert text.startswith("# Swarm Signal Pipeline")
    assert text.count("# Swarm Signal Pipeline") == 1


def test_mutate_config_atomic_no_partial(tmp_path: Path) -> None:
    # A mutator that raises must NOT corrupt/truncate the existing file
    # (atomic write: tmp is discarded, original untouched).
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [{"id": "a", "name": "A"}])
    before = cfg.read_text()

    def boom(config: dict) -> None:
        config["feeds"].append({"id": "b"})
        raise ValueError("mutator failed")

    try:
        mutate_config(boom, config_path=cfg)
    except ValueError:
        pass
    # original intact — no partial write
    assert cfg.read_text() == before


def test_mutate_config_lock_is_sidecar_not_the_file(tmp_path: Path) -> None:
    # The lock must be a SIDECAR (.config.yaml.lock), never config.yaml itself —
    # os.replace swaps the inode and would drop a flock held on the replaced file (GUI22).
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [{"id": "a"}])
    mutate_config(lambda c: None, config_path=cfg)
    assert (tmp_path / ".config.yaml.lock").exists()  # sidecar created


def test_mutate_config_concurrent_writes_both_persist(tmp_path: Path) -> None:
    # THE R27 test: two threads each add a distinct feed; the sidecar lock
    # serializes the read-modify-write so BOTH survive (no last-writer-wins clobber).
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [])

    def adder(fid: str):
        def mut(config: dict) -> None:
            config.setdefault("feeds", []).append({"id": fid})
        # small barrier to encourage interleaving
        mutate_config(mut, config_path=cfg)

    threads = [threading.Thread(target=adder, args=(f"f{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = yaml.safe_load(cfg.read_text())
    ids = sorted(f["id"] for f in data["feeds"])
    assert ids == [f"f{i}" for i in range(8)]  # all 8 present, none clobbered


def test_read_config_missing_returns_empty(tmp_path: Path) -> None:
    assert read_config(config_path=tmp_path / "nope.yaml") == {}


def test_mutate_config_returns_mutator_result(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _seed(cfg, [{"id": "a"}])

    def mut(config: dict) -> str:
        return "sentinel"

    assert mutate_config(mut, config_path=cfg) == "sentinel"
