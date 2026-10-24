"""repo_pipeline_config — a repository's own ``.swarmai/pipeline.toml``.

The one place SwarmAI reads per-repo pipeline settings (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.3.6). Every field is optional;
a missing file means the defaults. The file is read from a git commit (``<rev>:path``),
never from the working tree, so a landing or a worktree reads the same bytes on
every retry.

```toml
[landing]
author = "repo"      # repo (default): the repo's own git user | swarm: Swarm <swarm@swarmai.dev>
trailer = ""         # one line added to every landing commit message, e.g. a Co-Authored-By

[worktree]
link = []            # repo-relative dirs of the main checkout symlinked into a run worktree
```

An invalid file is ignored as a whole (defaults apply) and logged: a typo must never
change who a commit is attributed to in a way nobody asked for.

Public: ``RepoPipelineConfig``, ``parse``, ``load_at``, ``SWARM_IDENTITY``.
"""
from __future__ import annotations

import logging
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

CONFIG_PATH = ".swarmai/pipeline.toml"
SWARM_IDENTITY = ("Swarm", "swarm@swarmai.dev")
_MAX_TRAILER = 200


@dataclass(frozen=True)
class RepoPipelineConfig:
    author: str = "repo"                 # "repo" | "swarm"
    trailer: str = ""
    links: tuple[str, ...] = field(default_factory=tuple)


DEFAULT = RepoPipelineConfig()


def _valid_link(rel: object) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\\" in rel:
        return False
    parts = PurePosixPath(rel).parts
    return bool(parts) and all(p not in ("..", ".", "") for p in parts) and parts[0] != ".git"


def parse(text: str, *, where: str = CONFIG_PATH) -> RepoPipelineConfig:
    """The settings in *text*; ``DEFAULT`` when it is not valid TOML or a field is wrong."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("%s ignored: not valid TOML (%s)", where, exc)
        return DEFAULT
    landing = data.get("landing", {}) or {}
    worktree = data.get("worktree", {}) or {}
    if not isinstance(landing, dict) or not isinstance(worktree, dict):
        logger.warning("%s ignored: [landing] / [worktree] must be tables", where)
        return DEFAULT
    author = landing.get("author", "repo")
    trailer = landing.get("trailer", "")
    links = worktree.get("link", [])
    if author not in ("repo", "swarm"):
        logger.warning("%s ignored: landing.author must be \"repo\" or \"swarm\"", where)
        return DEFAULT
    if not isinstance(trailer, str) or "\n" in trailer or len(trailer) > _MAX_TRAILER:
        logger.warning("%s ignored: landing.trailer must be one line", where)
        return DEFAULT
    if not isinstance(links, list) or not all(_valid_link(x) for x in links):
        logger.warning("%s ignored: worktree.link must be repo-relative paths", where)
        return DEFAULT
    return RepoPipelineConfig(author=author, trailer=trailer.strip(),
                              links=tuple(dict.fromkeys(links)))


def load_at(root: str, rev: str = "HEAD") -> RepoPipelineConfig:
    """The settings of the repo at *root* as committed in *rev*; ``DEFAULT`` when the
    file is not there or git cannot read it."""
    try:
        p = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{CONFIG_PATH}"],
                           capture_output=True, timeout=10,
                           env={**_base_env(), "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("%s in %s unreadable: %s", CONFIG_PATH, root, exc)
        return DEFAULT
    if p.returncode != 0:
        return DEFAULT       # not committed there: the defaults
    return parse(p.stdout.decode("utf-8", "replace"), where=f"{root}:{rev}:{CONFIG_PATH}")


def _base_env() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
