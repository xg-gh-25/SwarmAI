"""golden_set_store — where the golden set lives and how it is read (one reader).

Two files make up the golden set a user's eval runs:

- the **product set**, ``templates/eval/golden_set.yaml``, shipped inside the
  backend and upgraded with it. It is read-only at runtime: every user gets the
  same cases, and nothing on the user's machine writes to it.
- the user's **private set**, ``<workspace>/Eval/golden_set.private.yaml``
  (optional): cases a user adds about their own projects and data.

``load_merged`` is the only function that reads them. Every reader (the eval
runner and its CLI, the dashboard service, the daily canary, the scheduled
eval, the case validator) goes through it, so they all see the same cases.

Each returned case carries an in-memory ``_origin`` tag, ``"product"`` or
``"private"``; writers use it to keep product cases read-only and it is never
written to disk (``_origin`` is outside the case stamp, see
``golden_case_validator._STAMP_EXCLUDED_FIELDS``).

A private case whose id equals a product case's id is skipped and its id is
reported in ``shadowed_private_ids``: a product upgrade that adds an id a user
already used must not stop that user's eval from loading.
"""
from __future__ import annotations

import contextlib

from pathlib import Path

PRODUCT_ORIGIN = "product"
PRIVATE_ORIGIN = "private"

# 宁缺毋滥 (design 2026-10-26-eval-as-product §3.2): the product set stays at or
# below PRODUCT_CAP; product + a user's private cases stay at or below TOTAL_CAP
# (add_case refuses past it — retire a case first).
PRODUCT_CAP = 50
TOTAL_CAP = 80


def product_golden_set_path() -> Path:
    """The product set shipped with this backend (``_MEIPASS`` when packaged,
    ``backend/`` in a checkout)."""
    from utils.backend_bin import bundled_path

    return bundled_path("templates", "eval", "golden_set.yaml")


def private_golden_set_path(workspace: Path) -> Path:
    return Path(workspace) / "Eval" / "golden_set.private.yaml"


def _read_yaml(path: Path, *, required: bool) -> dict:
    import yaml

    if not path.exists():
        if required:
            raise FileNotFoundError(f"golden set not found: {path}")
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"golden set {path} is not a mapping")
    return data


def load_merged(workspace: Path, *, product_path: Path | None = None) -> dict:
    """Read the product set (required) and the workspace's private set (optional).

    Returns the product file's container metadata (version, categories, ...) with
    ``cases`` = product cases followed by private cases, each tagged with
    ``_origin``, plus ``shadowed_private_ids``. Raises FileNotFoundError when the
    product set is missing (a broken install, never a normal state) and
    ValueError on a parse error or an id repeated inside one file.
    """
    product_path = product_path or product_golden_set_path()
    product = _read_yaml(product_path, required=True)
    private = _read_yaml(private_golden_set_path(workspace), required=False)

    merged: list[dict] = []
    product_ids: set[str] = set()
    for case in product.get("cases") or []:
        cid = case.get("id")
        if cid in product_ids:
            raise ValueError(f"golden set: id '{cid}' repeated in the product set")
        product_ids.add(cid)
        merged.append({**case, "_origin": PRODUCT_ORIGIN})

    shadowed: list[str] = []
    private_ids: set[str] = set()
    for case in private.get("cases") or []:
        cid = case.get("id")
        if cid in private_ids:
            raise ValueError(f"golden set: id '{cid}' repeated in the private set")
        private_ids.add(cid)
        if cid in product_ids:
            shadowed.append(cid)
            continue
        merged.append({**case, "_origin": PRIVATE_ORIGIN})

    meta = {k: v for k, v in product.items() if k != "cases"}
    return {**meta, "cases": merged, "shadowed_private_ids": shadowed}


def is_product_case(case: dict) -> bool:
    return case.get("_origin") == PRODUCT_ORIGIN


@contextlib.contextmanager
def fixture_workspace():
    """A fresh copy of the workspace the product's offline cases run in
    (``templates/eval/fixtures/workspace``), in a temporary directory that is removed
    afterwards. The shipped ``context/`` folder becomes ``.context/`` (a dot folder is
    not guaranteed to survive packaging). Yields the workspace path."""
    import shutil
    import tempfile
    from utils.backend_bin import bundled_path

    src = bundled_path("templates", "eval", "fixtures", "workspace")
    with tempfile.TemporaryDirectory(prefix="swarm-eval-ws-") as tmp:
        ws = Path(tmp) / "workspace"
        shutil.copytree(src, ws)
        (ws / "context").rename(ws / ".context")
        yield ws
