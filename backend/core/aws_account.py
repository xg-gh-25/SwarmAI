"""aws_account — the one SwarmAI AWS account: its record, resolution and pin.

Design: ``docs/design/2026-10-31-swarmai-aws-account.md`` (§5 mechanism, §9 step 1).
SwarmAI uses ONE AWS account for Bedrock, Hive, cloud storage and voice. This module
holds the record of that account and is the place that turns it into credentials.

What step 1 provides (no consumer is migrated yet — design §9 step 2 does that):
- the record (``config.json`` key ``aws_account``) and the chat path (key ``chat``);
  neither is writable through the generic ``PUT /api/settings`` (``PROTECTED_CONFIG_KEYS``);
- ``derive_from_legacy``: the record an existing install starts with (design §5.6),
  from ``auth_method`` and the credentials the daemon already uses;
- ``resolve``: short-term credentials for the record's source (``ada``, ``profile``,
  ``instance_role``; ``sso`` / ``login`` / ``keys`` arrive with the wizard, step 4),
  **pinned**: every new access key is checked with STS against the record's account,
  and a different account raises ``AccountMismatch`` instead of being used;
- ``write_profile_file``: ``<data dir>/aws/config``, one profile ``swarmai`` whose
  ``credential_process`` is ``python-backend aws-credentials`` (design §5.3);
- ``write_daemon_endpoint`` / ``token_ok``: how that subcommand reaches this daemon
  (``<data dir>/aws/daemon.json``, port + a per-boot token, 0600);
- ``run_capability_checks``: Bedrock, Hive, cloud storage, voice (design §7.2). The
  Hive and storage checks are the read-only ones the Hive account verify already ran;
  "can it create" (DryRun / policy simulation) is step 5, and the result says so.

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
#: sources this version can resolve; the others are recorded but answer "unsupported"
RESOLVABLE_SOURCES = ("ada", "profile", "instance_role")

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


def get_chat_via() -> str:
    chat = _cfg().get(CHAT_KEY)
    via = chat.get("via") if isinstance(chat, dict) else None
    return via if via in ("aws", "bedrock_key", "anthropic") else "aws"


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
    cmd = [ada_bin, "credentials", "print", f"--account={account}", f"--role={role}",
           f"--provider={provider}", "--format=json"]
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


_RESOLVERS = {"ada": _ada_creds, "profile": _profile_creds, "instance_role": _instance_role_creds}


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
    return (rec.get("source"), rec.get("account_id"), rec.get("profile"),
            ada.get("role"), ada.get("provider"), ada.get("bin"))


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
        config=Config(connect_timeout=_STS_TIMEOUT_S, read_timeout=_STS_TIMEOUT_S,
                      retries={"max_attempts": 2}))
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
            _update_record(state=state, state_detail=exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 — STS / network: a key that cannot be pinned is not served
            _cache = None
            _update_record(state=UNVERIFIED,
                           state_detail=f"{type(exc).__name__}: {_redact(str(exc))}")
            raise AccountError("verify_failed", f"could not verify: {type(exc).__name__}")
        # every success says so, also when the key was verified before a failed attempt
        _update_record(state=CONNECTED, state_detail=None)
        _cache = creds
        return creds


def session(region: Optional[str] = None):
    """A boto3 session on the pinned credentials (static for their lifetime)."""
    import boto3
    creds = resolve()
    rec = get_record() or {}
    return boto3.Session(
        aws_access_key_id=creds.access_key_id, aws_secret_access_key=creds.secret_access_key,
        aws_session_token=creds.session_token, region_name=region or rec.get("region") or "us-east-1")


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
    chat_via = {"apikey": "anthropic", "bedrock_api_key": "bedrock_key"}.get(auth, "aws")
    if config.get("use_bedrock") is False:
        chat_via = "anthropic"
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


# ── capability checks (design §7.2; Hive / storage "can create" is step 5) ─────

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


def run_capability_checks(config: Any) -> dict:
    """One verdict per capability: ``ok`` | ``missing`` (with what is missing) |
    ``unknown`` (could not tell) | ``not_applicable``. Stored on the record."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec = get_record() or {}
    region = rec.get("region") or "us-east-1"
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

    verdicts: list = []
    detail_vpc = None
    for name, call in (
        ("ec2:DescribeInstances", lambda: sess.client("ec2").describe_instances(MaxResults=5)),
        ("iam:ListRoles", lambda: sess.client("iam").list_roles(MaxItems=1)),
        ("cloudfront:ListDistributions", lambda: sess.client("cloudfront").list_distributions(MaxItems="1")),
    ):
        try:
            call()
        except Exception as exc:  # noqa: BLE001
            verdicts.append(_verdict(exc, name))
    try:
        vpcs = sess.client("ec2").describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        if not vpcs.get("Vpcs"):
            detail_vpc = f"no default VPC in {region}"
    except Exception:  # noqa: BLE001 — already reported through DescribeInstances, or unknowable
        pass
    basis = "read-only check; whether it can create is checked in a later version"
    if verdicts:
        status = "missing" if any(v["status"] == "missing" for v in verdicts) else "unknown"
        out["hive"] = {"status": status, "detail": "; ".join(v["detail"] for v in verdicts),
                       "basis": basis}
    else:
        out["hive"] = {"status": "missing" if detail_vpc else "ok",
                       "detail": detail_vpc or "reads EC2, IAM, CloudFront", "basis": basis}
    try:
        sess.client("s3").list_buckets()
        out["storage"] = {"status": "ok", "detail": "lists buckets", "basis": basis}
    except Exception as exc:  # noqa: BLE001
        out["storage"] = {**_verdict(exc, "s3:ListAllMyBuckets"), "basis": basis}
    try:
        sess.client("polly").describe_voices(LanguageCode="en-US")
        out["voice"] = {"status": "ok", "detail": "Polly; transcription is confirmed on first use"}
    except Exception as exc:  # noqa: BLE001
        out["voice"] = _verdict(exc, "polly:DescribeVoices")
    _update_record(capabilities=out)
    return out


# ── startup + view ─────────────────────────────────────────────────────────────

def ensure_record(config: Any) -> Optional[dict]:
    """Create the record from the legacy config when there is none (design §5.6)."""
    rec = get_record()
    if rec is not None:
        return rec
    rec, chat_via = derive_from_legacy(config)
    updates: dict = {RECORD_KEY: rec}
    if not isinstance(config.get(CHAT_KEY), dict):
        updates[CHAT_KEY] = {"via": chat_via or "aws"}
    _cfg().update(updates)
    logger.info("aws_account: record created from the existing config (source=%s)",
                (rec or {}).get("source"))
    return rec


def startup(port: int) -> None:
    """Daemon startup (blocking; run it in a thread): record, profile file, endpoint,
    then one verification, and one capability check if none was ever made."""
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
        return
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
        run_capability_checks(cfg)


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
    return {
        "state": state,
        "state_detail": (rec or {}).get("state_detail"),
        "account": public,
        "chat_via": get_chat_via(),
        "capabilities": (rec or {}).get("capabilities"),
        "profile_file": str(profile_file()),
        "supported": rec is None or rec.get("source") in RESOLVABLE_SOURCES,
    }
