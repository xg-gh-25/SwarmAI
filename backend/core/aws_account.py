"""aws_account — the one SwarmAI AWS account: its record, resolution and pin.

Design: ``docs/design/2026-10-31-swarmai-aws-account.md`` (§5 mechanism, §9 step 1).
SwarmAI uses ONE AWS account for Bedrock, Hive, cloud storage and voice. This module
holds the record of that account and is the place that turns it into credentials.

What it provides (steps 1-2 of design §9; Hive is step 3):
- the record (``config.json`` key ``aws_account``) and the chat path (key ``chat``);
  neither is writable through the generic ``PUT /api/settings`` (``PROTECTED_CONFIG_KEYS``);
- ``derive_from_legacy``: the record an existing install starts with (design §5.6),
  from ``auth_method`` and the credentials the daemon already uses;
- ``resolve``: short-term credentials for the record's source (``ada``, ``profile``,
  ``instance_role``, and the sign-ins SwarmAI runs itself: ``sso``, ``login``, ``keys``,
  in ``core/aws_signin``),
  **pinned**: every new access key is checked with STS against the record's account,
  and a different account raises ``AccountMismatch`` instead of being used;
- ``cli_settings``: the CLI's ``awsCredentialExport`` = ``python-backend aws-credentials``,
  so the CLI's own Bedrock calls use the pinned account while the agent's shell keeps the
  user's own AWS configuration (design §5.3);
- ``boto_session`` / ``auth_check`` / ``region`` / ``credential_resolver``: what the daemon's
  consumers (jobs, voice, the thinking gateway, settings, the spawn pre-flight, the
  readiness sampler) use instead of the default chain. While no record can be resolved
  (no AWS identity found, a source this version cannot resolve, a Hive) ``active()`` is
  False and each falls back to what it did before: the old path stays until the new one
  covers the case (design §9);
- ``write_profile_file``: ``<data dir>/aws/config``, one profile ``swarmai`` whose
  ``credential_process`` is the same subcommand, for a tool that wants the SwarmAI account
  explicitly (``AWS_CONFIG_FILE=… AWS_PROFILE=swarmai``); nothing sets it by default;
- ``write_daemon_endpoint`` / ``token_ok``: how that subcommand reaches this daemon
  (``<data dir>/aws/daemon.json``, port + a per-boot token, 0600);
- ``run_capability_checks``: Bedrock, Hive, cloud storage, voice (design §7.2). Hive
  and storage are "can it create" checks (``core.aws_permissions``: DryRun and policy
  simulation, nothing created);
- ``connect``: make a candidate the account only after STS confirms it (the wizard and
  the account page; a failure keeps the current record).

Never here: writing the user's ``~/.aws`` files, a cost cap. A ``profile`` source is
resolved by botocore exactly as the daemon's default chain did before this module: that
runs the profile's own ``credential_process`` and may refresh its SSO cache (accepted,
design §8). The capability checks call Bedrock once (one output token). Callers: ``routers/aws_account.py``, ``main.py`` (startup),
``desktop_main.py`` (the ``aws-credentials`` subcommand reads ``daemon.json`` only).
"""
from __future__ import annotations

import configparser
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

RECORD_KEY = "aws_account"
CHAT_KEY = "chat"
HIVE_FLEET_KEY = "hive_fleet"
#: config.json keys only this module and its routes write (never ``PUT /api/settings``)
PROTECTED_CONFIG_KEYS = frozenset({RECORD_KEY, CHAT_KEY, HIVE_FLEET_KEY})

SOURCES = ("ada", "sso", "login", "profile", "keys", "instance_role")
#: sources this version can resolve
RESOLVABLE_SOURCES = SOURCES
ADA_PROVIDERS = ("isengard", "conduit")

UNCONNECTED, CONNECTED, NEEDS_LOGIN, MISMATCH, UNVERIFIED = (
    "unconnected", "connected", "needs_login", "account_mismatch", "unverified")

PROFILE_NAME = "swarmai"
_REFRESH_MARGIN = timedelta(minutes=5)
#: hang guards (a command that never returns), not business timeouts
_ADA_TIMEOUT_S = 60
_STS_TIMEOUT_S = 20


class AccountError(Exception):
    """A resolution failure with a stable ``kind`` the API and UI can act on."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class NeedsLogin(AccountError):
    def __init__(self, message: str):
        super().__init__("needs_login", message)


class AccountMismatch(AccountError):
    def __init__(self, expected: str, actual: str):
        super().__init__(
            "account_mismatch",
            f"the credentials belong to account {actual}, not the SwarmAI account {expected}")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class Creds:
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str]
    expiration: Optional[datetime]

    def fresh(self, now: datetime) -> bool:
        return self.expiration is None or self.expiration - now > _REFRESH_MARGIN

    def process_json(self) -> dict:
        """The AWS ``credential_process`` output format (Version 1)."""
        out: dict[str, Any] = {"Version": 1, "AccessKeyId": self.access_key_id,
                               "SecretAccessKey": self.secret_access_key}
        if self.session_token:
            out["SessionToken"] = self.session_token
        if self.expiration is not None:
            out["Expiration"] = self.expiration.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        return out


# ── paths ──────────────────────────────────────────────────────────────────────

def _data_dir() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir())


def aws_dir() -> Path:
    return _data_dir() / "aws"


def profile_file() -> Path:
    return aws_dir() / "config"


def daemon_endpoint_file() -> Path:
    return aws_dir() / "daemon.json"


def _write_private(path: Path, text: str) -> None:
    """Write *text* to *path* atomically, the directory 0700 and the file 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── record ─────────────────────────────────────────────────────────────────────

def _cfg():
    from core.app_config_manager import AppConfigManager
    return AppConfigManager.instance()


def get_record() -> Optional[dict]:
    rec = _cfg().get(RECORD_KEY)
    return dict(rec) if isinstance(rec, dict) else None


_record_lock = threading.RLock()  # every read-modify-write of the record


def save_record(rec: Optional[dict]) -> None:
    """Replace the record. Pins are keyed by the record's identity (``_identity_key``),
    so a key verified for the old record is checked again for the new one."""
    with _record_lock:
        _cfg().update({RECORD_KEY: rec})


def _update_record(**fields: Any) -> None:
    """Change status fields of the record. A failed write is logged, never raised: it
    must not decide whether valid, pinned credentials are served."""
    try:
        with _record_lock:
            rec = get_record()
            if rec is None:
                return
            changed = {k: v for k, v in fields.items() if rec.get(k) != v}
            if changed:
                rec.update(changed)
                _cfg().update({RECORD_KEY: rec})
    except Exception:  # noqa: BLE001 — config.json unreadable: keep serving, say so in the log
        logger.warning("aws_account: could not save the record's status", exc_info=True)


def get_chat_via(config: Any = None) -> str:
    """Which path chat takes: ``aws`` (Bedrock on this account, or the machine's AWS
    credentials without one) or ``bedrock_key`` (a Bedrock API key). Chat always runs
    on Bedrock: Anthropic-direct keys are no longer supported (design
    2026-11-01-settings-account-merge §3; ``retire_anthropic_direct`` moves an old
    install over). Read here, the one reader, so no consumer can disagree with another."""
    config = config if config is not None else _cfg()
    if config.get("auth_method") == "bedrock_api_key":
        return "bedrock_key"
    return "aws"


def region(config: Any = None) -> str:
    """The main region (design §5.1): Bedrock, cloud storage, voice, eval judges, the
    default for new Hives. Written as ``aws_region`` (a connect writes it from the record,
    the Swarm AWS Account page changes it), with the record's ``region`` only as the fall-back when it
    is unset; every consumer reads it here, never either value itself, so no two can
    disagree."""
    config = config if config is not None else _cfg()
    rec = get_record() if config is _cfg() else config.get(RECORD_KEY)
    value = config.get("aws_region") or (rec or {}).get("region") or "us-east-1"
    return value if re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", value) else "us-east-1"


_main_region = region   # for functions whose ``region`` parameter shadows it


def is_hive_mode() -> bool:
    return os.environ.get("SWARMAI_MODE", "daemon") == "hive"


# ── ADA ────────────────────────────────────────────────────────────────────────

def find_ada() -> Optional[str]:
    """The ada executable: on PATH, else the Builder Toolbox location. None when absent."""
    exe = shutil.which("ada")
    if exe:
        return str(Path(exe))
    for cand in (Path.home() / ".toolbox" / "bin" / "ada",
                 Path.home() / ".toolbox" / "bin" / "ada.exe"):
        if cand.is_file():
            return str(cand)
    return None


def _ada_profile_print(ada_bin: str) -> dict:
    """``ada profile print`` (ADA's own local config, no network) → account/role/provider."""
    try:
        res = subprocess.run([ada_bin, "profile", "print"], capture_output=True, text=True,
                             timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if res.returncode != 0:
        return {}
    out: dict = {}
    for line in res.stdout.splitlines():
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key in ("account", "role", "provider") and val:
            out[key] = val
    return out


_ADA_LOGIN_RE = re.compile(r"mwinit|midway", re.I)
_ACCOUNT_RE = re.compile(r"\d{12}")
_ADA_WORD_RE = re.compile(r"[A-Za-z0-9+=,.@_][A-Za-z0-9+=,.@_/-]{0,127}")
_SECRETISH_RE = re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{12,}|[A-Za-z0-9+/=]{40,}")


def _redact(text: str) -> str:
    """Text safe to keep in the record and show: no key ids, no long base64 runs."""
    return _SECRETISH_RE.sub("[redacted]", text or "")[:300]


def _ada_bin_ok(path: str) -> bool:
    return Path(path).name.lower() in ("ada", "ada.exe") and Path(path).is_file()


def _ada_creds(rec: dict) -> Creds:
    ada = rec.get("ada") or {}
    ada_bin = ada.get("bin")
    if not ada_bin or not _ada_bin_ok(str(ada_bin)):
        raise AccountError("ada_not_found", "ada was not found where it was at setup")
    account, role = str(rec.get("account_id") or ""), str(ada.get("role") or "")
    provider = str(ada.get("provider") or "isengard")
    if not _ACCOUNT_RE.fullmatch(account) or not _ADA_WORD_RE.fullmatch(role) \
            or not _ADA_WORD_RE.fullmatch(provider):
        raise AccountError("bad_record", "the record's ADA account, role or provider is not valid")
    # --profile names ada's own cache slot. Without it ada uses its default slot and can
    # print another account's credentials (measured: --account=210347900436 printed the
    # default slot's 533267412361). `print` writes no AWS file; the slot is SwarmAI's own.
    slot = f"swarmai-{account}-{role}-{provider}"
    cmd = [ada_bin, "credentials", "print", f"--account={account}", f"--role={role}",
           f"--provider={provider}", f"--profile={slot}", "--format=json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=_ADA_TIMEOUT_S,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise AccountError("ada_timeout", f"ada did not answer within {_ADA_TIMEOUT_S}s")
    except OSError as exc:
        raise AccountError("ada_not_found", f"ada could not run: {exc.__class__.__name__}")
    if res.returncode != 0:
        msg = (res.stderr or "").strip().splitlines()  # never stdout: it may hold credentials
        last = msg[-1] if msg else f"exit {res.returncode}"
        if _ADA_LOGIN_RE.search(res.stderr or ""):
            raise NeedsLogin("Midway has expired: run `mwinit -f`")
        raise AccountError("ada_failed", _redact(last))
    try:
        data = json.loads(res.stdout)
        creds = Creds(data["AccessKeyId"], data["SecretAccessKey"], data.get("SessionToken"),
                      _parse_time(data.get("Expiration")))
    except (ValueError, KeyError, TypeError):
        raise AccountError("ada_failed", "ada printed something that is not credentials")
    if creds.expiration is None:  # ada's are always short-term; never cache them forever
        raise AccountError("ada_failed", "ada printed credentials without an expiry")
    return creds


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── profile / instance role ────────────────────────────────────────────────────

def _frozen(creds: Any) -> Creds:
    if creds is None:
        raise AccountError("no_credentials", "no credentials could be resolved")
    try:
        frozen = creds.get_frozen_credentials()
    except Exception as exc:  # noqa: BLE001 — classify below
        name = type(exc).__name__
        if "SSO" in name or "Token" in name or "Login" in name:
            raise NeedsLogin(f"the profile's sign-in has expired ({name})")
        raise AccountError("resolve_failed", f"{name}: {_redact(str(exc))}")
    expiry = _parse_time(getattr(creds, "_expiry_time", None))
    return Creds(frozen.access_key, frozen.secret_key, frozen.token, expiry)


def _profile_creds(rec: dict) -> Creds:
    import boto3
    import botocore.exceptions as bce
    name = rec.get("profile")
    if not name:
        raise AccountError("bad_record", "the record has no profile name")
    if name == PROFILE_NAME:  # SwarmAI's own profile would ask this daemon for itself
        raise AccountError("bad_record", f"profile {PROFILE_NAME!r} is SwarmAI's own")
    try:
        session = boto3.Session(profile_name=name)
        return _frozen(session.get_credentials())
    except bce.ProfileNotFound:
        raise AccountError("profile_not_found", f"profile {name!r} is no longer in ~/.aws")
    except (bce.SSOTokenLoadError, bce.UnauthorizedSSOTokenError, bce.TokenRetrievalError):
        raise NeedsLogin(f"profile {name!r}: the sign-in has expired")


def _instance_role_creds(_rec: dict) -> Creds:
    import boto3
    return _frozen(boto3.Session().get_credentials())


def _sso_creds(rec: dict) -> Creds:
    from core import aws_signin
    return aws_signin.sso_creds(rec)


def _login_creds(rec: dict) -> Creds:
    from core import aws_signin
    return aws_signin.login_creds(rec)


def _keys_creds(rec: dict) -> Creds:
    from core import aws_signin
    return aws_signin.keys_creds(rec)


_RESOLVERS = {"ada": _ada_creds, "profile": _profile_creds, "instance_role": _instance_role_creds,
              "sso": _sso_creds, "login": _login_creds, "keys": _keys_creds}


# ── resolve + pin ──────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: Optional[Creds] = None
#: (record identity, access key id) → caller ARN: keys STS confirmed for THAT record
_verified: dict[tuple, str] = {}


def _identity_key(rec: Optional[dict]) -> Optional[tuple]:
    """Who the record says the account is; a pin and a cache belong to one of these."""
    if not rec:
        return None
    ada = rec.get("ada") or {}
    sso = rec.get("sso") or {}
    return (rec.get("source"), rec.get("account_id"), rec.get("profile"),
            ada.get("role"), ada.get("provider"), ada.get("bin"),
            sso.get("start_url"), sso.get("role"), rec.get("keys_id"))


def _reset_cache() -> None:
    global _cache
    _cache = None
    _verified.clear()


def _sts_identity(creds: Creds, region: str) -> dict:
    import boto3
    from botocore.config import Config
    sts = boto3.client(
        "sts", region_name=region, aws_access_key_id=creds.access_key_id,
        aws_secret_access_key=creds.secret_access_key, aws_session_token=creds.session_token,
        # the identity check talks to AWS's own STS endpoint: an ``endpoint_url`` in the
        # person's ~/.aws/config (or AWS_ENDPOINT_URL_STS) could answer any account
        config=Config(connect_timeout=_STS_TIMEOUT_S, read_timeout=_STS_TIMEOUT_S,
                      retries={"max_attempts": 2}, ignore_configured_endpoint_urls=True))
    return sts.get_caller_identity()


def resolve(force: bool = False) -> Creds:
    """Short-term credentials for the record, pinned to its account.

    Raises ``AccountError`` (and its ``NeedsLogin`` / ``AccountMismatch``); the record's
    ``state`` follows every outcome. A record with no ``account_id`` yet (an upgrade
    whose account could not be read from the config, design §5.6) is pinned to the
    first identity STS confirms."""
    global _cache
    with _lock:
        rec = get_record()
        if rec is None:
            raise AccountError(UNCONNECTED, "no SwarmAI AWS account is connected")
        key = _identity_key(rec)  # a pin belongs to one record: a changed record re-checks
        now = datetime.now(timezone.utc)
        if not force and _cache is not None and _cache.fresh(now) and \
                (key, _cache.access_key_id) in _verified:
            return _cache
        source = rec.get("source")
        resolver = _RESOLVERS.get(source)
        if resolver is None:
            raise AccountError("unsupported", f"source {source!r} is not supported in this version")
        try:
            creds = resolver(rec)
            if force or (key, creds.access_key_id) not in _verified:
                ident = _sts_identity(creds, rec.get("region") or "us-east-1")
                actual = str(ident.get("Account"))
                expected = rec.get("account_id")
                if expected and actual != str(expected):
                    raise AccountMismatch(str(expected), actual)
                if not expected:  # an upgrade record: pinned to the first confirmed identity
                    _update_record(account_id=actual)
                    key = _identity_key(get_record())
                _verified[(key, creds.access_key_id)] = str(ident.get("Arn"))
                _update_record(caller_arn=str(ident.get("Arn")),
                               verified_at=now.isoformat(timespec="seconds"))
        except AccountError as exc:
            _cache = None
            state = {"needs_login": NEEDS_LOGIN, "account_mismatch": MISMATCH}.get(
                exc.kind, UNVERIFIED)
            _update_record(state=state, state_detail=exc.message, error_kind=exc.kind)
            raise
        except Exception as exc:  # noqa: BLE001 — STS / network: a key that cannot be pinned is not served
            _cache = None
            _update_record(state=UNVERIFIED,
                           state_detail=f"{type(exc).__name__}: {_redact(str(exc))}")
            raise AccountError("verify_failed", f"could not verify: {type(exc).__name__}")
        # every success says so, also when the key was verified before a failed attempt
        _update_record(state=CONNECTED, state_detail=None, error_kind=None)
        _cache = creds
        return creds


def _check_candidate(cand: dict) -> dict:
    """The record a connect request asks for, with only the fields its source uses. Every
    value that reaches a command or an API is checked here; the ada binary is the one
    found on this machine, never a path from the request."""
    source = cand.get("source")
    if source not in SOURCES:
        raise AccountError("bad_input", "unknown sign-in method")
    reg = cand.get("region") or "us-east-1"
    if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", reg):
        raise AccountError("bad_input", "not an AWS region")
    rec: dict = {"source": source, "region": reg,
                 "context": cand.get("context") if cand.get("context") in ("internal", "external")
                 else ("internal" if find_ada() else "external")}
    expected = cand.get("account_id")
    if expected is not None and not _ACCOUNT_RE.fullmatch(str(expected)):
        raise AccountError("bad_input", "an AWS account ID is 12 digits")
    if source == "ada":
        ada = cand.get("ada") or {}
        ada_bin = find_ada()
        if not ada_bin:
            raise AccountError("ada_not_found", "ada is not installed on this machine")
        role, provider = str(ada.get("role") or "Admin"), str(ada.get("provider") or "isengard").lower()
        if not expected:
            raise AccountError("bad_input", "ADA needs the account ID")
        if not _ADA_WORD_RE.fullmatch(role) or provider not in ADA_PROVIDERS:
            raise AccountError("bad_input", "not a valid ADA role or provider")
        rec["ada"] = {"role": role, "provider": provider, "bin": ada_bin}
    elif source == "profile":
        name = str(cand.get("profile") or "")
        found = _from_profile(name, find_ada()) if re.fullmatch(r"[\w.@+-]{1,64}", name) else None
        if not found or found.get("source") != "profile":
            raise AccountError("profile_not_found", f"profile {name!r} is not in ~/.aws")
        rec["profile"] = name
    elif source == "sso":
        sso = cand.get("sso") or {}
        from core import aws_signin
        try:
            start_url = aws_signin.check_start_url(sso.get("start_url"))
            sso_region = aws_signin.check_region(sso.get("sso_region"))
        except aws_signin.SigninError as exc:
            raise AccountError("bad_input", exc.message)
        if not expected or not re.fullmatch(r"[\w+=,.@-]{1,64}", str(sso.get("role") or "")):
            raise AccountError("bad_input", "AWS SSO needs the account and role")
        rec["sso"] = {"start_url": start_url, "sso_region": sso_region, "role": str(sso["role"])}
    elif source == "keys":
        from core import aws_signin
        key_id = str(cand.get("keys_id") or "")
        if not aws_signin.has_keys(key_id):
            raise AccountError("no_credentials", "store the access keys first")
        rec["keys_id"] = key_id
    elif source == "login":
        from core import aws_signin
        profile = str((cand.get("login") or {}).get("profile") or aws_signin.LOGIN_PROFILE)
        if not aws_signin.LOGIN_PROFILE_RE.fullmatch(profile) or \
                profile not in aws_signin.login_profiles():
            raise AccountError("bad_input", "sign in with AWS login first")
        rec["login"] = {"profile": profile}
    if expected:
        rec["account_id"] = str(expected)
    return rec


def connect(candidate: dict) -> dict:
    """Make *candidate* the SwarmAI account: ``verify_candidate`` then ``commit``. A
    failure leaves the current record exactly as it was (design §6.1 ④: never locked out)."""
    rec, creds = verify_candidate(candidate)
    return commit(rec, creds)


def verify_candidate(candidate: dict) -> "tuple[dict, Creds]":
    """Resolve *candidate* and check with STS that it is the account asked for (when one
    was given). Writes nothing."""
    rec = _check_candidate(candidate)
    creds = _RESOLVERS[rec["source"]](rec)
    try:
        ident = _sts_identity(creds, rec["region"])
    except Exception as exc:  # noqa: BLE001 — a key STS will not confirm is not used
        raise AccountError("verify_failed", f"could not verify: {type(exc).__name__}")
    actual = str(ident.get("Account"))
    if rec.get("account_id") and actual != rec["account_id"]:
        raise AccountMismatch(rec["account_id"], actual)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec.update({"account_id": actual, "caller_arn": str(ident.get("Arn")), "verified_at": now,
                "state": CONNECTED, "state_detail": None, "error_kind": None, "derived": False})
    return rec, creds


def commit(rec: dict, creds: Creds) -> dict:
    """Make a verified candidate the record (and the pin)."""
    global _cache
    with _lock, _record_lock:
        save_record(rec)
        _reset_cache()
        _verified[(_identity_key(rec), creds.access_key_id)] = rec["caller_arn"]
        _cache = creds
    cfg = _cfg()
    updates: dict = {"aws_region": rec["region"]}
    if cfg.get("auth_method") in (None, "", "ada", "sso"):
        # legacy readers (auth_remediation, the old settings card) follow the new source
        updates["auth_method"] = "ada" if rec["source"] == "ada" else "sso"
    cfg.update(updates)
    try:
        write_profile_file(rec["region"])
    except ValueError:
        logger.warning("aws_account: cannot write the swarmai profile", exc_info=True)
    logger.info("aws_account: connected %s via %s", rec["account_id"], rec["source"])
    return rec


def creds_for_record(rec: dict) -> Creds:
    """Credentials for a record that is not the current one (another account's Hives that
    were kept when the account changed, design §7.5), checked with STS against that
    record's account. Never cached, never the current pin."""
    resolver = _RESOLVERS.get(rec.get("source"))
    if resolver is None:
        raise AccountError("bad_record", "the kept account's sign-in method is unknown")
    # the kept record is data from the database: what reaches a command is checked again
    # exactly as at connect (the ada program found here, a profile that exists, ...)
    checked = _check_candidate(rec)
    if not checked.get("account_id"):
        raise AccountError("bad_record", "the kept record has no account")
    creds = resolver(checked)
    actual = str(_sts_identity(creds, rec.get("region") or "us-east-1").get("Account"))
    if actual != str(rec.get("account_id")):
        raise AccountMismatch(str(rec.get("account_id")), actual)
    return creds


def session(region: Optional[str] = None):
    """A boto3 session on the pinned credentials (static for their lifetime)."""
    import boto3
    creds = resolve()
    return boto3.Session(
        aws_access_key_id=creds.access_key_id, aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token, region_name=region or _main_region())


# ── legacy → record (design §5.6) ──────────────────────────────────────────────

_ACCT_IN_ARN = re.compile(r"arn:aws[\w-]*:(?:iam|sts)::(\d{12}):")


def _user_aws_config() -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    path = os.environ.get("AWS_CONFIG_FILE") or str(Path.home() / ".aws" / "config")
    try:
        cfg.read(os.path.expanduser(path), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        pass
    return cfg


def _ada_args(command: str) -> dict:
    """``--account`` / ``--role`` / ``--provider`` from an ada ``credential_process``."""
    try:
        words = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return {}
    words = [w.strip('"') for w in words]
    out: dict = {"bin": words[0] if words else ""}
    for i, w in enumerate(words):
        for flag in ("account", "role", "provider"):
            if w == f"--{flag}" and i + 1 < len(words):
                out[flag] = words[i + 1]
            elif w.startswith(f"--{flag}="):
                out[flag] = w.split("=", 1)[1]
    return out


def _from_profile(name: str, ada_bin: Optional[str]) -> Optional[dict]:
    """A record for the user's profile *name*; ``None`` when it does not exist."""
    cfg = _user_aws_config()
    section = "default" if name == "default" else f"profile {name}"
    has_creds_file_section = name in _user_credentials_sections()
    if not cfg.has_section(section) and not has_creds_file_section:
        return None
    sec = dict(cfg[section]) if cfg.has_section(section) else {}
    proc = sec.get("credential_process", "")
    if name == PROFILE_NAME or "aws-credentials" in proc:  # SwarmAI's own: would loop
        return None
    if proc:
        args = _ada_args(proc)
        if ada_bin and args.get("account") and _ACCOUNT_RE.fullmatch(args["account"]) \
                and _same_file(args.get("bin", ""), ada_bin):
            return {"source": "ada", "account_id": args["account"],
                    "ada": {"role": args.get("role") or "Admin",
                            "provider": args.get("provider") or "isengard", "bin": ada_bin}}
    account = sec.get("sso_account_id")
    for key in ("role_arn", "login_session"):
        m = _ACCT_IN_ARN.search(sec.get(key, ""))
        if m and not account:
            account = m.group(1)
    return {"source": "profile", "profile": name, "account_id": account}


def _user_credentials_sections() -> "list[str]":
    cfg = configparser.RawConfigParser()
    path = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or str(Path.home() / ".aws" / "credentials")
    try:
        cfg.read(os.path.expanduser(path), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return []
    return cfg.sections()


def _same_file(a: str, b: str) -> bool:
    try:
        return Path(os.path.expanduser(a)).resolve() == Path(b).resolve()
    except OSError:
        return False


def derive_from_legacy(config: Any) -> "tuple[Optional[dict], Optional[str]]":
    """(record, chat_via) for an install that has no record yet (design §5.6).

    Reads only local configuration (``auth_method``, ``ada profile print``, the user's
    ``~/.aws`` files); nothing is resolved here, ``resolve`` verifies afterwards. Never
    guesses: when nothing points at an AWS account the record is ``None``."""
    region = config.get("aws_region") or "us-east-1"
    context = config.get("deployment_context")
    auth = config.get("auth_method")
    chat_via = "bedrock_key" if auth == "bedrock_api_key" else "aws"
    if is_hive_mode():
        return ({"source": "instance_role", "account_id": None, "region": region,
                 "context": context}, "aws")
    ada_bin = find_ada()
    rec: Optional[dict] = None
    if auth == "ada" and ada_bin:
        p = _ada_profile_print(ada_bin)
        if p.get("account"):
            rec = {"source": "ada", "account_id": p["account"],
                   "ada": {"role": p.get("role") or "Admin",
                           "provider": (p.get("provider") or "isengard").lower(), "bin": ada_bin}}
    if rec is None:
        name = os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or "default"
        rec = _from_profile(name, ada_bin)
    if rec is None:
        return None, chat_via
    rec.update({"region": region, "context": context or ("internal" if ada_bin else "external"),
                "state": UNVERIFIED, "state_detail": "not verified yet", "derived": True})
    return rec, chat_via


# ── the credential_process profile + the daemon endpoint ──────────────────────

def backend_command() -> "list[str]":
    """The argv that runs this backend's ``aws-credentials`` subcommand."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "aws-credentials"]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "desktop_main.py"),
            "aws-credentials"]


_UNSAFE_IN_QUOTES = re.compile(r'["$`%!\n\r]')


def _quote(word: str) -> str:
    """One argv word for ``credential_process``. SDKs differ: botocore splits it itself,
    the JS / Go SDKs hand it to a shell (``sh -c``, ``cmd``). A word with a character any
    of them would expand or end the quotes on is refused rather than escaped."""
    if _UNSAFE_IN_QUOTES.search(word):
        raise ValueError(f"cannot put {word!r} in a credential_process line")
    if os.name != "nt":
        word = word.replace("\\", "\\\\")
    return f'"{word}"'


def write_profile_file(region: str) -> Path:
    """``<data dir>/aws/config`` with the one ``swarmai`` profile (design §5.3)."""
    if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region or ""):
        region = "us-east-1"
    cmd = " ".join(_quote(w) for w in backend_command())
    text = (
        "# Written by SwarmAI. Child processes use this profile; the daemon resolves and\n"
        "# pins the credentials. Do not edit: it is rewritten at every start.\n"
        f"[profile {PROFILE_NAME}]\n"
        f"credential_process = {cmd}\n"
        f"region = {region}\n")
    path = profile_file()
    _write_private(path, text)
    return path


_token: Optional[str] = None


def write_daemon_endpoint(port: int) -> None:
    """Where ``aws-credentials`` finds this daemon: port + a token made at each boot."""
    global _token
    _token = secrets.token_urlsafe(32)
    _write_private(daemon_endpoint_file(), json.dumps({"port": int(port), "token": _token}))


def token_ok(presented: Optional[str]) -> bool:
    if not _token or not presented:
        return False
    return hmac.compare_digest(_token.encode("utf-8"), presented.encode("utf-8", "replace"))


# ── capability checks (design §7.2) ─────────────────────────────────────────

def _err(exc: Exception) -> "tuple[str, str]":
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "")
    return code or type(exc).__name__, _redact(str(exc))


#: codes that mean "this identity may not do it" — anything else is "could not tell"
_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
           "UnauthorizedException", "AuthorizationError", "AuthFailure"}


def _verdict(exc: Exception, action: str) -> dict:
    code, msg = _err(exc)
    return {"status": "missing" if code in _DENIED else "unknown",
            "detail": f"{action} ({code})" if code in _DENIED else f"{action}: {code}: {msg}"}


def _bedrock_model_id(config: Any) -> Optional[str]:
    """The default model's Bedrock id, resolved the way a session resolves it
    (``config.get_bedrock_model_id``, as ``prompt_builder`` does)."""
    model = config.get("default_model")
    if not model:
        return None
    from config import get_bedrock_model_id
    mapping = config.get("bedrock_model_map")
    return get_bedrock_model_id(model, config_map=mapping if isinstance(mapping, dict) else None)


def create_default_vpc(region: str) -> dict:
    """Create the default VPC in *region* (the Hive check found none; design §7.2). Only
    when the person clicks it; AWS refuses when one already exists. Returns its id."""
    if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region or ""):
        raise AccountError("bad_input", "not an AWS region")
    ec2 = session(region).client("ec2", region_name=region)
    try:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
    except Exception as exc:  # noqa: BLE001
        code, msg = _err(exc)
        raise AccountError("missing" if code in _DENIED else "create_failed",
                           f"ec2:DescribeVpcs ({code}): {msg}")
    if vpcs:
        return {"vpc_id": vpcs[0]["VpcId"], "created": False}
    try:
        vpc = ec2.create_default_vpc()["Vpc"]
    except Exception as exc:  # noqa: BLE001
        code, msg = _err(exc)
        if code == "DefaultVpcAlreadyExists":   # made meanwhile (console, another click)
            again = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}]).get("Vpcs", [])
            return {"vpc_id": again[0]["VpcId"] if again else None, "created": False}
        raise AccountError("missing" if code in _DENIED else "create_failed",
                           f"ec2:CreateDefaultVpc ({code}): {msg}")
    logger.info("aws_account: created the default VPC %s in %s", vpc.get("VpcId"), region)
    return {"vpc_id": vpc.get("VpcId"), "created": True}


def _voice_check(sess: Any, rec: dict) -> dict:
    """Voice: one Polly call, and whether speech can be transcribed (Transcribe streaming
    has no cheap read call: its action is simulated against this identity's policy, as
    the Hive checks do; a simulation that cannot run reads "could not confirm")."""
    try:
        sess.client("polly").describe_voices(LanguageCode="en-US")
    except Exception as exc:  # noqa: BLE001
        return _verdict(exc, "polly:DescribeVoices")
    from core import aws_permissions
    principal = aws_permissions.principal_arn(sess, str(rec.get("caller_arn") or ""))
    denied, problem = aws_permissions._simulate(  # noqa: SLF001 — the one simulator
        sess, principal, ["polly:SynthesizeSpeech", "transcribe:StartStreamTranscription"])
    if denied:
        return {"status": "missing", "detail": f"missing {', '.join(denied)}", "missing": denied}
    if problem:
        return {"status": "unknown", "detail": f"Polly works; could not confirm transcription: {problem}"}
    return {"status": "ok", "detail": "Polly and Transcribe"}


def run_capability_checks(config: Any, hive_region: Optional[str] = None) -> dict:
    """One verdict per capability: ``ok`` | ``missing`` (with what is missing) |
    ``unknown`` (could not tell) | ``not_applicable``. Stored on the record. Hive and
    storage are "can it create" checks in *hive_region* (default the main region):
    DryRun and policy simulation, nothing is created (``core.aws_permissions``)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec = get_record() or {}
    region = _main_region()
    out: dict = {"checked_at": now}
    try:
        sess = session(region)
    except AccountError as exc:
        for cap in ("bedrock", "hive", "storage", "voice"):
            out[cap] = {"status": "unknown", "detail": exc.message}
        _update_record(capabilities=out)
        return out

    model_id = _bedrock_model_id(config)
    if get_chat_via() != "aws":
        out["bedrock"] = {"status": "not_applicable", "detail": "chat uses a key, not this account"}
    elif not model_id:
        out["bedrock"] = {"status": "unknown", "detail": "no Bedrock model id for the default model"}
    else:
        try:
            sess.client("bedrock-runtime").converse(
                modelId=model_id, messages=[{"role": "user", "content": [{"text": "ok"}]}],
                inferenceConfig={"maxTokens": 1})
            out["bedrock"] = {"status": "ok", "detail": model_id}
        except Exception as exc:  # noqa: BLE001 — every failure becomes a verdict
            out["bedrock"] = _verdict(exc, f"bedrock:InvokeModel {model_id}")

    if is_hive_mode():
        out["hive"] = out["storage"] = {"status": "not_applicable", "detail": "this is a Hive"}
        out["voice"] = {"status": "not_applicable", "detail": "this is a Hive"}
        _update_record(capabilities=out)
        return out

    from core import aws_permissions
    hive_region = hive_region or region
    try:
        out["hive"], out["storage"] = aws_permissions.run_hive_checks(
            sess if hive_region == region else session(hive_region), hive_region,
            str(rec.get("account_id") or ""), str(rec.get("caller_arn") or ""))
        out["hive"]["region"] = hive_region
    except Exception as exc:  # noqa: BLE001 — a check that breaks reads "could not tell"
        out["hive"] = {"status": "unknown", "region": hive_region,
                       "detail": f"{type(exc).__name__}: {_redact(str(exc))}"}
        out["storage"] = {k: v for k, v in out["hive"].items() if k != "region"}
    out["voice"] = _voice_check(sess, rec)
    _update_record(capabilities=out)
    return out


# ── startup + view ─────────────────────────────────────────────────────────────

def ensure_record(config: Any) -> Optional[dict]:
    """Create the record from the legacy config when there is none (design §5.6). With
    nothing to derive from, no record is stored and the next start tries again."""
    rec = get_record()
    if rec is not None:
        return rec
    rec, _chat_via = derive_from_legacy(config)
    if rec is None:
        return None
    save_record(rec)
    logger.info("aws_account: record created from the existing config (source=%s)",
                rec.get("source"))
    return rec


#: legacy ``auth_method`` → the record source it should produce
_METHOD_SOURCE = {"ada": "ada", "sso": "profile"}


def rederive(config: Any) -> Optional[dict]:
    """The sign-in method was changed in the legacy settings (``POST /system/auth-method``):
    derive the record again so the pinned account follows what the user chose. Until the
    wizard owns the record (step 4) that endpoint is how a user changes it.

    Only an AWS sign-in method moves the record (an API key changes the chat path, not the
    account), only to the source that method stands for (a derive that fell back to some
    other profile is not what the user chose), and never from a pinned account to one
    with no account id (that would re-pin to whatever answers first)."""
    with _record_lock:
        old = get_record()
        if old is not None and not old.get("derived"):
            return old  # connected in the account page or the wizard: it owns the record
        want = _METHOD_SOURCE.get(config.get("auth_method"))
        if want is None:
            return old
        rec, _chat_via = derive_from_legacy(config)
        if rec is None or rec.get("source") != want or _identity_key(rec) == _identity_key(old):
            return old
        if (old or {}).get("account_id") and not rec.get("account_id"):
            return old
        save_record(rec)
    logger.info("aws_account: record re-derived after a sign-in change (source=%s)",
                rec.get("source"))
    return rec


# ── what the daemon's consumers use (design §5.4) ──────────────────────────────

def active() -> bool:
    """True when the record can be resolved here: consumers use it, not the default chain."""
    if is_hive_mode():
        return False
    rec = get_record()
    return rec is not None and rec.get("source") in RESOLVABLE_SOURCES


def _refreshable_botocore_session():
    import botocore.session
    from botocore.credentials import RefreshableCredentials

    def fetch() -> dict:
        c = resolve()
        exp = c.expiration or (datetime.now(timezone.utc) + timedelta(hours=1))
        return {"access_key": c.access_key_id, "secret_key": c.secret_access_key,
                "token": c.session_token, "expiry_time": exp.isoformat()}

    bs = botocore.session.get_session()
    creds = RefreshableCredentials.create_from_metadata(
        metadata=fetch(), refresh_using=fetch, method="swarmai-account")
    # botocore asks for new credentials 15 / 10 min before expiry; resolve() keeps them
    # until 5 min before. Align, or every call in that window goes through resolve().
    creds._advisory_refresh_timeout = 4 * 60  # noqa: SLF001
    creds._mandatory_refresh_timeout = 2 * 60  # noqa: SLF001
    bs._credentials = creds  # noqa: SLF001
    return bs


def boto_session(region_name: Optional[str] = None):
    """The boto3 session every daemon consumer uses. On the pinned account (credentials
    refresh through ``resolve`` before they expire) when ``active()``; otherwise the
    default chain, as before. Raises ``AccountError`` when active and unresolvable:
    never a silent fall back to the default chain, which could be another account."""
    import boto3
    if not active():  # exactly the old default-chain session
        return boto3.Session(region_name=region_name) if region_name else boto3.Session()
    return boto3.Session(botocore_session=_refreshable_botocore_session(),
                         region_name=region_name or region())


def auth_check() -> str:
    """``valid`` | ``expired`` (sign in again) | ``mismatch`` (another account) | ``unknown``,
    for the spawn pre-flight and the readiness sampler. Only call when ``active()``."""
    try:
        resolve()
        return "valid"
    except NeedsLogin:
        return "expired"
    except AccountMismatch:
        return "mismatch"
    except AccountError as exc:
        return "broken" if exc.kind in _BROKEN_SETUP else "unknown"


#: resolution failures retrying will not fix: the record points at something gone or invalid
_BROKEN_SETUP = {"profile_not_found", "ada_not_found", "bad_record"}


def state_check() -> str:
    """``auth_check``'s answer from the record's last known state, with no network:
    for the readiness sampler, which must stay instant. Every resolution (a CLI's
    credential request, a job, the pre-flight) keeps that state current."""
    rec = get_record() or {}
    return {CONNECTED: "valid", NEEDS_LOGIN: "expired", MISMATCH: "mismatch"}.get(
        rec.get("state"), "unknown")


def remediation_method(config: Any = None) -> Optional[str]:
    """The key of ``core.auth_remediation`` that matches how this account signs in."""
    config = config if config is not None else _cfg()
    via = get_chat_via(config)
    if via == "bedrock_key":
        return "bedrock_api_key"
    rec = get_record()
    if active() and rec and rec.get("error_kind") in _BROKEN_SETUP:
        return "account_broken"
    if active() and rec:
        return {"ada": "ada_account", "profile": "profile", "instance_role": "iam_role",
                "sso": "sso_app", "login": "aws_login", "keys": "access_keys"}.get(
            rec.get("source"), config.get("auth_method"))
    return config.get("auth_method")


def credential_export_command() -> str:
    """The shell command the CLI runs for ``awsCredentialExport``."""
    return " ".join(_quote(w) for w in backend_command())


def cli_settings(config: Any = None, *, bedrock: bool = False) -> dict:
    """Settings merged into a CLI spawn's ``--settings`` (design §5.3). ``bedrock=True``
    for a headless run that always uses Bedrock whatever the chat path is (jobs).

    Chat on Bedrock with an active record: ``awsCredentialExport`` makes the CLI ask this
    daemon (through the subcommand) for the pinned credentials, at start and before
    they expire, instead of resolving the default chain. Only the CLI's own AWS calls
    use them; the environment the agent's commands run in keeps the user's AWS
    configuration, so its own profiles keep working. Otherwise: nothing (as before)."""
    config = config if config is not None else _cfg()
    if not active() or (not bedrock and get_chat_via(config) != "aws"):
        return {}
    try:
        return {"awsCredentialExport": credential_export_command()}
    except ValueError:
        # the install path holds a character a shell would expand: the CLI then uses its
        # own default chain (as it does whenever the export fails, design §8)
        logger.error("aws_account: cannot build the credential export command", exc_info=True)
        return {}


def credential_resolver():
    """An ``amazon_transcribe`` credential resolver on the pinned account, or None (its
    own default chain, as before) when not active."""
    if not active():
        return None
    from amazon_transcribe.auth import CredentialResolver, Credentials

    class _Pinned(CredentialResolver):
        async def get_credentials(self):
            import asyncio
            c = await asyncio.to_thread(resolve)
            return Credentials(c.access_key_id, c.secret_access_key, c.session_token)

    return _Pinned()


def retire_anthropic_direct(config: Any) -> bool:
    """Move an install that chatted through an Anthropic key onto Bedrock (Anthropic
    direct is no longer supported). Only the two settings that chose it change:
    ``use_bedrock`` becomes true and ``auth_method`` becomes the legacy name of the
    record's sign-in (ada, a Hive's iam_role), else unset: an unset method gets the
    method-agnostic fix text rather than a guessed command. The stored key is left in
    the secret store, unused: nothing the person saved is deleted. True when it moved."""
    auth = config.get("auth_method")
    if auth != "apikey" and config.get("use_bedrock", True) is not False:
        return False
    update: "dict[str, Any]" = {"use_bedrock": True}
    if auth == "apikey":   # any other method (bedrock_api_key, sso, ada) is kept as chosen
        rec = get_record() or {}
        update["auth_method"] = {"ada": "ada", "instance_role": "iam_role"}.get(str(rec.get("source")))
    method = update.get("auth_method", auth)
    config.update(update)
    logger.info("aws_account: Anthropic-direct chat retired; now Bedrock (auth_method=%s)", method)
    return True


def startup(port: int) -> None:
    """Daemon startup (blocking; run it in a thread): record, profile file, endpoint,
    then one verification, and one capability check if none was ever made. An old
    Anthropic-direct setup is moved onto Bedrock, also when the record cannot be made."""
    cfg = _cfg()
    if not is_hive_mode():  # a Hive's children use its instance role directly
        # first: child processes reach this daemon even when the record cannot be made
        write_daemon_endpoint(port)
        try:
            write_profile_file(cfg.get("aws_region") or "us-east-1")
        except ValueError:
            logger.warning("aws_account: cannot write the swarmai profile", exc_info=True)
    try:
        rec = ensure_record(cfg)
    except Exception:  # noqa: BLE001 — e.g. a hand-edited config.json that does not parse
        logger.warning("aws_account: could not create the record", exc_info=True)
        rec = None
    # also without a record: chat must not stay on a path that no longer exists
    try:
        retire_anthropic_direct(cfg)
    except Exception:  # noqa: BLE001 — never stop startup over it
        logger.warning("aws_account: could not move off Anthropic-direct", exc_info=True)
    if rec is None:
        return
    if not is_hive_mode() and rec.get("region") and rec.get("region") != cfg.get("aws_region"):
        try:
            write_profile_file(rec["region"])
        except ValueError:
            pass
    try:
        resolve(force=True)
    except AccountError as exc:
        logger.warning("aws_account: not verified at startup: %s (%s)", exc.message, exc.kind)
        return
    if not (get_record() or {}).get("capabilities"):
        # a dozen AWS calls: on a thread of its own, not the pool startup runs on
        threading.Thread(target=run_capability_checks, args=(cfg,), daemon=True,
                         name="aws-capability-checks").start()


def view() -> dict:
    """What ``GET /api/aws-account`` returns. The record holds no secret."""
    rec = get_record()
    state = UNCONNECTED if rec is None else (rec.get("state") or UNVERIFIED)
    public = None
    if rec is not None:
        public = {k: rec.get(k) for k in ("source", "account_id", "region", "context", "profile",
                                         "caller_arn", "verified_at", "derived")}
        ada = rec.get("ada") or {}
        public["ada"] = {"role": ada.get("role"), "provider": ada.get("provider")} if ada else None
        sso = rec.get("sso") or {}
        public["sso"] = {k: sso.get(k) for k in ("start_url", "sso_region", "role")} if sso else None
    return {
        "state": state,
        "state_detail": (rec or {}).get("state_detail"),
        "account": public,
        "chat_via": get_chat_via(),
        "region": region(),   # the main region (Settings → Swarm AWS Account writes aws_region)
        "capabilities": (rec or {}).get("capabilities"),
        "profile_file": str(profile_file()),
        "supported": rec is None or rec.get("source") in RESOLVABLE_SOURCES,
        "remediation": _remediation(state),
    }


def _remediation(state: str) -> Optional[dict]:
    """What the credential banner tells the user when the credentials do not work (it
    shows only on health's "expired"). With the account in use: for a state that needs
    them, else nothing. Without it (no record, a source this build cannot resolve, a
    Hive): the fix for the legacy sign-in method (``auth_method``), so those users keep
    the method-aware text and one-click action (design 2026-11-01 §4.2)."""
    from core.auth_remediation import remediation_for
    if is_hive_mode():   # a Hive signs in with its instance role, whatever auth_method says
        return dict(remediation_for("iam_role"))
    if not active():
        return dict(remediation_for(_cfg().get("auth_method")))
    if state not in (NEEDS_LOGIN, MISMATCH):
        return None
    method = "account_mismatch" if state == MISMATCH else remediation_method()
    out = dict(remediation_for(method))
    if method == "profile":
        cmd = _profile_login_command(get_record() or {})
        if cmd:
            out["action"] = {"kind": "terminal", "command": cmd, "title": "AWS sign-in"}
    return out


#: a profile name that may be typed into a shell as one word (design 2026-11-01 §4.2)
#: ASCII only, no leading "-" (never an option), at most 64 characters, used with fullmatch
_SAFE_PROFILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,63}")


def _profile_login_command(rec: dict) -> Optional[str]:
    """``aws sso login --profile <name>`` for a profile-sourced record whose name is safe
    to type into a shell; None otherwise (the banner then opens the account page). Only
    an SSO profile signs in this way; any other profile has no login command."""
    name = str(rec.get("profile") or "")
    if not _SAFE_PROFILE.fullmatch(name) or not _profile_is_sso(name):
        return None
    return f"aws sso login --profile {name}"


def _profile_is_sso(name: str) -> bool:
    """Whether ~/.aws/config gives *name* an SSO sign-in (sso_session or sso_start_url)."""
    try:
        cp = _user_aws_config()
    except (configparser.Error, OSError):
        return False
    sec = "default" if name == "default" else f"profile {name}"
    return cp.has_section(sec) and (cp.has_option(sec, "sso_session") or cp.has_option(sec, "sso_start_url"))
