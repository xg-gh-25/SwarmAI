"""uv.lock records pypi.org as every package's source, whatever index the machine that
locked it uses.

A user-level uv config (a regional mirror) once ended up in the committed uv.lock, so
every install, CI run and Hive fetched from that mirror (403s and stalls from outside
its region). pyproject.toml declares pypi.org as the project's default index, which
takes precedence over a user config; these checks keep both in place. Repo files only.
"""
import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PYPI = "https://pypi.org/simple"


def test_the_project_declares_pypi_as_its_default_index():
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    indexes = cfg.get("tool", {}).get("uv", {}).get("index", [])
    assert any(i.get("url") == PYPI and i.get("default") is True for i in indexes)


def test_every_locked_source_is_pypi():
    lock = (BACKEND / "uv.lock").read_text()
    registries = set(re.findall(r'source = \{ registry = "([^"]+)" \}', lock))
    assert registries == {PYPI}, registries
    hosts = set(re.findall(r'url = "https://([^/"]+)/', lock))
    assert hosts <= {"files.pythonhosted.org", "pypi.org"}, hosts
