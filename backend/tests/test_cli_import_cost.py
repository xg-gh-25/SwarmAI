"""Guards the artifact_cli cold-start budget: what one CLI process imports.

Every ``scripts/artifact_cli.py`` call is a fresh process — the pipeline makes hundreds
per run and the CLI test suites spawn thousands per test run. Importing any ``core.*``
submodule used to run ``core/__init__.py``'s eager imports (session_manager → database
→ config → pydantic_settings, ~150 ms), and ``config``'s path helpers dragged in
pydantic_settings too. These tests pin the lazy package inits:

- importing artifact_cli (and resolving the workspace) loads neither pydantic_settings
  nor core.session_manager / database;
- ``core.<Name>`` and ``config.settings`` still resolve, to the same objects as their
  defining modules, and unknown names still raise AttributeError (so
  ``from core import <submodule>`` keeps importing the submodule).

Methodology: a clean subprocess per check (sys.modules of THIS process is polluted by
other tests).
"""
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _modules_after(code: str) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-c", code + "\nimport json, sys\nprint(json.dumps(sorted(sys.modules)))"],
        cwd=str(BACKEND), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{BACKEND}:{BACKEND / 'scripts'}",
             "SWARM_WORKSPACE": str(BACKEND / ".no-such-workspace")},
    )
    assert out.returncode == 0, out.stderr
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


HEAVY = ("pydantic_settings", "core.session_manager", "database", "core.session_unit")


def test_artifact_cli_import_skips_the_app_stack():
    mods = _modules_after("import artifact_cli\nartifact_cli._get_workspace()")
    assert not [m for m in HEAVY if m in mods], [m for m in HEAVY if m in mods]


def test_config_path_helpers_skip_pydantic_settings():
    mods = _modules_after("from config import get_app_data_dir, get_log_file_path\n"
                          "get_app_data_dir(); get_log_file_path()")
    assert "pydantic_settings" not in mods


def test_lazy_names_resolve_to_the_defining_objects():
    import config
    import config_settings
    import core
    from core.session_router import SessionRouter
    from core.session_unit import SessionUnit
    assert core.SessionUnit is SessionUnit and core.SessionRouter is SessionRouter
    from core import LifecycleManager, PromptBuilder  # noqa: F401 — from-import path
    assert config.settings is config_settings.settings
    assert config.get_settings() is config.settings
    assert config.Settings is config_settings.Settings


def test_unknown_names_still_raise_and_submodules_still_import():
    import config
    import core
    for mod, name in ((core, "no_such_name"), (config, "no_such_name")):
        try:
            getattr(mod, name)
        except AttributeError:
            pass
        else:
            raise AssertionError(f"{mod.__name__}.{name} should raise AttributeError")
    from core import executors  # a submodule, not a lazy name
    assert executors.__name__ == "core.executors"
