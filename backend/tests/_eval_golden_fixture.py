"""Test helper: lay out a golden set the way an install sees it.

The product set ships with the backend and is read-only; a user's own cases live
in <workspace>/Eval/golden_set.private.yaml (core.golden_set_store). Tests that
exercise CRUD, merge-preserve or loading put their fixture cases in the PRIVATE
file (the only file anything writes) and point the product path at a fixture
holding the container metadata plus any product cases the test needs.
"""
from pathlib import Path

import yaml


def install_golden_fixture(monkeypatch, workspace: Path, golden_set: dict,
                           *, product_cases=()) -> tuple[Path, Path]:
    import core.golden_set_store as store

    meta = {k: v for k, v in golden_set.items() if k != "cases"}
    meta.setdefault("version", 2)
    product = Path(workspace).parent / f"{Path(workspace).name}-product-golden_set.yaml"
    product.write_text(yaml.safe_dump({**meta, "cases": list(product_cases)}), encoding="utf-8")
    monkeypatch.setattr(store, "product_golden_set_path", lambda: product)

    private = Path(workspace) / "Eval" / "golden_set.private.yaml"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(
        yaml.dump({"version": 2, "cases": list(golden_set.get("cases") or [])},
                  default_flow_style=False),
        encoding="utf-8")
    return product, private
