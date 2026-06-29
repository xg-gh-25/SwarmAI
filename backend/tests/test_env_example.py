"""Tests that the committed .env.example template stays in sync with the Settings schema.

Verifies that:
- backend/.env.example loads through the REAL ``config.Settings`` class without
  a pydantic ``ValidationError`` — i.e. copying it to ``backend/.env`` does NOT
  crash backend startup.
- Every ``KEY=`` line in the template maps to a declared ``Settings`` field.

Why this exists (regression guard): ``pydantic_settings.BaseSettings`` inherits
``extra="forbid"``. A template that lists a field NOT on the ``Settings`` class
(e.g. a setting that was moved to ``config.json``/``AppConfigManager``) makes
``Settings()`` raise ``ValidationError: Extra inputs are not permitted`` the moment
a user copies the template to ``backend/.env`` and launches. This is exactly the
landmine that ``desktop/backend.env.example`` carried (8 dead fields). This test
is the teeth: it goes RED if any dead field is reintroduced into the canonical
template.

The test drives the REAL Settings class (no mock of pydantic), so it verifies
actual startup behavior, not an imagined contract.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from config import Settings

# backend/tests/test_env_example.py -> backend/.env.example
ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

# Matches uncommented `KEY=value` lines; ignores blank lines and `# comment` lines.
_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def _template_keys(path: Path) -> list[str]:
    """Return the uncommented env keys declared in an .env template."""
    keys: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _KEY_LINE.match(line)
        if m:
            keys.append(m.group(1))
    return keys


def _settings_pointed_at(path: Path) -> type[Settings]:
    """A Settings subclass that loads from the given env file (real schema, real validation)."""

    class _S(Settings):
        model_config = SettingsConfigDict(
            env_file=str(path), env_file_encoding="utf-8"
        )

    return _S


def test_env_example_exists():
    """The canonical backend template exists at backend/.env.example."""
    assert ENV_EXAMPLE.is_file(), f"missing canonical env template: {ENV_EXAMPLE}"


def test_env_example_loads_through_real_settings():
    """Copying backend/.env.example to backend/.env does NOT crash startup.

    Load-bearing assertion: the template, fed through the REAL Settings class
    (which inherits extra='forbid'), instantiates without ValidationError.
    """
    settings_cls = _settings_pointed_at(ENV_EXAMPLE)
    try:
        settings_cls()
    except ValidationError as exc:
        offending = sorted({e["loc"][0] for e in exc.errors() if e.get("loc")})
        pytest.fail(
            "backend/.env.example crashes Settings() with extra=forbid; "
            f"offending fields not on the Settings schema: {offending}. "
            "Remove them from the template (they likely moved to config.json)."
        )


def test_env_example_keys_are_all_settings_fields():
    """Every key in the template maps to a declared Settings field (advisory teeth).

    Stricter than the load test: catches a key that pydantic would reject before
    a user ever copies the file. Skips commented lines, so documentation hints
    (e.g. a commented `# SQLITE_DB_PATH=`) are allowed.
    """
    field_names = set(Settings.model_fields.keys())
    template_keys = _template_keys(ENV_EXAMPLE)
    assert template_keys, "no KEY= lines parsed from template — parser or file is wrong"
    unknown = [k for k in template_keys if k.lower() not in field_names]
    assert not unknown, (
        f"backend/.env.example declares keys absent from the Settings class: {unknown}. "
        "Either add the field to Settings or remove it from the template."
    )
