"""Find the AWS accounts this machine can already reach (design 2026-10-31-swarmai-aws-account
§6.1 ②): two stages.

**Stage 1 — read the configuration, run nothing** (milliseconds): every profile in the
person's ``~/.aws/config`` and ``~/.aws/credentials``, with the account and role when the
file says them (ADA's ``credential_process`` arguments, ``sso_account_id`` /
``sso_role_name``, the account in a ``role_arn`` / ``login_session``), plus ADA's own
profile (``ada profile print``, ADA's local config).

**Stage 2 — verify, one row at a time, each in its own killable process.** Only rows
that can be checked without a prompt or a write to the person's files are verified
automatically (``auto``): static keys (one STS call), the ``credential_process`` of the
ada found here (the same program SwarmAI runs; no prompt while Midway is valid), an
assume-role from static keys without MFA. Everything else waits for the person's click
(``check``: another program in ``credential_process`` may show a Touch ID / YubiKey /
keychain prompt or hang; an MFA role) or is a sign-in to start in the app (``login``:
SSO and console-login profiles are recorded as SwarmAI's own sign-in, never by reusing
the person's CLI cache, design §6.1). A verification never refreshes a token, never
reads stdin, and is killed with its children at a hang guard.

Nothing here writes a file. The account's records are built by ``aws_account.connect``.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any, Optional

from core import aws_account

_HANG_GUARD_S = 45  # one check that never answers is killed (not a business timeout)
JOB_ENV = "SWARM_AWS_DETECT_JOB"
_ACCOUNT_RE = re.compile(r"\d{12}")
_ROLE_IN_ARN = re.compile(r"arn:aws[\w-]*:iam::(\d{12}):role/(?:[\w+=,.@/-]*/)?([\w+=,.@-]+)")
_NAME_RE = re.compile(r"[\w.@+-]{1,64}")


def _read(path: str) -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    try:
        cfg.read(os.path.expanduser(path), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        pass
    return cfg


def _config_path() -> str:
    return os.environ.get("AWS_CONFIG_FILE") or str(Path.home() / ".aws" / "config")


def _credentials_path() -> str:
    return os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or str(Path.home() / ".aws" / "credentials")


def _row(name: str, kind: str, verify: str, **kw: Any) -> dict:
    return {"profile": name, "kind": kind, "verify": verify, "account_id": kw.get("account_id"),
            "role": kw.get("role"), "detail": kw.get("detail"), "sso": kw.get("sso"),
            "ada": kw.get("ada")}


def _sso_fields(cfg: configparser.RawConfigParser, sec: dict) -> dict:
    start, region = sec.get("sso_start_url"), sec.get("sso_region")
    session = sec.get("sso_session")
    if session and cfg.has_section(f"sso-session {session}"):
        s = dict(cfg[f"sso-session {session}"])
        start, region = start or s.get("sso_start_url"), region or s.get("sso_region")
    return {"start_url": start, "sso_region": region}


def stage1() -> list:
    """Every profile with what its configuration says. No program runs except
    ``ada profile print`` (ADA's own local config, no network)."""
    cfg = _read(_config_path())
    creds = _read(_credentials_path())
    ada_bin = aws_account.find_ada()
    rows: list = []
    names: list = []
    for section in cfg.sections():
        if section == "default":
            names.append("default")
        elif section.startswith("profile "):
            names.append(section[len("profile "):].strip())
    names += [n for n in creds.sections() if n not in names]
    for name in names:
        if not _NAME_RE.fullmatch(name) or name == aws_account.PROFILE_NAME:
            continue
        section = "default" if name == "default" else f"profile {name}"
        sec = dict(cfg[section]) if cfg.has_section(section) else {}
        keys = dict(creds[name]) if creds.has_section(name) else {}
        proc = sec.get("credential_process", "") or keys.get("credential_process", "")
        if "aws-credentials" in proc:
            continue  # SwarmAI's own profile: asking it would loop
        if proc:
            args = aws_account._ada_args(proc)  # noqa: SLF001
            if ada_bin and _ACCOUNT_RE.fullmatch(args.get("account", "")) and \
                    aws_account._same_file(args.get("bin", ""), ada_bin):  # noqa: SLF001
                rows.append(_row(name, "ada", "auto", account_id=args["account"],
                                 role=args.get("role") or "Admin",
                                 ada={"role": args.get("role") or "Admin",
                                      "provider": (args.get("provider") or "isengard").lower()}))
            else:
                rows.append(_row(name, "process", "check",
                                 detail="runs another program; it may ask for confirmation"))
            continue
        if sec.get("sso_account_id") or sec.get("sso_session") or sec.get("sso_start_url"):
            rows.append(_row(name, "sso", "login", account_id=sec.get("sso_account_id"),
                             role=sec.get("sso_role_name"), sso=_sso_fields(cfg, sec)))
            continue
        if sec.get("login_session"):
            m = aws_account._ACCT_IN_ARN.search(sec["login_session"])  # noqa: SLF001
            rows.append(_row(name, "login", "login", account_id=m.group(1) if m else None))
            continue
        if sec.get("role_arn"):
            m = _ROLE_IN_ARN.search(sec["role_arn"])
            source = sec.get("source_profile")
            static_source = bool(source) and creds.has_section(source) and \
                "aws_access_key_id" in dict(creds[source])
            auto = static_source and not sec.get("mfa_serial")
            rows.append(_row(name, "assume_role", "auto" if auto else "check",
                             account_id=m.group(1) if m else None, role=m.group(2) if m else None,
                             detail=None if auto else "may ask for MFA or run another program"))
            continue
        if keys.get("aws_access_key_id") or sec.get("aws_access_key_id"):
            rows.append(_row(name, "keys", "auto", detail="access keys"))
    if ada_bin:
        p = aws_account._ada_profile_print(ada_bin)  # noqa: SLF001
        if _ACCOUNT_RE.fullmatch(p.get("account", "")):
            rows.insert(0, _row("", "ada_default", "auto", account_id=p["account"],
                                role=p.get("role") or "Admin",
                                ada={"role": p.get("role") or "Admin",
                                     "provider": (p.get("provider") or "isengard").lower()},
                                detail="ADA's own profile"))
    return rows


def find_row(profile: str) -> Optional[dict]:
    """The stage-1 row of *profile* (``""`` = ADA's own profile): verification only ever
    runs what the person's own configuration says, never what a request says."""
    for row in stage1():
        if row["profile"] == profile:
            return row
    return None


def _worker_argv() -> "tuple[list, Optional[str]]":
    from utils.backend_bin import is_frozen, python_argv
    cwd = None if is_frozen() else str(Path(__file__).resolve().parents[1])
    return [*python_argv(), "-m", "core.aws_detect_worker"], cwd


def verify(row: dict, *, user_asked: bool) -> dict:
    """Verify one stage-1 row in a child process: ``{"status": "verified", "account_id",
    "arn"}`` | ``{"status": "invalid" | "needs_login" | "failed" | "timeout", "detail"}``.
    A row whose ``verify`` is ``check`` runs only when the person asked; ``login`` rows
    are never run (they are signed in to in the app)."""
    if row["verify"] == "login":
        return {"status": "needs_login", "detail": "sign in to it in SwarmAI"}
    if row["verify"] == "check" and not user_asked:
        return {"status": "check", "detail": row.get("detail")}
    job = {"kind": row["kind"], "profile": row["profile"],
           "ada": {**(row.get("ada") or {}), "account_id": row.get("account_id"),
                   "bin": aws_account.find_ada()} if row["kind"] in ("ada", "ada_default") else None,
           "region": aws_account.region()}
    argv, cwd = _worker_argv()
    env = {k: v for k, v in os.environ.items() if k not in (
        "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN")}
    env["AWS_EC2_METADATA_DISABLED"] = "true"  # a laptop: never wait on instance metadata
    env[JOB_ENV] = json.dumps(job)  # no secret in it: a profile name and what its config says
    # stdin closed: nothing in the profile's own program can wait for input
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, cwd=cwd, env=env,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=_HANG_GUARD_S)
    except subprocess.TimeoutExpired:
        _kill(proc)
        return {"status": "timeout", "detail": f"no answer within {_HANG_GUARD_S}s"}
    _kill(proc)  # anything the profile's program left behind in its process group
    try:
        return json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "failed", "detail": "the check did not finish"}


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


