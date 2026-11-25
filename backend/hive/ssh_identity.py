"""An SSH machine's own AWS identity through IAM Roles Anywhere (design
docs/design/2026-11-24-ssh-machine-roles-anywhere.md §4).

The Host is a small certificate authority: one EC P-256 CA per Host, account and region, kept
in its state directory and registered as a Roles Anywhere trust anchor of the
``CERTIFICATE_BUNDLE`` kind (no AWS Private CA), named by the Host and the CA's fingerprint
(``anchor_name``). A trust anchor is never changed after it is made: a new CA (rotated, or the
Host's CA made again) gets a new anchor, and each machine's role moves to it at its next
renewal. So the SwarmAI account needs no permission to change an anchor, which would let it
swap the CA of an anchor someone else's roles trust. A disabled anchor or profile is taken
as revoked and never turned on again. Each machine makes its own key (it never leaves
the machine); the Host signs a 30-day certificate whose CN names the machine and its install
(``cn_for``); the machine's narrow role trusts only that CN on this Host's anchor
(``trust_policy``); each machine has its own Roles Anywhere profile; and the official
``aws_signing_helper`` (``helper_binary``, checked against the SHA256 written here) turns the
certificate into the role's temporary credentials on the machine, with the Host off.

This module holds no SSH: hive/ssh_provisioner runs the machine side. The AWS calls take a
boto3 session (the Host's SwarmAI account session) and are synchronous; callers run them in a
thread.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: the official credential helper (https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html):
#: the version and the SHA256 of each Linux build the page publishes (Linux builds are not code-signed)
HELPER_VERSION = "1.8.6"
HELPER_SHA256 = {
    "x86_64": "3553fdc515e5682541a9ebdd37031ec7f461169aa13ccbb6fbd5b7259644113d",  # pragma: allowlist secret  (a published checksum, not a credential)
    "aarch64": "b8a1800a9f483674a376170e1eabc0796a4ad3b7ac048670de9346975e9925dc",  # pragma: allowlist secret  (a published checksum, not a credential)
}
_HELPER_DIR = {"x86_64": "X86_64", "aarch64": "Aarch64"}
_ARCH_ALIASES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
HELPER_URL = "https://rolesanywhere.amazonaws.com/releases/{version}/{arch}/Linux/Amzn2023/aws_signing_helper"

CA_DAYS = 730
#: a CA with less than this left gets a successor (and its own trust anchor); a certificate
#: the old one signed stays valid with the old anchor until its machine's next renewal
CA_ROTATE_DAYS = 183
CERT_DAYS = 30
#: a machine certificate with less than this left is renewed (hive_idle.check_once)
RENEW_BEFORE_S = 10 * 86400
#: notBefore this far in the past: clocks of the Host and AWS may differ a little
SKEW_S = 300
SESSION_S = 3600
TAG = {"key": "swarmai:managed", "value": "true"}

_ACCOUNT_RE = re.compile(r"^\d{12}$")
_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_ARN_ID_RE = re.compile(r"/([0-9a-f-]{36})$")


class IdentityError(RuntimeError):
    """Setting up or renewing a machine's identity failed; the text says why."""


#: the Host's CA files and host id are read and written by several renewals at once (threads)
_LOCK = threading.RLock()


# -- the Host's state ----------------------------------------------------------------------

def _root() -> Path:
    from config import get_app_data_dir
    d = Path(get_app_data_dir()) / "state" / "hive-ca"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_private(path: Path, data: bytes) -> None:
    """*data* into *path* created 0600, replacing it whole (written beside, then renamed)."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def host_id() -> str:
    """This Host's id (8 hex), made once: it names the Host's trust anchors, so two Hosts of
    one account each keep their own."""
    with _LOCK:
        p = _root() / "host-id"
        try:
            got = p.read_text().strip()
            if re.fullmatch(r"[0-9a-f]{8}", got):
                return got
        except OSError:
            pass
        got = secrets.token_hex(4)
        _write_private(p, got.encode())
        return got


# -- the CA --------------------------------------------------------------------------------

@dataclass
class CA:
    key: Any
    cert: Any


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _ca_dir(account: str, region: str) -> Path:
    if not _ACCOUNT_RE.fullmatch(str(account)) or not _REGION_RE.fullmatch(str(region)):
        raise IdentityError("the SwarmAI account or its region has an unexpected value")
    d = _root() / f"{account}-{region}"
    d.mkdir(exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _new_ca(now: dt.datetime) -> tuple[Any, Any]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"SwarmAI Hive CA {host_id()} {now:%Y%m%d}")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(seconds=SKEW_S))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def _pem(cert: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.PEM)


def ensure_ca(account: str, region: str, now: Optional[dt.datetime] = None) -> CA:
    """The Host's CA for *account* and *region*: made the first time (directory 0700, key
    0600), reused after; with less than CA_ROTATE_DAYS left a new one replaces it (it gets its
    own trust anchor, ``anchor_name``)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    now = now or _now()
    with _LOCK:
        d = _ca_dir(account, region)
        key_p, cert_p = d / "ca.key", d / "ca.pem"
        if key_p.exists() and cert_p.exists():
            key = serialization.load_pem_private_key(key_p.read_bytes(), password=None)
            cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
            if cert.not_valid_after_utc - now >= dt.timedelta(days=CA_ROTATE_DAYS):
                return CA(key=key, cert=cert)
        key, cert = _new_ca(now)
        _write_private(key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
        _write_private(cert_p, _pem(cert))
        logger.info("ssh_identity: new Hive CA for %s/%s until %s", account, region, cert.not_valid_after_utc)
        return CA(key=key, cert=cert)


def fingerprint(ca: CA) -> str:
    from cryptography.hazmat.primitives import serialization
    return hashlib.sha256(ca.cert.public_bytes(serialization.Encoding.DER)).hexdigest()


# -- machine certificates ------------------------------------------------------------------

def cn_for(name: str, install_id: str) -> str:
    """The CN of one install of a machine: a machine removed and added again under its name
    gets a new one, so an old certificate is never trusted by the new role."""
    tail = re.sub(r"[^0-9a-f]", "", str(install_id).lower())[:8]
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", name) or len(tail) != 8:
        raise IdentityError("the machine's name or install id has an unexpected value")
    return f"swarmai-hive-{name}-{tail}"


def sign_csr(ca: CA, csr_pem: str, cn: str, now: Optional[dt.datetime] = None) -> tuple[str, float]:
    """A CERT_DAYS certificate for the public key of *csr_pem*, signed by *ca*: subject CN=*cn*,
    CA:false, keyUsage digitalSignature, notBefore SKEW_S in the past. Only the request's public
    key is used (its subject and extensions are ignored). (PEM, not_after as Unix seconds)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import NameOID
    now = now or _now()
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode() if isinstance(csr_pem, str) else csr_pem)
    except ValueError:
        raise IdentityError("identity: the machine's certificate request cannot be read") from None
    if not csr.is_signature_valid:
        raise IdentityError("identity: the machine's certificate request is not signed by its key")
    pub = csr.public_key()
    if not ((isinstance(pub, ec.EllipticCurvePublicKey) and pub.curve.name in ("secp256r1", "secp384r1"))
            or (isinstance(pub, rsa.RSAPublicKey) and pub.key_size >= 2048)):
        raise IdentityError("identity: the machine's key is not P-256, P-384 or RSA 2048+")
    not_after = now + dt.timedelta(days=CERT_DAYS)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(ca.cert.subject).public_key(pub).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(seconds=SKEW_S)).not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.key.public_key()), critical=False)
            .sign(ca.key, hashes.SHA256()))
    return _pem(cert).decode(), not_after.timestamp()


# -- AWS: the role's trust, the anchor, the profile ----------------------------------------

def trust_policy(anchor_arn: str, cn: str) -> dict[str, Any]:
    """Only Roles Anywhere, for a certificate of this Host's anchor whose CN is this install's."""
    return {"Version": "2012-10-17", "Statement": [roles_anywhere_statement(anchor_arn, cn)]}


def roles_anywhere_statement(anchor_arn: str, cn: str) -> dict[str, Any]:
    return {"Sid": "RolesAnywhere", "Effect": "Allow", "Principal": {"Service": "rolesanywhere.amazonaws.com"},
            "Action": ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"],
            "Condition": {"ArnEquals": {"aws:SourceArn": anchor_arn},
                          "StringEquals": {"aws:PrincipalTag/x509Subject/CN": cn}}}


def _client(session: Any, region: str) -> Any:
    return session.client("rolesanywhere", region_name=region)


_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}


def _aws(action: str, fn: Any, /, **kwargs: Any) -> Any:
    """*fn*(**kwargs); a refusal says which permission the SwarmAI account lacks (design §9)."""
    try:
        return fn(**kwargs)
    except Exception as e:  # noqa: BLE001 — a refusal is named, anything else raised as it is
        code = str(((getattr(e, "response", None) or {}).get("Error") or {}).get("Code") or "")
        if code in _DENIED:
            raise IdentityError(f"identity: the SwarmAI account may not {action} (needed for the machine's "
                                "own AWS identity); add it from the permission template in Settings, "
                                "then Retry") from None
        raise


def _all(call: Any, key: str) -> list:
    out: list = []
    token = None
    while True:
        page = call(**({"nextToken": token} if token else {}))
        out.extend(page.get(key) or [])
        token = page.get("nextToken")
        if not token:
            return out


def anchor_name(ca: CA) -> str:
    """This Host's trust anchor for *ca*: a new CA is a new anchor, so an anchor never changes."""
    return f"swarmai-hives-{host_id()}-{fingerprint(ca)[:12]}"


def _same_certs(pem: str, ca: CA) -> bool:
    """Whether *pem* (an anchor's bundle, as AWS gives it back) is exactly *ca*'s certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        got = x509.load_pem_x509_certificates(str(pem or "").encode())
    except ValueError:
        return False
    der = ca.cert.public_bytes(serialization.Encoding.DER)
    return [c.public_bytes(serialization.Encoding.DER) for c in got] == [der]


def ensure_trust_anchor(session: Any, region: str, ca: CA) -> str:
    """This Host's trust anchor for *ca* in *region* (created when there is none). Its ARN.
    IdentityError when the anchor of that name holds another CA, or was disabled: someone
    else made it, or turned it off to revoke SwarmAI's certificates; it is never changed or
    turned on again here."""
    ra = _client(session, region)
    name = anchor_name(ca)
    found = next((a for a in _aws("rolesanywhere:ListTrustAnchors", _all, call=ra.list_trust_anchors,
                                  key="trustAnchors") if a.get("name") == name), None)
    if found is None:
        source = {"sourceType": "CERTIFICATE_BUNDLE", "sourceData": {"x509CertificateData": _pem(ca.cert).decode()}}
        got = _aws("rolesanywhere:CreateTrustAnchor", ra.create_trust_anchor, name=name, source=source,
                   enabled=True, tags=[TAG])["trustAnchor"]
        logger.info("ssh_identity: trust anchor %s created in %s", name, region)
        return got["trustAnchorArn"]
    source = found.get("source") or {}
    if source.get("sourceType") != "CERTIFICATE_BUNDLE" or \
            not _same_certs((source.get("sourceData") or {}).get("x509CertificateData"), ca):
        raise IdentityError(f"identity: the trust anchor {name} in {region} does not hold this computer's CA; "
                            "delete it in the SwarmAI account (IAM Roles Anywhere), then Retry")
    if not found.get("enabled"):
        raise IdentityError(f"identity: the trust anchor {name} in {region} was turned off; turn it on in the "
                            "SwarmAI account (IAM Roles Anywhere) to let SwarmAI's machines use it, then Retry")
    return found["trustAnchorArn"]


def anchor_for(session: Any, account: str, region: str) -> tuple[CA, str]:
    """The Host's CA for *account* and *region* and its trust anchor, made when missing; one
    at a time (renewals of several machines run at once)."""
    with _LOCK:
        ca = ensure_ca(account, region)
        return ca, ensure_trust_anchor(session, region, ca)


def ensure_profile(session: Any, region: str, name: str, role_arn: str) -> str:
    """The machine's Roles Anywhere profile *name* listing only *role_arn* (created; one listing
    other roles is deleted and made again: a profile is never changed). Its ARN. IdentityError
    when it was disabled (turned off to revoke the machine; never turned on again here)."""
    ra = _client(session, region)
    found = next((p for p in _aws("rolesanywhere:ListProfiles", _all, call=ra.list_profiles, key="profiles")
                  if p.get("name") == name), None)
    if found is not None and not found.get("enabled"):
        raise IdentityError(f"identity: the Roles Anywhere profile {name} in {region} was turned off; turn it on "
                            "in the SwarmAI account, or remove the machine and add it again")
    if found is not None and list(found.get("roleArns") or []) == [role_arn]:
        return found["profileArn"]
    if found is not None:
        delete_profile(session, region, found["profileArn"])
    got = _aws("rolesanywhere:CreateProfile and iam:PassRole", ra.create_profile, name=name,
               roleArns=[role_arn], durationSeconds=SESSION_S, enabled=True, tags=[TAG])["profile"]
    return got["profileArn"]


def delete_profile(session: Any, region: str, profile_arn: str) -> None:
    """Delete the profile *profile_arn* in *region*; one already gone is fine."""
    m = _ARN_ID_RE.search(str(profile_arn or ""))
    if not m:
        return
    ra = _client(session, region)
    try:
        _aws("rolesanywhere:DeleteProfile", ra.delete_profile, profileId=m.group(1))
    except ra.exceptions.ResourceNotFoundException:
        pass


# -- the credential helper -----------------------------------------------------------------

def helper_arch(arch: str) -> str:
    got = _ARCH_ALIASES.get(str(arch).strip().lower())
    if got is None:
        raise IdentityError(f"image: no credential helper for the machine's architecture {str(arch)[:20]}")
    return got


def helper_binary(arch: str, fetch: Any = None) -> bytes:
    """The official aws_signing_helper HELPER_VERSION for *arch*, checked against HELPER_SHA256
    (kept in the Host's state directory once checked). IdentityError when it does not match."""
    a = helper_arch(arch)
    want = HELPER_SHA256[a]
    cache = _root() / "helper" / f"{HELPER_VERSION}-{a}"
    if cache.exists():
        data = cache.read_bytes()
        if hashlib.sha256(data).hexdigest() == want:
            return data
    url = HELPER_URL.format(version=HELPER_VERSION, arch=_HELPER_DIR[a])
    data = (fetch or _download)(url)
    if hashlib.sha256(data).hexdigest() != want:
        raise IdentityError(f"image: the credential helper {HELPER_VERSION} for {a} does not match its "
                            "published checksum; not installed")
    cache.parent.mkdir(exist_ok=True)
    _write_private(cache, data)
    return data


def _download(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.content
