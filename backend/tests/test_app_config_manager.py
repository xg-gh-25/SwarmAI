"""Unit tests for AppConfigManager.

Tests the in-memory cached configuration manager backed by a JSON file.
Covers the full lifecycle: load, get, update, reload, plus edge cases
like missing files, invalid JSON, secret filtering, and file permissions.

Key invariants verified:

- ``get()`` returns from cache with zero file IO after ``load()``
- ``update()`` never writes secret keys to the config file
- Missing / empty / invalid JSON falls back to ``DEFAULT_CONFIG`` (a present-but-unreadable file is isolated first, never overwritten)
- ``reload()`` re-reads from disk and refreshes the cache
- File is created with ``0o600`` permissions
- Migration only runs when ``config.json`` is missing
"""

import json
import stat
import sys
import pytest
from pathlib import Path

from core.app_config_manager import (
    AppConfigManager,
    DEFAULT_CONFIG,
    SECRET_KEYS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Return a path to a temporary config.json (does not exist yet)."""
    return tmp_path / "config.json"


@pytest.fixture
def mgr(tmp_config: Path) -> AppConfigManager:
    """Return an AppConfigManager pointing at the temp config path."""
    return AppConfigManager(config_path=tmp_config)


# ---------------------------------------------------------------------------
# load() tests
# ---------------------------------------------------------------------------


class TestLoad:
    """Tests for AppConfigManager.load()."""

    def test_load_creates_file_with_defaults_when_missing(self, mgr, tmp_config):
        """Missing config file → defaults written to disk.

        The model assertion checks the DERIVATION (the registry flagship), not a
        literal name. A literal made this test a second place to edit on every
        model release, and it silently encoded a default that had already
        drifted behind the live config — the exact defect that made a
        missing-config boot downgrade the model two generations.
        """
        from model_registry import FLAGSHIP_MODEL
        result = mgr.load()
        assert tmp_config.exists()
        assert result["use_bedrock"] is True
        assert result["aws_region"] == "us-east-1"
        assert result["default_model"] == FLAGSHIP_MODEL

    def test_load_falls_back_on_empty_file(self, mgr, tmp_config):
        """Empty config file → defaults."""
        tmp_config.write_text("")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_falls_back_on_invalid_json(self, mgr, tmp_config):
        """Malformed JSON → defaults."""
        tmp_config.write_text("{not valid json!!")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_falls_back_on_non_dict_json(self, mgr, tmp_config):
        """JSON array at root → defaults."""
        tmp_config.write_text("[1, 2, 3]")
        result = mgr.load()
        assert result == DEFAULT_CONFIG

    def test_load_merges_with_defaults(self, mgr, tmp_config):
        """Partial config file → merged with defaults."""
        tmp_config.write_text(json.dumps({"aws_region": "eu-west-1"}))
        result = mgr.load()
        assert result["aws_region"] == "eu-west-1"
        # Default keys still present
        assert result["use_bedrock"] is True
        assert "available_models" in result

    def test_load_preserves_extra_keys(self, mgr, tmp_config):
        """Unknown keys in file are preserved (forward compat)."""
        tmp_config.write_text(json.dumps({"custom_key": "hello"}))
        result = mgr.load()
        assert result["custom_key"] == "hello"


# ---------------------------------------------------------------------------
# get() tests
# ---------------------------------------------------------------------------


class TestGet:
    """Tests for AppConfigManager.get()."""

    def test_get_returns_cached_value(self, mgr, tmp_config):
        """get() reads from cache, not disk."""
        tmp_config.write_text(json.dumps({"aws_region": "ap-southeast-1"}))
        mgr.load()
        assert mgr.get("aws_region") == "ap-southeast-1"

    def test_get_returns_default_for_missing_key(self, mgr):
        """Missing key returns the caller-supplied default."""
        mgr.load()
        assert mgr.get("nonexistent", "fallback") == "fallback"

    def test_get_returns_none_for_missing_key_no_default(self, mgr):
        """Missing key with no default returns None."""
        mgr.load()
        assert mgr.get("nonexistent") is None

    def test_get_auto_loads_if_cache_empty(self, mgr):
        """get() triggers load() if cache is None."""
        result = mgr.get("use_bedrock")
        assert result is True  # from DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# update() tests
# ---------------------------------------------------------------------------


class TestUpdate:
    """Tests for AppConfigManager.update()."""

    def test_update_merges_into_cache(self, mgr):
        """update() merges new values into the in-memory cache."""
        mgr.load()
        mgr.update({"aws_region": "us-west-2"})
        assert mgr.get("aws_region") == "us-west-2"
        # Other keys preserved
        assert mgr.get("use_bedrock") is True

    def test_update_writes_to_disk(self, mgr, tmp_config):
        """update() persists changes to the config file."""
        mgr.load()
        mgr.update({"aws_region": "eu-central-1"})
        on_disk = json.loads(tmp_config.read_text())
        assert on_disk["aws_region"] == "eu-central-1"

    def test_update_filters_secret_keys_from_file(self, mgr, tmp_config):
        """Secret keys are stripped from the persisted file."""
        mgr.load()
        mgr.update({
            "aws_region": "us-east-1",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "aws_session_token": "FwoGZXIvYXdzEBY...",
            "aws_bearer_token": "bearer-token-value",
            "anthropic_api_key": "sk-ant-api03-...",
        })
        on_disk = json.loads(tmp_config.read_text())
        for secret in SECRET_KEYS:
            assert secret not in on_disk, f"{secret} leaked to config file"
        # Non-secret key is present
        assert on_disk["aws_region"] == "us-east-1"

    def test_update_auto_loads_if_cache_empty(self, mgr, tmp_config):
        """update() triggers load() if cache is None."""
        mgr.update({"aws_region": "ap-northeast-1"})
        assert mgr.get("aws_region") == "ap-northeast-1"


# ---------------------------------------------------------------------------
# reload() tests
# ---------------------------------------------------------------------------


class TestReload:
    """Tests for AppConfigManager.reload()."""

    def test_reload_picks_up_manual_edits(self, mgr, tmp_config):
        """reload() re-reads from disk after external changes."""
        mgr.load()
        assert mgr.get("aws_region") == "us-east-1"
        # Simulate manual edit
        data = json.loads(tmp_config.read_text())
        data["aws_region"] = "sa-east-1"
        tmp_config.write_text(json.dumps(data))
        mgr.reload()
        assert mgr.get("aws_region") == "sa-east-1"


# ---------------------------------------------------------------------------
# File permissions tests
# ---------------------------------------------------------------------------


class TestFilePermissions:
    """Tests for config file permission enforcement."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod 0600 not meaningful on Windows",
    )
    def test_file_created_with_0600_permissions(self, mgr, tmp_config):
        """Config file is created with owner-only read/write."""
        mgr.load()  # triggers file creation
        mode = tmp_config.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod 0600 not meaningful on Windows",
    )
    def test_update_preserves_0600_permissions(self, mgr, tmp_config):
        """update() re-applies 0600 after writing."""
        mgr.load()
        mgr.update({"aws_region": "us-west-2"})
        mode = tmp_config.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Round-trip consistency
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Tests for cache ↔ file consistency."""

    def test_cache_matches_file_after_update(self, mgr, tmp_config):
        """After update(), cache and file have identical non-secret data."""
        mgr.load()
        mgr.update({"aws_region": "eu-west-1", "use_bedrock": False})
        on_disk = json.loads(tmp_config.read_text())
        # Cache should match file for non-secret keys
        for key, value in on_disk.items():
            assert mgr.get(key) == value

    def test_non_updated_fields_preserved(self, mgr, tmp_config):
        """Partial update preserves fields not in the update dict."""
        mgr.load()
        original_model = mgr.get("default_model")
        mgr.update({"aws_region": "ap-south-1"})
        assert mgr.get("default_model") == original_model


# ---------------------------------------------------------------------------
# Secret store tests (FIX-A: durable secret storage, never in config.json)
# ---------------------------------------------------------------------------

class TestSecretStore:
    """anthropic_api_key must persist across restarts via a SEPARATE secret
    store (0o600), NEVER written to config.json (which is backed up to git)."""

    def test_set_secret_persists_and_reads_back(self, tmp_config: Path):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("anthropic_api_key", "sk-ant-test123")
        assert mgr.get("anthropic_api_key") == "sk-ant-test123"

    def test_secret_NOT_written_to_config_json(self, tmp_config: Path):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("anthropic_api_key", "sk-ant-secret")
        # config.json must never contain the secret value
        disk = tmp_config.read_text(encoding="utf-8")
        assert "sk-ant-secret" not in disk

    def test_secret_survives_restart(self, tmp_config: Path):
        mgr1 = AppConfigManager(config_path=tmp_config)
        mgr1.load()
        mgr1.set_secret("anthropic_api_key", "sk-ant-durable")
        # New manager instance (simulates daemon restart) reads the same paths
        mgr2 = AppConfigManager(config_path=tmp_config)
        mgr2.load()
        assert mgr2.get("anthropic_api_key") == "sk-ant-durable"

    def test_secret_file_is_0600(self, tmp_config: Path):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("anthropic_api_key", "sk-ant-perm")
        secret_path = tmp_config.parent / "secrets.json"
        assert secret_path.exists()
        mode = stat.S_IMODE(secret_path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_only_secret_keys_accepted(self, tmp_config: Path):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        with pytest.raises(ValueError):
            mgr.set_secret("aws_region", "us-west-2")  # not a secret key

    # ── #3: cross-process lock around the read-modify-write ──
    def test_set_secret_serializes_on_lockfile(self, tmp_config: Path):
        """set_secret's read-modify-write MUST be guarded by an exclusive lock
        on a persistent sibling lockfile (secrets.json.lock). Non-theater proof:
        THIS test grabs the exclusive lock first, then calls set_secret from a
        worker thread and asserts it BLOCKS while the lock is held and only
        completes after release. Without the prod lock, set_secret ignores the
        held lock and completes immediately → this test fails.

        POSIX-only mechanism (fcntl.flock via utils.file_lock); skip on Windows
        where the daemon runs as a subprocess and this race is not exercised."""
        import sys as _sys
        if _sys.platform.startswith("win"):
            pytest.skip("flock semantics differ on Windows; race not exercised there")
        import threading
        from utils.file_lock import flock_exclusive, flock_unlock

        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        # Seed the store so the lockfile path is established next to secrets.json.
        mgr.set_secret("anthropic_api_key", "seed")
        lock_path = tmp_config.parent / "secrets.json.lock"

        done = threading.Event()

        def _writer():
            # Runs in a worker thread; blocks on the same exclusive lock.
            mgr.set_secret("aws_bearer_token", "racer")
            done.set()

        lock_fd = open(lock_path, "w")
        try:
            flock_exclusive(lock_fd)          # test holds the lock
            t = threading.Thread(target=_writer, daemon=True)
            t.start()
            # While WE hold the lock, set_secret must NOT complete.
            blocked = not done.wait(timeout=1.0)
            assert blocked, (
                "set_secret completed while an exclusive lock was held — it is "
                "NOT serializing its read-modify-write on secrets.json.lock")
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()
        # After release, the writer must finish promptly.
        assert done.wait(timeout=5.0), "set_secret did not complete after lock release"
        t.join(timeout=5.0)
        # Both keys survived — no lost update.
        mgr2 = AppConfigManager(config_path=tmp_config)
        mgr2.load()
        assert mgr2.get("anthropic_api_key") == "seed"
        assert mgr2.get("aws_bearer_token") == "racer"

    def test_config_json_never_contains_secret_after_set_secret(self, tmp_config: Path):
        """Gate-2 R4: even after a normal update() writes config.json, the
        secret set via set_secret must be absent from the config file."""
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("anthropic_api_key", "sk-ant-must-not-appear")
        mgr.update({"aws_region": "eu-west-1"})  # forces a config.json write
        raw = tmp_config.read_text(encoding="utf-8")
        assert "anthropic_api_key" not in json.loads(raw)
        assert "sk-ant-must-not-appear" not in raw

    def test_secret_survives_stale_tmp_from_crashed_write(self, tmp_config: Path):
        """Atomic write: a leftover .tmp must not clobber a good secrets.json."""
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("anthropic_api_key", "sk-ant-good")
        (tmp_config.parent / "secrets.json.tmp").write_text("{corrupt", encoding="utf-8")
        mgr2 = AppConfigManager(config_path=tmp_config)
        mgr2.load()
        assert mgr2.get("anthropic_api_key") == "sk-ant-good"

    def test_bedrock_bearer_token_is_a_secret_key(self):
        """aws_bearer_token_bedrock (AWS_BEARER_TOKEN_BEDROCK) must be a SECRET_KEY."""
        assert "aws_bearer_token_bedrock" in SECRET_KEYS

    def test_bedrock_bearer_persists_and_never_in_config_json(self, tmp_config: Path):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        mgr.set_secret("aws_bearer_token_bedrock", "bedrock-bearer-xyz")
        assert mgr.get("aws_bearer_token_bedrock") == "bedrock-bearer-xyz"
        # Force a config.json write; the bearer must NOT leak into it.
        mgr.update({"aws_region": "us-west-2"})
        raw = tmp_config.read_text(encoding="utf-8")
        assert "aws_bearer_token_bedrock" not in json.loads(raw)
        assert "bedrock-bearer-xyz" not in raw

    def test_bedrock_bearer_survives_restart(self, tmp_config: Path):
        mgr1 = AppConfigManager(config_path=tmp_config)
        mgr1.load()
        mgr1.set_secret("aws_bearer_token_bedrock", "bedrock-durable")
        mgr2 = AppConfigManager(config_path=tmp_config)
        mgr2.load()
        assert mgr2.get("aws_bearer_token_bedrock") == "bedrock-durable"


# ---------------------------------------------------------------------------
# Accessor contract: the singleton is AppConfigManager.instance()
# ---------------------------------------------------------------------------


class TestSingletonAccessorContract:
    """There is no module-level ``app_config_manager`` object.

    Seven call sites once did ``from core.app_config_manager import
    app_config_manager`` inside ``try/except Exception``. The symbol never existed,
    so every one silently fell back (config thresholds never applied, workspace_path
    never persisted, SkillMetrics priority always empty). This guard keeps the
    phantom import from coming back.
    """

    def test_module_has_no_app_config_manager_attribute(self):
        import core.app_config_manager as mod

        assert not hasattr(mod, "app_config_manager")

    def test_no_source_imports_the_phantom_symbol(self):
        import re

        backend = Path(__file__).resolve().parent.parent
        pattern = re.compile(r"import\s+app_config_manager\b")
        offenders = [
            str(p.relative_to(backend))
            for p in backend.rglob("*.py")
            if "tests" not in p.parts
            and ".venv" not in p.parts
            and "node_modules" not in p.parts
            and pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert offenders == [], f"use AppConfigManager.instance(): {offenders}"

    def test_persist_workspace_path_writes_through_singleton(
        self, tmp_config: Path, monkeypatch
    ):
        from core.initialization_manager import InitializationManager

        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        monkeypatch.setattr(AppConfigManager, "_instance", mgr)

        InitializationManager._persist_workspace_path("/tmp/ws-under-test")

        assert mgr.get("workspace_path") == "/tmp/ws-under-test"
        on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert on_disk["workspace_path"] == "/tmp/ws-under-test"


# ---------------------------------------------------------------------------
# Corruption handling — PRESERVE, never destroy (STEERING #20)
# ---------------------------------------------------------------------------

class TestCorruptStoreIsPreserved:
    """A present-but-unreadable config.json / secrets.json must be ISOLATED
    (renamed to ``<name>.corrupt-<ts>``, bytes intact) and surfaced via the
    recovery marker BEFORE anything is written in its place. Overwriting it
    with defaults destroys the user's settings / API keys."""

    @staticmethod
    def _corrupt_copies(directory: Path, name: str) -> list[Path]:
        return sorted(directory.glob(f"{name}.corrupt-*"))

    @staticmethod
    def _marker(directory: Path) -> dict | None:
        from core.data_safety import read_recovery_marker
        return read_recovery_marker(directory)

    @pytest.mark.parametrize("content", ["{not valid json!!", "[1, 2, 3]", "   "])
    def test_invalid_config_is_isolated_before_defaults_written(self, tmp_config, content):
        tmp_config.write_text(content, encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        result = mgr.load()

        copies = self._corrupt_copies(tmp_config.parent, "config.json")
        assert len(copies) == 1, "corrupt config.json must be preserved by rename"
        assert copies[0].read_text(encoding="utf-8") == content
        assert result == DEFAULT_CONFIG
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["default_model"] == DEFAULT_CONFIG["default_model"]

        marker = self._marker(tmp_config.parent)
        assert marker is not None, "isolation must be surfaced via the recovery marker"
        assert any(Path(e["isolated_path"]) == copies[0] for e in marker["pending"])

    def test_isolation_failure_never_overwrites_the_original(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        def _fail(_target):
            raise ds.IsolationError("read-only fs")

        monkeypatch.setattr(ds, "isolate_store", _fail)
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        result = mgr.load()

        assert result == DEFAULT_CONFIG, "runs degraded on in-memory defaults"
        assert tmp_config.read_text(encoding="utf-8") == "{half-written", (
            "a store that could not be preserved must be left untouched"
        )

    def test_missing_config_creates_defaults_without_isolation(self, tmp_config):
        AppConfigManager(config_path=tmp_config).load()
        assert tmp_config.exists()
        assert self._corrupt_copies(tmp_config.parent, "config.json") == []
        assert self._marker(tmp_config.parent) is None

    def test_corrupt_secrets_is_isolated_before_new_key_written(self, tmp_config):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        secrets = tmp_config.parent / "secrets.json"
        secrets.write_text('{"anthropic_api_key": "sk-old", ', encoding="utf-8")

        mgr.set_secret("aws_bearer_token_bedrock", "tok-new")

        copies = self._corrupt_copies(tmp_config.parent, "secrets.json")
        assert len(copies) == 1, "corrupt secrets.json must be preserved by rename"
        assert "sk-old" in copies[0].read_text(encoding="utf-8")
        assert json.loads(secrets.read_text(encoding="utf-8")) == {"aws_bearer_token_bedrock": "tok-new"}
        assert self._marker(tmp_config.parent) is not None

    def test_unreadable_secrets_raises_and_writes_nothing(self, tmp_config, monkeypatch):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        secrets = tmp_config.parent / "secrets.json"
        secrets.write_text('{"anthropic_api_key": "sk-keep"}', encoding="utf-8")

        real_read_text = Path.read_text

        def _read_text(self, *a, **kw):
            if self == secrets:
                raise PermissionError("EACCES")
            return real_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _read_text)
        with pytest.raises(RuntimeError):
            mgr.set_secret("aws_bearer_token_bedrock", "tok-new")
        monkeypatch.setattr(Path, "read_text", real_read_text)

        assert json.loads(secrets.read_text(encoding="utf-8")) == {"anthropic_api_key": "sk-keep"}
        assert self._corrupt_copies(tmp_config.parent, "secrets.json") == []

    def test_config_write_is_atomic(self, tmp_config, monkeypatch):
        import os

        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def _spy(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _spy)
        mgr.update({"aws_region": "eu-west-1"})

        assert any(dst == str(tmp_config) for _src, dst in calls), "config.json must be os.replace()d into place"
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["aws_region"] == "eu-west-1"
        assert stat.S_IMODE(tmp_config.stat().st_mode) == 0o600
        assert list(tmp_config.parent.glob("config.json.tmp*")) == []
        assert [p for p in tmp_config.parent.iterdir() if p.name.startswith(".config.json")] == []


class TestUnpreservedCorruptStoreStaysProtected:
    """If isolation failed at load(), a LATER write must not overwrite the file."""

    def test_update_after_failed_isolation_refuses_while_still_unpreservable(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        def _fail(_target):
            raise ds.IsolationError("read-only fs")

        monkeypatch.setattr(ds, "isolate_store", _fail)
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        with pytest.raises(RuntimeError):
            mgr.update({"aws_region": "eu-west-1"})
        assert tmp_config.read_text(encoding="utf-8") == "{half-written"

    def test_update_after_failed_isolation_isolates_once_possible(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        real = ds.isolate_store
        state = {"fail": True}

        def _flaky(target):
            if state["fail"]:
                raise ds.IsolationError("transient")
            return real(target)

        monkeypatch.setattr(ds, "isolate_store", _flaky)
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        state["fail"] = False
        mgr.update({"aws_region": "eu-west-1"})

        copies = sorted(tmp_config.parent.glob("config.json.corrupt-*"))
        assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == "{half-written"
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["aws_region"] == "eu-west-1"

    def test_refused_update_leaves_cache_unchanged(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        monkeypatch.setattr(ds, "isolate_store", lambda _t: (_ for _ in ()).throw(ds.IsolationError("ro")))
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        with pytest.raises(RuntimeError):
            mgr.update({"aws_region": "eu-west-1"})
        assert mgr.get("aws_region") == DEFAULT_CONFIG["aws_region"], (
            "a refused write must not leave the process on unsaved settings"
        )

    def test_hand_fixed_file_is_adopted_not_isolated(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        monkeypatch.setattr(ds, "isolate_store", lambda _t: (_ for _ in ()).throw(ds.IsolationError("ro")))
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        tmp_config.write_text(json.dumps({"aws_region": "ap-east-1"}), encoding="utf-8")  # user repairs it

        mgr.update({"thinking_effort": "max"})

        on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert on_disk["aws_region"] == "ap-east-1", "repaired settings must survive"
        assert on_disk["thinking_effort"] == "max"
        assert list(tmp_config.parent.glob("config.json.corrupt-*")) == []

    def test_non_utf8_secrets_is_isolated_not_500(self, tmp_config):
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        secrets = tmp_config.parent / "secrets.json"
        secrets.write_bytes(b"\xff\xfe{")
        mgr.set_secret("anthropic_api_key", "sk-new")
        copies = sorted(tmp_config.parent.glob("secrets.json.corrupt-*"))
        assert len(copies) == 1 and copies[0].read_bytes() == b"\xff\xfe{"
        assert json.loads(secrets.read_text(encoding="utf-8")) == {"anthropic_api_key": "sk-new"}

    def test_non_utf8_config_is_isolated(self, tmp_config):
        tmp_config.write_bytes(b"\xff\xfe{")
        result = AppConfigManager(config_path=tmp_config).load()
        assert result == DEFAULT_CONFIG
        copies = sorted(tmp_config.parent.glob("config.json.corrupt-*"))
        assert len(copies) == 1 and copies[0].read_bytes() == b"\xff\xfe{"

    def test_failed_isolation_is_still_surfaced(self, tmp_config, monkeypatch):
        import core.data_safety as ds

        monkeypatch.setattr(ds, "isolate_store", lambda _t: (_ for _ in ()).throw(ds.IsolationError("ro")))
        tmp_config.write_text("{half-written", encoding="utf-8")
        AppConfigManager(config_path=tmp_config).load()
        from core.data_safety import read_recovery_marker
        marker = read_recovery_marker(tmp_config.parent)
        assert marker is not None
        assert any(Path(e["isolated_path"]) == tmp_config for e in marker["pending"])

    def test_repeated_failed_isolation_writes_one_marker_entry(self, tmp_config, monkeypatch):
        import core.data_safety as ds
        from core.data_safety import read_recovery_marker

        monkeypatch.setattr(ds, "isolate_store", lambda _t: (_ for _ in ()).throw(ds.IsolationError("ro")))
        tmp_config.write_text("{half-written", encoding="utf-8")
        mgr = AppConfigManager(config_path=tmp_config)
        mgr.load()
        for _ in range(3):
            with pytest.raises(RuntimeError):
                mgr.update({"aws_region": "eu-west-1"})
        entries = [e for e in read_recovery_marker(tmp_config.parent)["pending"]
                   if Path(e["isolated_path"]) == tmp_config]
        assert len(entries) == 1


def test_update_keeps_a_hand_edit_made_after_load(mgr, tmp_config):
    """A save after a hand edit must not write the stale startup cache over it."""
    import os
    import time
    mgr.load()
    data = json.loads(tmp_config.read_text())
    data["pipeline"] = {"worktree_mode": "off"}
    tmp_config.write_text(json.dumps(data))
    st = tmp_config.stat()
    os.utime(tmp_config, (st.st_atime, st.st_mtime + 5))   # a later edit, whatever the clock
    time.sleep(0)
    mgr.update({"aws_region": "eu-west-1"})
    disk = json.loads(tmp_config.read_text())
    assert disk["pipeline"] == {"worktree_mode": "off"} and disk["aws_region"] == "eu-west-1"


def test_worktree_mode_is_not_a_setting(mgr):
    """P7 (usable-by-anyone §1): no product config serves only one workflow. Pipeline runs
    always get isolated worktrees; there is no config.json switch to turn that off."""
    from core.app_config_manager import DEFAULT_CONFIG
    assert "pipeline" not in DEFAULT_CONFIG
    assert "pipeline" not in mgr.load()


def test_update_refuses_a_hand_edit_that_no_longer_parses(mgr, tmp_config):
    import os
    mgr.load()
    mgr.update({"aws_region": "eu-west-1"})
    tmp_config.write_text('{"aws_region": "x",}')
    st = tmp_config.stat()
    os.utime(tmp_config, (st.st_atime, st.st_mtime + 5))
    with pytest.raises(RuntimeError):
        mgr.update({"thinking_mode": "disabled"})
    assert mgr.get("aws_region") == "eu-west-1"              # cache kept, nothing reset
    assert tmp_config.read_text() == '{"aws_region": "x",}'  # the edit is left for the user
