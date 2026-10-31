"""The sign-in sources SwarmAI runs itself: IAM Identity Center (``sso``), AWS console sign-in
(``login``) and access keys (``keys``) (design 2026-10-31-swarmai-aws-account §5.1, §6.3,
§6.3b). Each gives ``core.aws_account`` a resolver ``(record) -> Creds``.

- **SSO**: the OIDC device flow through botocore (``sso-oidc``: RegisterClient,
  StartDeviceAuthorization, CreateToken; ``sso``: ListAccounts, ListAccountRoles,
  GetRoleCredentials). Started only by the person in the app. The client and the tokens
  live in the daemon's secret store (``secrets.json``, 0600, never backed up), never in
  ``~/.aws/sso/cache``, so the person's own CLI sign-in is untouched. The refresh token
  reaches every account and role this person has in that Identity Center, not only the
  pinned one: only the daemon reads it. An expired token is refreshed; when that fails
  the account needs a new sign-in (``NeedsLogin``). An expired or rejected client is
  registered again, never reported as a sign-in problem.
- **One slot per identity**: SSO sign-ins are kept per start URL, access keys per key
  ID, AWS logins per SwarmAI login profile. A sign-in for another account therefore
  never overwrites what the current account uses: it sits next to it until a connect
  commits it, and ``prune`` (after a commit) keeps only what the current record and
  the Hives kept from a previous account still use (design §6.1 ④: never locked out;
  §7.5: the old account's Hives keep their sign-in). A client is saved only with the
  token it issued, so an abandoned sign-in changes nothing.
- **AWS login**: delegated to the AWS CLI (``aws login`` / ``aws configure
  export-credentials``) with SwarmAI's own config file and cache directory, so the
  person's ``~/.aws`` is untouched; the CLI renews the session (the daemon's ``awscrt``
  cannot, §6.3b). Needs AWS CLI 2.32 or later.
- **Access keys**: kept in the secret store; static, so they are checked with STS like
  every other source but never expire on their own.

Nothing here writes the person's ``~/.aws``. Secrets never enter a log, a response or
the record.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SSO_SECRET = "aws_sso_session"
KEYS_SECRET = "aws_account_keys"
LOGIN_PROFILE = "swarmai-login"          # the first AWS login profile (records before per-login profiles)
LOGIN_PROFILE_RE = re.compile(r"swarmai-login(-[0-9a-f]{8})?")
MIN_CLI = (2, 32, 0)

_REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
_ACCOUNT_RE = re.compile(r"\d{12}")
_ROLE_RE = re.compile(r"[\w+=,.@-]{1,64}")
_KEY_ID_RE = re.compile(r"(AKIA|ASIA)[A-Z0-9]{12,124}")
_SECRET_RE = re.compile(r"[A-Za-z0-9/+=]{30,128}")
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_FLOW_TTL_S = 15 * 60
_LOGIN_HANG_GUARD_S = 11 * 60   # the CLI gives up after 10 min itself
_EXPORT_TIMEOUT_S = 60          # hang guard for one export


class SigninError(Exception):
    """A sign-in step failed with a stable ``kind`` the UI can act on."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _aws():
    from core import aws_account
    return aws_account


def _cfg():
    from core.app_config_manager import AppConfigManager
    return AppConfigManager.instance()


def _read_secret(key: str) -> Optional[dict]:
    raw = _cfg().get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _write_secret(key: str, data: Optional[dict]) -> None:
    _cfg().set_secret(key, json.dumps(data) if data is not None else "")


#: read-modify-write of the SSO and key stores (sign-ins and refreshes run in threads)
_store_lock = threading.RLock()


def _sso_store() -> dict:
    """``{"sessions": {start_url: {start_url, region, client, token}}}``; the first
    shape (one sign-in at the top level) reads as one session."""
    raw = _read_secret(SSO_SECRET) or {}
    pending = {k: dict(v) for k, v in (raw.get("pending_clients") or {}).items() if isinstance(v, dict)}
    if isinstance(raw.get("sessions"), dict):
        return {"sessions": {k: dict(v) for k, v in raw["sessions"].items() if isinstance(v, dict)},
                "pending_clients": pending}
    if raw.get("start_url"):
        return {"sessions": {raw["start_url"]: {k: raw.get(k) for k in
                                                ("start_url", "region", "client", "token")}},
                "pending_clients": pending}
    return {"sessions": {}, "pending_clients": pending}


def _sso_session(start_url: Optional[str]) -> dict:
    return dict(_sso_store()["sessions"].get(start_url or "") or {})


def _save_sso_session(start_url: str, data: dict) -> None:
    with _store_lock:
        store = _sso_store()
        store["sessions"][start_url] = data
        if (store["pending_clients"].get(start_url) or {}).get("id") == (data.get("client") or {}).get("id"):
            store["pending_clients"].pop(start_url, None)   # it became the session's client
        _write_secret(SSO_SECRET, store)


def _keys_store() -> dict:
    """``{"keys": {access key id: secret}}``; the first shape (one pair) reads as one."""
    raw = _read_secret(KEYS_SECRET) or {}
    if isinstance(raw.get("keys"), dict):
        return {"keys": dict(raw["keys"])}
    if raw.get("id") and raw.get("secret"):
        return {"keys": {raw["id"]: raw["secret"]}}
    return {"keys": {}}


def _unsigned(service: str, region: str):
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    # endpoints come from botocore only: an ``endpoint_url`` in ~/.aws/config or an
    # AWS_ENDPOINT_URL_* variable must not receive SwarmAI's Identity Center tokens
    return boto3.client(service, region_name=region, config=Config(
        signature_version=UNSIGNED, connect_timeout=15, read_timeout=30,
        retries={"max_attempts": 2}, ignore_configured_endpoint_urls=True))


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return (resp.get("Error") or {}).get("Code", "") or type(exc).__name__
    return type(exc).__name__


# ── SSO: validation ────────────────────────────────────────────────────────────

#: an AWS access portal: ``<x>.awsapps.com`` (``.cn`` in China) or ``<x>.portal.<region>.app.aws``
_PORTAL_HOST_RE = re.compile(r"[a-z0-9-]+\.(awsapps\.com|awsapps\.cn|portal\.[a-z]{2}(-[a-z]+)+-\d\.app\.aws)")
#: the device sign-in page Identity Center links to
_DEVICE_HOST_RE = re.compile(r"device\.sso\.[a-z]{2}(-[a-z]+)+-\d\.amazonaws\.com(\.cn)?")


def _host_ok(url: str, *patterns: "re.Pattern") -> bool:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and not parts.username and not parts.password and \
        parts.port in (None, 443) and any(p.fullmatch(host) for p in patterns)


def check_start_url(url: str) -> str:
    """An Identity Center start URL: https on an AWS access portal host (exact patterns:
    a suffix such as ``.aws`` or ``.amazonaws.com`` also matches Lambda URLs and API
    Gateway, which anyone can own). Only passed to AWS APIs."""
    url = (url or "").strip()
    if not _host_ok(url, _PORTAL_HOST_RE):
        raise SigninError("bad_input", "the start URL must look like https://xxx.awsapps.com/start")
    return url


def check_region(value: str) -> str:
    if not _REGION_RE.fullmatch(value or ""):
        raise SigninError("bad_input", "not an AWS region")
    return value


def _verification_url_ok(url: str) -> bool:
    """The link the app opens in the browser: Identity Center's device page or the
    access portal, nothing else."""
    return _host_ok(url, _DEVICE_HOST_RE, _PORTAL_HOST_RE)


# ── SSO: device flow ───────────────────────────────────────────────────────────

_flows: dict[str, dict] = {}
_flows_lock = threading.Lock()


def _client(start_url: str, region: str, *, fresh: bool = False) -> dict:
    """The registered OIDC client for this start URL (registered again when expired). A
    newly registered client goes to ``pending_clients``, never into the session: the
    session's client (the one its refresh token belongs to) changes only together with
    the token (``_store_token``), so a sign-in that is never finished leaves it as it was."""
    store = _sso_store()
    now = time.time()

    def usable(c: dict) -> bool:
        return c.get("start_url") == start_url and c.get("region") == region and \
            c.get("expires_at", 0) - now > 3600
    if not fresh:
        for cand in ((store["sessions"].get(start_url) or {}).get("client") or {},
                     store["pending_clients"].get(start_url) or {}):
            if usable(cand):
                return cand
    oidc = _unsigned("sso-oidc", region)
    try:
        resp = oidc.register_client(clientName="SwarmAI", clientType="public",
                                    scopes=["sso:account:access"],
                                    grantTypes=[_DEVICE_GRANT, "refresh_token"],
                                    issuerUrl=start_url)
    except Exception as exc:  # noqa: BLE001 — every failure is reported, none hides
        raise SigninError("sso_failed", f"could not register with Identity Center ({_code(exc)})")
    client = {"id": resp["clientId"], "secret": resp["clientSecret"],
              "expires_at": int(resp.get("clientSecretExpiresAt") or now + 86400),
              "start_url": start_url, "region": region}
    with _store_lock:
        store = _sso_store()
        store["pending_clients"][start_url] = client
        _write_secret(SSO_SECRET, store)
    return client


def sso_start(start_url: str, sso_region: str) -> dict:
    """Start a device sign-in. Returns what the person sees: the code and the link."""
    start_url, sso_region = check_start_url(start_url), check_region(sso_region)
    client = _client(start_url, sso_region)
    oidc = _unsigned("sso-oidc", sso_region)
    try:
        auth = oidc.start_device_authorization(
            clientId=client["id"], clientSecret=client["secret"], startUrl=start_url)
    except Exception as exc:  # noqa: BLE001
        if _code(exc) in ("InvalidClientException", "UnauthorizedClientException"):
            client = _client(start_url, sso_region, fresh=True)
            auth = oidc.start_device_authorization(
                clientId=client["id"], clientSecret=client["secret"], startUrl=start_url)
        else:
            raise SigninError("sso_failed", f"Identity Center refused the sign-in ({_code(exc)})")
    link = auth.get("verificationUriComplete") or auth.get("verificationUri") or ""
    if not _verification_url_ok(link):
        raise SigninError("sso_failed", "Identity Center returned an unexpected sign-in link")
    flow_id = secrets.token_urlsafe(16)
    with _flows_lock:
        now = time.time()
        for fid in [f for f, v in _flows.items() if v["expires_at"] < now]:
            _flows.pop(fid, None)
        _flows[flow_id] = {"device_code": auth["deviceCode"], "client": client,
                           "start_url": start_url, "region": sso_region,
                           "interval": int(auth.get("interval") or 5),
                           "expires_at": now + min(int(auth.get("expiresIn") or 600), _FLOW_TTL_S)}
    return {"flow_id": flow_id, "user_code": auth.get("userCode"), "verification_url": link,
            "interval": int(auth.get("interval") or 5),
            "expires_in": int(auth.get("expiresIn") or 600)}


def _store_token(start_url: str, region: str, tok: dict, client: dict) -> None:
    """Save this start URL's session (its client with the token it issued); other start
    URLs' sessions are untouched."""
    _save_sso_session(start_url, {"client": client, "start_url": start_url, "region": region,
                                  "token": {"access": tok["accessToken"],
                                            "refresh": tok.get("refreshToken"),
                                            "expires_at": int(time.time()) + int(tok.get("expiresIn") or 3600)}})


def sso_poll(flow_id: str) -> dict:
    """``{"status": "pending" | "done", ...}``; ``done`` lists the accounts and roles."""
    with _flows_lock:
        flow = _flows.get(flow_id or "")
    if flow is None or flow["expires_at"] < time.time():
        raise SigninError("expired", "this sign-in has expired: start again")
    oidc = _unsigned("sso-oidc", flow["region"])
    try:
        tok = oidc.create_token(clientId=flow["client"]["id"], clientSecret=flow["client"]["secret"],
                                grantType=_DEVICE_GRANT, deviceCode=flow["device_code"])
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code in ("AuthorizationPendingException", "SlowDownException"):
            return {"status": "pending"}
        with _flows_lock:
            _flows.pop(flow_id, None)
        if code == "AccessDeniedException":
            raise SigninError("denied", "the sign-in was not allowed in the browser")
        if code == "ExpiredTokenException":
            raise SigninError("expired", "the code expired: start again")
        raise SigninError("sso_failed", f"Identity Center did not give a token ({code})")
    with _flows_lock:
        _flows.pop(flow_id, None)
    _store_token(flow["start_url"], flow["region"], tok, flow["client"])
    return {"status": "done", "accounts": sso_accounts(flow["start_url"])}


def _access_token(start_url: Optional[str]) -> "tuple[str, dict]":
    """A valid access token for this start URL, refreshed when it is about to expire."""
    stored = _sso_session(start_url)
    tok = stored.get("token") or {}
    if not tok.get("access"):
        raise _aws().NeedsLogin("sign in to AWS SSO in SwarmAI")
    if tok.get("expires_at", 0) - time.time() > 300:
        return tok["access"], stored
    client = stored.get("client") or {}
    if not tok.get("refresh") or not client.get("id"):
        raise _aws().NeedsLogin("the AWS SSO sign-in has expired: sign in again")
    oidc = _unsigned("sso-oidc", stored["region"])
    try:
        new = oidc.create_token(clientId=client["id"], clientSecret=client["secret"],
                                grantType="refresh_token", refreshToken=tok["refresh"])
    except Exception as exc:  # noqa: BLE001
        raise _aws().NeedsLogin(f"the AWS SSO sign-in could not be renewed ({_code(exc)}): sign in again")
    if not new.get("refreshToken"):
        new["refreshToken"] = tok["refresh"]
    _store_token(stored["start_url"], stored["region"], new, client)
    stored = _sso_session(start_url)
    return stored["token"]["access"], stored


def sso_accounts(start_url: str) -> list:
    """Accounts and roles the person signed in to *start_url* can use (Admin-like first)."""
    token, stored = _access_token(start_url)
    sso = _unsigned("sso", stored["region"])
    out = []
    for page in sso.get_paginator("list_accounts").paginate(accessToken=token):
        for acct in page.get("accountList", []):
            roles = []
            for rp in sso.get_paginator("list_account_roles").paginate(
                    accessToken=token, accountId=acct["accountId"]):
                roles += [r["roleName"] for r in rp.get("roleList", [])]
            roles.sort(key=lambda r: (0 if "admin" in r.lower() else 1, r))
            out.append({"account_id": acct["accountId"], "name": acct.get("accountName"),
                        "roles": roles})
    return out


def sso_creds(rec: dict):
    """The record's resolver for ``sso``: GetRoleCredentials for its account and role."""
    aws = _aws()
    sso_rec = rec.get("sso") or {}
    account, role = str(rec.get("account_id") or ""), str(sso_rec.get("role") or "")
    if not _ACCOUNT_RE.fullmatch(account) or not _ROLE_RE.fullmatch(role):
        raise aws.AccountError("bad_record", "the record's SSO account or role is not valid")
    token, stored = _access_token(sso_rec.get("start_url"))
    sso = _unsigned("sso", stored["region"])
    try:
        rc = sso.get_role_credentials(roleName=role, accountId=account, accessToken=token)["roleCredentials"]
    except Exception as exc:  # noqa: BLE001
        code = _code(exc)
        if code in ("UnauthorizedException", "ExpiredTokenException"):
            raise aws.NeedsLogin("the AWS SSO sign-in has expired: sign in again")
        raise aws.AccountError("sso_failed", f"AWS SSO did not give credentials ({code})")
    return aws.Creds(rc["accessKeyId"], rc["secretAccessKey"], rc.get("sessionToken"),
                     datetime.fromtimestamp(rc["expiration"] / 1000, tz=timezone.utc))


def prune(records: list) -> None:
    """Keep only the sign-ins *records* use (the current record and the records of Hives
    kept from previous accounts): the rest are sign-ins for accounts that were not
    connected, or no longer are. Called after a connect commits."""
    recs = [r for r in records if isinstance(r, dict)]
    urls = {(r.get("sso") or {}).get("start_url") for r in recs if r.get("source") == "sso"}
    key_ids = {r.get("keys_id") for r in recs if r.get("source") == "keys"}
    profiles = {(r.get("login") or {}).get("profile") or LOGIN_PROFILE
                for r in recs if r.get("source") == "login"}
    with _store_lock:
        store = _sso_store()
        if set(store["sessions"]) - urls or set(store["pending_clients"]) - urls:
            store["sessions"] = {u: v for u, v in store["sessions"].items() if u in urls}
            store["pending_clients"] = {u: v for u, v in store["pending_clients"].items() if u in urls}
            _write_secret(SSO_SECRET, store if store["sessions"] or store["pending_clients"] else None)
        keys = _keys_store()
        if set(keys["keys"]) - key_ids:
            keys["keys"] = {k: v for k, v in keys["keys"].items() if k in key_ids}
            _write_secret(KEYS_SECRET, keys if keys["keys"] else None)
    _prune_login_profiles(profiles)


# ── AWS login (through the AWS CLI) ────────────────────────────────────────────

def find_aws_cli() -> Optional[str]:
    exe = shutil.which("aws")
    if exe:
        return exe
    for cand in ("/usr/local/bin/aws", "/opt/homebrew/bin/aws", "/usr/bin/aws"):
        if Path(cand).is_file():
            return cand
    return None


def aws_cli_version(cli: str) -> Optional[tuple]:
    try:
        out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"aws-cli/(\d+)\.(\d+)\.(\d+)", (out.stdout or "") + (out.stderr or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def login_support() -> dict:
    """Whether AWS login can be offered here, and why not."""
    cli = find_aws_cli()
    if not cli:
        return {"available": False, "reason": "no_cli", "version": None}
    version = aws_cli_version(cli)
    if not version or version < MIN_CLI:
        return {"available": False, "reason": "old_cli",
                "version": ".".join(map(str, version)) if version else None}
    return {"available": True, "reason": None, "version": ".".join(map(str, version))}


def _login_config() -> Path:
    return _aws().aws_dir() / "login-config"


def login_profiles() -> set:
    """The AWS login profiles in SwarmAI's own login config."""
    conf = _login_config()
    if not conf.exists():
        return set()
    return {m.group(1) for m in re.finditer(r"^\[profile (swarmai-login(?:-[0-9a-f]{8})?)\]\s*$",
                                            conf.read_text(encoding="utf-8"), re.M)}


def _prune_login_profiles(keep: set) -> None:
    """Drop the login profiles no record uses (the CLI's own lines in the kept ones stay),
    except the one a sign-in is running or just finished in (not connected yet)."""
    conf = _login_config()
    with _login_lock:
        if _login_proc.get("profile"):
            keep = keep | {_login_proc["profile"]}
    import configparser
    import io
    with _store_lock:   # the same lock _login_env appends a profile under
        if not conf.exists():
            return
        cp = configparser.RawConfigParser()
        cp.read(conf, encoding="utf-8")
        drop = [s for s in cp.sections() if s.startswith("profile ") and s[len("profile "):] not in keep]
        if not drop:
            return
        for sec in drop:
            cp.remove_section(sec)
        buf = io.StringIO()
        cp.write(buf)
        _aws()._write_private(conf, buf.getvalue())


def _login_env(region: str, profile: str = LOGIN_PROFILE) -> dict:
    aws = _aws()
    if not LOGIN_PROFILE_RE.fullmatch(profile or ""):
        raise SigninError("bad_input", "not a SwarmAI login profile")
    root = aws.aws_dir()
    cache = root / "login-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(cache, 0o700)
    conf = _login_config()
    with _store_lock:
        text = conf.read_text(encoding="utf-8") if conf.exists() else ""
        if f"[profile {profile}]" not in text:
            aws._write_private(conf, (text.rstrip("\n") + "\n\n" if text.strip() else "") +
                               f"[profile {profile}]\nregion = {check_region(region)}\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AWS_") and not k.startswith(("HTTPS_PROXY", "https_proxy"))}
    env.update(AWS_CONFIG_FILE=str(conf), AWS_LOGIN_CACHE_DIRECTORY=str(cache),
               AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_PAGER="")
    return env


_login_proc: dict = {}
_login_lock = threading.Lock()


def login_start(region: str) -> dict:
    """Run ``aws login`` (it opens the browser itself) into a NEW SwarmAI login profile,
    so the profile the current account uses is untouched until a connect commits. One at
    a time."""
    support = login_support()
    if not support["available"]:
        raise SigninError(support["reason"], "AWS login needs AWS CLI 2.32 or later")
    with _login_lock:
        proc = _login_proc.get("proc")
        if proc is not None and proc.poll() is None:
            return {"status": "running", "profile": _login_proc.get("profile")}
        profile = f"{LOGIN_PROFILE}-{secrets.token_hex(4)}"
        proc = subprocess.Popen(
            [find_aws_cli(), "login", "--profile", profile, "--region", check_region(region)],
            env=_login_env(region, profile), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
        _login_proc.clear()
        _login_proc.update(proc=proc, started=time.time(), region=region, profile=profile)
    return {"status": "running", "profile": profile}


def login_status() -> dict:
    """The last sign-in's state. A finished result stays until the next ``login_start``
    (reading it does not consume it: the page and the agent may both ask)."""
    with _login_lock:
        if "result" in _login_proc:
            return dict(_login_proc["result"])
        proc = _login_proc.get("proc")
        if proc is None:
            return {"status": "idle"}
        if proc.poll() is None:
            if time.time() - _login_proc["started"] > _LOGIN_HANG_GUARD_S:
                proc.kill()
                _login_proc["result"] = {"status": "failed", "detail": "the sign-in did not finish"}
                return dict(_login_proc["result"])
            return {"status": "running", "profile": _login_proc.get("profile")}
        _out, err = proc.communicate()
        if proc.returncode == 0:
            result = {"status": "done", "profile": _login_proc.get("profile")}
        else:
            last = (err or "").strip().splitlines()
            result = {"status": "failed",
                      "detail": _aws()._redact(last[-1] if last else f"exit {proc.returncode}")}
        _login_proc["result"] = result
        return dict(result)


def login_creds(rec: dict):
    """The record's resolver for ``login``: the CLI exports (and renews) the session."""
    aws = _aws()
    cli = find_aws_cli()
    if not cli:
        raise aws.AccountError("cli_not_found", "the AWS CLI is no longer installed")
    profile = (rec.get("login") or {}).get("profile") or LOGIN_PROFILE
    if not LOGIN_PROFILE_RE.fullmatch(profile):
        raise aws.AccountError("bad_record", "the record's AWS login profile is not valid")
    try:
        res = subprocess.run(
            [cli, "configure", "export-credentials", "--profile", profile, "--format", "process"],
            env=_login_env(rec.get("region") or "us-east-1", profile), capture_output=True, text=True,
            timeout=_EXPORT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise aws.AccountError("login_timeout", "the AWS CLI did not answer")
    if res.returncode != 0:
        err = (res.stderr or "").lower()
        if any(w in err for w in ("login", "expired", "sign in", "token")):
            raise aws.NeedsLogin("the AWS login session has ended: sign in again")
        raise aws.AccountError("login_failed", aws._redact((res.stderr or "").strip()[-300:]))
    try:
        data = json.loads(res.stdout)
        return aws.Creds(data["AccessKeyId"], data["SecretAccessKey"], data.get("SessionToken"),
                         aws._parse_time(data.get("Expiration")))
    except (ValueError, KeyError, TypeError):
        raise aws.AccountError("login_failed", "the AWS CLI printed something that is not credentials")


# ── access keys ────────────────────────────────────────────────────────────────

def store_keys(access_key_id: str, secret_access_key: str) -> str:
    """Keep a key pair next to any other (the current account's keys stay until a
    connect commits these). Returns the key ID the connect candidate names."""
    access_key_id, secret_access_key = (access_key_id or "").strip(), (secret_access_key or "").strip()
    if not _KEY_ID_RE.fullmatch(access_key_id) or not _SECRET_RE.fullmatch(secret_access_key):
        raise SigninError("bad_input", "that does not look like an access key ID and secret")
    if access_key_id.startswith("ASIA"):
        raise SigninError("temporary_keys", "temporary keys expire within hours and cannot be renewed: "
                                            "use ADA, SSO or AWS login instead")
    with _store_lock:
        store = _keys_store()
        store["keys"][access_key_id] = secret_access_key
        _write_secret(KEYS_SECRET, store)
    return access_key_id


def keys_creds(rec: dict):
    aws = _aws()
    key_id = rec.get("keys_id")
    secret = _keys_store()["keys"].get(key_id or "")
    if not key_id or not secret:
        raise aws.AccountError("no_credentials", "the access keys are no longer stored")
    return aws.Creds(key_id, secret, None, None)


def has_keys(key_id: Optional[str]) -> bool:
    return bool(key_id) and key_id in _keys_store()["keys"]
