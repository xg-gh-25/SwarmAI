"""Settings reads only the .env beside its module, and never shows a refused value.

The backend's commands run from any directory, a person's own repositories
included. Reading the working directory's .env made one unknown key there (a
PEXELS_API_KEYS line meant for another program) crash every command, and the
pydantic error printed part of the key's value to the terminal.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

import config_settings
from config import Settings

BACKEND = Path(__file__).resolve().parent.parent
SECRET = "whBCpuiJNMd7-not-a-real-key-ZMXqws7mhNco"


def test_the_env_file_is_the_one_beside_the_module_not_the_working_directorys():
    assert config_settings.ENV_FILE == BACKEND / ".env"
    assert Path(Settings.model_config["env_file"]).is_absolute()


def test_a_foreign_env_in_the_working_directory_neither_crashes_nor_configures(tmp_path):
    (tmp_path / ".env").write_text(f"PEXELS_API_KEYS={SECRET}\nPORT=1\nDEBUG=true\n")
    code = "import config_settings as c; s = c.Settings(); print(s.port)"
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                       env={"PYTHONPATH": str(BACKEND), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
                       timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.split()[-1] != "1"                     # the foreign PORT was not read
    assert SECRET not in r.stdout + r.stderr


def test_a_refused_value_never_appears_in_the_error(tmp_path):
    env = tmp_path / "own.env"
    env.write_text(f"PEXELS_API_KEYS={SECRET}\nPORT=not-a-number-{SECRET}\n")

    class _S(Settings):
        model_config = SettingsConfigDict(env_file=str(env), env_file_encoding="utf-8")

    with pytest.raises(ValidationError) as e:
        _S()
    text = str(e.value)
    assert "pexels_api_keys" in text and "port" in text        # the fields are named
    assert SECRET not in text and "whBC" not in text           # their values are not
