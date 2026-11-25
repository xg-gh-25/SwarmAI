"""An SSH machine's own AWS identity through IAM Roles Anywhere (hive/ssh_identity,
hive/ssh_provisioner; design docs/design/2026-11-24-ssh-machine-roles-anywhere.md §8, the final
test set; its item 10, the permission template, is in tests/test_aws_permissions.py and item 11
is the real-machine check).

The machine is the recorder of tests/test_ssh_provisioner (its keys made as the machine makes
them), AWS the recorder of tests/_ssh_machine_store.FakeAWS; the machine's own scripts run with
real bash and a docker that runs the image's Python here. Questions:
1. is the Host's CA made once and reused, private on disk, and given a successor with its own
   trust anchor when less than six months are left; is an anchor never changed or turned on
   again (one holding another CA, or turned off, is refused); is the CA made once when several
   renewals ask at the same moment?
2. does a signed certificate carry this install's CN, CA:false, digitalSignature, 30 days and a
   notBefore five minutes back, verify against the CA, and ignore what the request asks for?
3. does the role trust only Roles Anywhere, for this anchor and this CN?
4. does an install need no AWS profile on the machine, never see the machine's key, write a
   profile of the helper with absolute paths and no source_profile, and record the identity?
5. does a helper that does not match its checksum stop the install before the image, and is an
   image built before the helper (same version) never reused?
6. does check_once renew a certificate with nine days left (a new key), leave one with eleven
   alone, also on a development build with no machine behind; does a renewal after the Host's
   CA changed move the role and the profile to the new anchor; is a renewal whose profile does
   not resolve not recorded (tried again)?
7. does an update move a machine installed on the person's profile to its own identity, and
   when the new container's identity is wrong, start the old one again with its old identity;
   does a step that fails once the new container is kept leave the identity recorded and the
   end of the move to the next round?
8. is a machine removed and added again under its name a new CN its old certificate cannot use?
9. does a removal delete the Roles Anywhere profile in the region recorded, the role and the
   identity on the machine, and keep the trust anchor?
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from hive import ssh_identity as si
from hive import ssh_provisioner as sp
from tests import _ssh_machine_store as store
from tests.test_ssh_provisioner import VERSION, Machine, _install, world  # noqa: F401 — the fixture

ACCT = store.ACCOUNT
#: the provisioner's own helper step (the world fixture replaces it with a stub)
REAL_HELPER = sp._helper


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("config.get_app_data_dir", lambda: tmp_path / "app")
    return tmp_path / "app"


def _mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def _cfg(db, mid) -> dict:
    with sqlite3.connect(db) as c:
        return json.loads(c.execute("SELECT carrier_config FROM hive_instances WHERE id = ?", (mid,)).fetchone()[0])


def _cert(pem: str):
    from cryptography import x509
    return x509.load_pem_x509_certificate(pem.encode())


def _cn(cert) -> str:
    from cryptography.x509.oid import NameOID
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def _trusts(trust: dict, anchor_arn: str, cn: str) -> bool:
    """Whether *trust* lets Roles Anywhere assume the role for a certificate of *anchor_arn*
    whose CN is *cn* (the two conditions the role is given, evaluated as IAM does)."""
    for st in trust["Statement"]:
        if st.get("Principal") != {"Service": "rolesanywhere.amazonaws.com"}:
            continue
        cond = st.get("Condition") or {}
        if cond.get("ArnEquals", {}).get("aws:SourceArn") == anchor_arn and \
                cond.get("StringEquals", {}).get("aws:PrincipalTag/x509Subject/CN") == cn:
            return True
    return False


# 1 ------------------------------------------------------------------------------------------

def test_ca_created_once_and_rotated(app):
    aws = store.FakeAWS()
    ca = si.ensure_ca(ACCT, "us-west-2")
    d = app / "state" / "hive-ca" / f"{ACCT}-us-west-2"
    assert _mode(app / "state" / "hive-ca") == 0o700 and _mode(d) == 0o700 and _mode(d / "ca.key") == 0o600
    again = si.ensure_ca(ACCT, "us-west-2")
    assert again.cert.serial_number == ca.cert.serial_number
    life = ca.cert.not_valid_after_utc - ca.cert.not_valid_before_utc
    assert dt.timedelta(days=729) < life <= dt.timedelta(days=730, minutes=5)
    _, anchor = si.anchor_for(aws.session("us-west-2"), ACCT, "us-west-2")
    assert si.anchor_for(aws.session("us-west-2"), ACCT, "us-west-2")[1] == anchor
    assert [c[0] for c in aws.calls] == ["create_trust_anchor"]
    stored, = aws.anchors.values()
    assert stored["source"]["sourceType"] == "CERTIFICATE_BUNDLE" and stored["enabled"] is True
    assert stored["source"]["sourceData"]["x509CertificateData"].count("BEGIN CERTIFICATE") == 1
    assert stored["tags"] == [si.TAG]
    # less than six months left: a new CA, with an anchor of its own; the old anchor is not touched
    rotated = si.ensure_ca(ACCT, "us-west-2", now=ca.cert.not_valid_after_utc - dt.timedelta(days=100))
    assert rotated.cert.serial_number != ca.cert.serial_number
    new_anchor = si.ensure_trust_anchor(aws.session("us-west-2"), "us-west-2", rotated)
    assert new_anchor != anchor and len(aws.anchors) == 2
    assert stored["source"]["sourceData"]["x509CertificateData"] == si._pem(ca.cert).decode()
    assert not any(c[0].startswith("update") for c in aws.calls)


def test_an_anchor_is_never_changed_or_turned_on(app):
    aws = store.FakeAWS()
    sess = aws.session("us-east-1")
    ca, arn = si.anchor_for(sess, ACCT, "us-east-1")
    anchor, = aws.anchors.values()
    anchor["enabled"] = False                      # turned off to revoke SwarmAI's certificates
    with pytest.raises(si.IdentityError, match="turned off"):
        si.ensure_trust_anchor(sess, "us-east-1", ca)
    anchor["enabled"] = True
    other = si._new_ca(si._now())[1]               # an anchor of this name holding another CA
    anchor["source"]["sourceData"]["x509CertificateData"] = si._pem(other).decode()
    with pytest.raises(si.IdentityError, match="does not hold"):
        si.ensure_trust_anchor(sess, "us-east-1", ca)
    # a profile turned off is not turned on; one listing another role is made again
    role = f"arn:aws:iam::{ACCT}:role/SwarmAI-Hive-box"
    p1 = si.ensure_profile(sess, "us-east-1", "swarmai-hive-box", role)
    assert si.ensure_profile(sess, "us-east-1", "swarmai-hive-box", role) == p1
    aws.profiles[p1.rsplit("/", 1)[1]]["roleArns"] = [f"arn:aws:iam::{ACCT}:role/Other"]
    p2 = si.ensure_profile(sess, "us-east-1", "swarmai-hive-box", role)
    assert p2 != p1 and [p["roleArns"] for p in aws.profiles.values()] == [[role]]
    aws.profiles[p2.rsplit("/", 1)[1]]["enabled"] = False
    with pytest.raises(si.IdentityError, match="turned off"):
        si.ensure_profile(sess, "us-east-1", "swarmai-hive-box", role)


def test_the_ca_is_made_once_when_renewals_ask_together(app, monkeypatch):
    import threading
    made: list = []
    real = si._new_ca

    def slow(now):
        made.append(now)
        time.sleep(0.2)                            # two renewals inside the same moment
        return real(now)
    monkeypatch.setattr(si, "_new_ca", slow)
    aws = store.FakeAWS()
    got: list = []
    threads = [threading.Thread(target=lambda: got.append(si.anchor_for(aws.session("us-east-1"), ACCT, "us-east-1")))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1 and len(aws.anchors) == 1 and len({a for _, a in got}) == 1


# 2 ------------------------------------------------------------------------------------------

def test_signed_certificate(app):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    ca = si.ensure_ca(ACCT, "us-east-1")
    key = ec.generate_private_key(ec.SECP256R1())
    # a request asking for more than it is given: another name, and to be a CA itself
    req = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "swarmai-hive-other-00000000")]))
           .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
           .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    now = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
    cn = si.cn_for("dev-box", "3f2a9c1e-1111-4222-8333-444455556666")
    assert cn == "swarmai-hive-dev-box-3f2a9c1e"
    pem, not_after = si.sign_csr(ca, req, cn, now=now)
    cert = _cert(pem)
    assert _cn(cert) == cn
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.digital_signature and not usage.key_cert_sign
    assert cert.not_valid_before_utc == now - dt.timedelta(minutes=5)
    assert cert.not_valid_after_utc == now + dt.timedelta(days=30) and not_after == cert.not_valid_after_utc.timestamp()
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    cert.verify_directly_issued_by(ca.cert)
    # a request not signed by its key, or that is not one, is refused
    with pytest.raises(si.IdentityError):
        si.sign_csr(ca, "not a request", cn)


# 3 ------------------------------------------------------------------------------------------

def test_trust_policy():
    anchor = f"arn:aws:rolesanywhere:us-east-1:{ACCT}:trust-anchor/00000001-0000-4000-8000-000000000000"
    st, = si.trust_policy(anchor, "swarmai-hive-dev-box-3f2a9c1e")["Statement"]
    assert st["Principal"] == {"Service": "rolesanywhere.amazonaws.com"} and st["Effect"] == "Allow"
    assert sorted(st["Action"]) == ["sts:AssumeRole", "sts:SetSourceIdentity", "sts:TagSession"]
    assert st["Condition"] == {"ArnEquals": {"aws:SourceArn": anchor},
                               "StringEquals": {"aws:PrincipalTag/x509Subject/CN": "swarmai-hive-dev-box-3f2a9c1e"}}


# 4 ------------------------------------------------------------------------------------------

def test_install_without_any_aws_profile(world):  # noqa: F811
    m = Machine()
    mid = _install(world, m)
    assert store.status_of(world["db"], mid)[:2] == ("running", None)
    name = "dev-dsk-alice-1a-0123abcd"
    # the steps of design §4.3, and nothing asks the machine for an identity of its own
    assert m.steps() == ["preflight", "image", "identity key", "identity certificate", "profile",
                         "profile check", "start"]
    assert [c[0] for c in world["aws"].calls] == ["create_trust_anchor", "create_profile"]
    # the machine's key never reaches the Host: not in a script, not on stdin
    key, = m.keys
    for step, remote, data in m.calls:
        assert key not in remote and key.encode() not in data and b"PRIVATE KEY" not in data, step
    # the certificate it was sent is for its key, named by this install
    cert = _cert(m.certs[0])
    cfg = _cfg(world["db"], mid)
    assert _cn(cert) == f"swarmai-hive-{name}-{mid[:8]}" == cfg["cn"]
    # what was set up is recorded
    anchor, = world["aws"].anchors.values()
    profile, = world["aws"].profiles.values()
    assert cfg["identity"] == "rolesanywhere" and cfg["aws_profile"] == f"swarmai-hive-{name}"
    assert cfg["anchor_arn"] == anchor["trustAnchorArn"] and cfg["profile_arn"] == profile["profileArn"]
    assert cfg["identity_region"] == "us-east-1" and cfg["role_arn"] == f"arn:aws:iam::{ACCT}:role/SwarmAI-Hive-{name}"
    assert abs(cfg["not_after"] - cert.not_valid_after_utc.timestamp()) < 1 and "source_profile" not in cfg
    assert profile["roleArns"] == [cfg["role_arn"]] and profile["durationSeconds"] == 3600 and profile["enabled"]
    assert _trusts(world["aws"].trust[f"SwarmAI-Hive-{name}"], cfg["anchor_arn"], cfg["cn"])
    # the profile written on the machine: the helper with this certificate, nothing else
    prof = next(c for c in m.calls if c[0] == "profile")[1]
    assert cfg["anchor_arn"] in prof and cfg["profile_arn"] in prof and cfg["role_arn"] in prof


def _docker_python(tmp_path):
    """A docker whose ``run`` runs the image's Python command here (with its -e values)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text('#!/bin/bash\n[ "$1" = run ] || exit 0\nshift\n'
                    'while [ $# -gt 0 ]; do case "$1" in -e) export "$2"; shift 2;; -u|-v|--network) shift 2;;\n'
                    '  --rm) shift;; *) break;; esac; done\n'
                    'shift; shift\nexec "$HOST_PYTHON" "$@"\n')
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(home), HOST_PYTHON=sys.executable), home


def test_machine_scripts_keep_the_key_and_write_the_profile(tmp_path, app):
    """The machine's own scripts, with real bash: the key is made on the machine (0600), only
    the request is printed; the certificate is put in place with it; the profile is the
    helper's with absolute paths, replaces an earlier block of its id and leaves the rest."""
    env, home = _docker_python(tmp_path)
    out = subprocess.run(["bash", "-c", sp.KEYGEN_SH, "bash", "2.5.5-h1.8.6"], env=env, capture_output=True,
                         text=True, timeout=60, check=True).stdout
    idd = home / ".swarmai-hive" / "identity"
    assert "BEGIN CERTIFICATE REQUEST" in out and "PRIVATE KEY" not in out
    assert _mode(idd) == 0o700 and _mode(idd / "key.pem.new") == 0o600
    ca = si.ensure_ca(ACCT, "us-east-1")
    pem, _ = si.sign_csr(ca, sp._CSR_RE.search(out).group(0), "swarmai-hive-box-12345678")
    subprocess.run(["bash", "-c", sp.CERT_SH], env=env, input=pem, text=True, timeout=30, check=True)
    assert not (idd / "key.pem.new").exists() and _mode(idd / "key.pem") == 0o600
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key((idd / "key.pem").read_bytes(), password=None)
    assert _cert((idd / "cert.pem").read_text()).public_key().public_numbers() == key.public_key().public_numbers()
    # a certificate with no new key waiting is refused
    assert subprocess.run(["bash", "-c", sp.CERT_SH], env=env, input=pem, text=True, timeout=30).returncode != 0

    aws_dir = home / ".aws"
    aws_dir.mkdir()
    (aws_dir / "config").write_text("[profile mine]\nregion = eu-west-1\n")
    args = ["box", "arn:aws:rolesanywhere:us-east-1:1:trust-anchor/a", "arn:aws:rolesanywhere:us-east-1:1:profile/p",
            "arn:aws:iam::1:role/SwarmAI-Hive-box", "us-east-1"]
    for _ in range(2):                         # again: the block is replaced, not added twice
        subprocess.run(["bash", "-c", sp.PROFILE_SH, "bash", *args], env=env, timeout=30, check=True)
    text = (aws_dir / "config").read_text()
    assert text.startswith("[profile mine]\nregion = eu-west-1\n") and text.count("[profile swarmai-hive-box]") == 1
    line = next(ln for ln in text.splitlines() if ln.startswith("credential_process"))
    assert line == (f"credential_process = /opt/swarmai/bin/aws_signing_helper credential-process --certificate "
                    f"{idd}/cert.pem --private-key {idd}/key.pem --trust-anchor-arn {args[1]} --profile-arn {args[2]} "
                    f"--role-arn {args[3]}")
    assert "source_profile" not in text and "~" not in line
    subprocess.run(["bash", "-c", sp.UNPROFILE_SH, "bash", "box"], env=env, timeout=30, check=True)
    assert (aws_dir / "config").read_text() == "[profile mine]\nregion = eu-west-1\n\n"


def test_a_refused_permission_is_named(world, monkeypatch):  # noqa: F811
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}
    real = store.FakeAWS._ra

    def ra(self, region):
        client = real(self, region)

        def create_trust_anchor(**kw):
            raise Denied()
        client.create_trust_anchor = create_trust_anchor
        return client
    monkeypatch.setattr(store.FakeAWS, "_ra", ra)
    m = Machine()
    mid = _install(world, m)
    status, error, _, _ = store.status_of(world["db"], mid)
    assert status == "error" and "rolesanywhere:CreateTrustAnchor" in error and error.endswith("then Retry")
    assert "identity key" not in m.steps()


# 5 ------------------------------------------------------------------------------------------

def test_helper_checksum_mismatch(world, monkeypatch):  # noqa: F811
    fetched: list = []

    def fetch(url):
        fetched.append(url)
        return b"not the published helper"
    monkeypatch.setattr(si, "_download", fetch)
    monkeypatch.setattr(sp, "_helper", REAL_HELPER)
    m = Machine()
    mid = _install(world, m)
    status, error, _, _ = store.status_of(world["db"], mid)
    assert status == "error" and "does not match its published checksum" in error
    assert "image" not in m.steps()
    assert fetched == [f"https://rolesanywhere.amazonaws.com/releases/{si.HELPER_VERSION}/X86_64/Linux/Amzn2023/aws_signing_helper"]
    # the published one is taken (and kept); an architecture without one is refused
    import hashlib
    good = b"the published helper"
    monkeypatch.setitem(si.HELPER_SHA256, "aarch64", hashlib.sha256(good).hexdigest())
    assert si.helper_binary("arm64", fetch=lambda url: good) == good
    assert si.helper_binary("aarch64", fetch=lambda url: pytest.fail("fetched again")) == good
    with pytest.raises(si.IdentityError):
        si.helper_binary("ppc64le")
    # an image built before the helper was in it, at the same Hive version, has another tag
    assert sp._tag(VERSION) != VERSION and sp._tag(VERSION).endswith(f"-h{si.HELPER_VERSION}")
    assert '"swarmai-hive-ssh:$ver"' in sp.BUILD_SH


# 6 ------------------------------------------------------------------------------------------

def _set_cfg(db, mid, **keys) -> None:
    with sqlite3.connect(db) as c:
        cfg = json.loads(c.execute("SELECT carrier_config FROM hive_instances WHERE id = ?", (mid,)).fetchone()[0])
        cfg.update(keys)
        c.execute("UPDATE hive_instances SET carrier_config = ? WHERE id = ?", (json.dumps(cfg), mid))


def test_renewal_pass(world, monkeypatch):  # noqa: F811
    from core import hive_idle
    from core.carriers import registry
    m = Machine()
    mid = _install(world, m)
    world["monkeypatch"].setattr(sp, "_run", m.run)
    monkeypatch.setattr(hive_idle, "_host_version", lambda: None)     # a development build
    monkeypatch.setattr(registry, "with_duty", lambda duty: [])
    calls_before = len(m.calls)

    def look():
        async def go():
            await hive_idle.check_once()
            await asyncio.gather(*list(sp._TASKS))
        asyncio.run(go())

    _set_cfg(world["db"], mid, not_after=time.time() + 11 * 86400)
    look()
    assert len(m.calls) == calls_before                                 # eleven days left: not contacted
    _set_cfg(world["db"], mid, not_after=time.time() + 9 * 86400)
    look()
    assert m.steps()[calls_before:] == ["identity key", "identity certificate", "profile check"]
    assert len(m.keys) == 2 and m.keys[0] != m.keys[1]                  # a new key
    new = _cert(m.certs[-1])
    cfg = _cfg(world["db"], mid)
    assert abs(cfg["not_after"] - new.not_valid_after_utc.timestamp()) < 1 and cfg["renew_error"] is None
    assert _cn(new) == cfg["cn"]
    # one that fails says why on the card and is tried again next round
    _set_cfg(world["db"], mid, not_after=time.time() + 60)
    m.fail = "identity key"
    look()
    assert "identity key: it broke" in _cfg(world["db"], mid)["renew_error"]
    assert store.status_of(world["db"], mid)[0] == "running"
    # a certificate the profile does not resolve with is not recorded: the next round tries again
    m.fail = ""
    m.whoami = None
    world["monkeypatch"].setattr(sp, "_retry_identity", lambda host, tag, profile: _no_answer())
    look()
    cfg = _cfg(world["db"], mid)
    assert cfg["not_after"] < time.time() + 120 and "does not resolve" in cfg["renew_error"]


async def _no_answer() -> str:
    return ""


def test_renewal_after_the_hosts_ca_changed(world):  # noqa: F811
    """The Host's CA made again (lost, or rotated): the renewal signs with the new one, moves
    the role's trust and the machine's profile to its new anchor, and records it."""
    import shutil
    m = Machine()
    mid = _install(world, m)
    world["monkeypatch"].setattr(sp, "_run", m.run)
    before = _cfg(world["db"], mid)
    shutil.rmtree(world["app"] / "state" / "hive-ca" / f"{ACCT}-us-east-1")
    role = "SwarmAI-Hive-dev-dsk-alice-1a-0123abcd"
    # the machine cannot be reached once the trust has the new anchor: the old one still works
    m.fail = "identity key"
    with pytest.raises(sp.SshInstallError):
        asyncio.run(sp.renew(mid))
    new_anchor = next(a["trustAnchorArn"] for a in world["aws"].anchors.values()
                      if a["trustAnchorArn"] != before["anchor_arn"])
    assert _trusts(world["aws"].trust[role], before["anchor_arn"], before["cn"])
    assert _trusts(world["aws"].trust[role], new_anchor, before["cn"])
    assert _cfg(world["db"], mid)["anchor_arn"] == before["anchor_arn"]
    m.fail = ""
    asyncio.run(sp.renew(mid))
    after = _cfg(world["db"], mid)
    assert after["anchor_arn"] != before["anchor_arn"] and len(world["aws"].anchors) == 2
    trust = world["aws"].trust["SwarmAI-Hive-dev-dsk-alice-1a-0123abcd"]
    assert _trusts(trust, after["anchor_arn"], after["cn"]) and not _trusts(trust, before["anchor_arn"], after["cn"])
    # the certificate and the profile naming its anchor are written by one script on the machine
    assert m.steps()[-3:] == ["identity key", "identity certificate", "profile check"]
    cert_call = m.calls[-2][1]
    assert after["anchor_arn"] in cert_call and "cert.pem.new" in cert_call and "credential_process" in cert_call
    # a half-done renewal's statement for the old anchor is never kept by a later move
    trust = {"Statement": [si.roles_anywhere_statement(before["anchor_arn"], after["cn"]) | {"Sid": "RolesAnywherePrevious"},
                           {"Effect": "Allow", "Principal": {"AWS": ADMIN}, "Action": ["sts:AssumeRole"]}]}
    moved = sp._also_roles_anywhere(trust, after["anchor_arn"], after["cn"])
    assert not _trusts(moved, before["anchor_arn"], after["cn"]) and _trusts(moved, after["anchor_arn"], after["cn"])


# 7 ------------------------------------------------------------------------------------------

ADMIN = f"arn:aws:iam::{ACCT}:role/Admin"


def _old_machine(world, name="old-box") -> str:
    """A machine installed on the person's profile, before its own identity."""
    hid = "a1b2c3d4-0000-4000-8000-000000000001"
    store.add(world["db"], hid, name, carrier="ssh", status="running", version=VERSION,
              carrier_config={"ssh_host": f"{name}.example.com", "remote_port": 18321, "local_port": 28433,
                              "source_profile": "swarmai", "reaches_private": True, "general_tasks": True,
                              "mounts": [], "mounts_auto": False, "cert_renew_hint": ""})
    world["aws"].trust[f"SwarmAI-Hive-{name}"] = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"AWS": ADMIN}, "Action": ["sts:AssumeRole", "sts:TagSession"]}]}
    return hid


def _principals(trust) -> list:
    return [st["Principal"] for st in trust["Statement"]]


def test_migration_moves_identity(world):  # noqa: F811
    hid = _old_machine(world)
    m = Machine()
    world["monkeypatch"].setattr(sp, "_run", m.run)
    asyncio.run(sp.claim_update(hid))
    asyncio.run(sp.update(hid, "2.5.6"))
    assert store.status_of(world["db"], hid)[:3] == ("running", None, "2.5.6")
    assert m.steps()[2:] == ["identity key", "identity certificate", "profile", "profile check", "update",
                             "identity check", "update", "update profile"]
    # the new container uses the new profile; the old block goes once the new one is kept
    env = next(c for c in m.calls if c[0] == "update" and c[2])[2].decode()
    assert "AWS_PROFILE=swarmai-hive-old-box-ra\n" in env  # pragma: allowlist secret  (a profile name, not a credential)
    assert next(c for c in m.calls if c[0] == "profile")[1].split(" bash ")[1].startswith("old-box-ra ")
    assert m.calls[-1][1].endswith(" bash old-box")
    cfg = _cfg(world["db"], hid)
    assert cfg["identity"] == "rolesanywhere" and cfg["aws_profile"] == "swarmai-hive-old-box-ra"
    assert "source_profile" not in cfg
    trust = world["aws"].trust["SwarmAI-Hive-old-box"]
    assert _principals(trust) == [{"Service": "rolesanywhere.amazonaws.com"}]
    assert _trusts(trust, cfg["anchor_arn"], cfg["cn"])


def test_migration_rolls_back(world):  # noqa: F811
    hid = _old_machine(world)
    m = Machine()
    m.whoami = "arn:aws:sts::123456789012:assumed-role/Admin/alice-Isengard"     # the new container is wrong
    world["monkeypatch"].setattr(sp, "_run", m.run)
    asyncio.run(sp.claim_update(hid))
    asyncio.run(sp.update(hid, "2.5.6"))
    status, error, version, _ = store.status_of(world["db"], hid)
    assert (status, version) == ("running", VERSION) and "identity check" in error and "started again" in error
    assert m.steps()[-2:] == ["identity check", "rollback"] and "update profile" not in m.steps()
    # the old container still has what it used: its profile and the trust in it
    trust = world["aws"].trust["SwarmAI-Hive-old-box"]
    assert {"AWS": ADMIN} in _principals(trust)
    cfg = _cfg(world["db"], hid)
    assert cfg["source_profile"] == "swarmai" and cfg.get("identity") is None
    # what was made in AWS is recorded, so a removal takes it away
    assert cfg["profile_arn"] and cfg["identity_region"] == "us-east-1"


# 8, 9 ---------------------------------------------------------------------------------------

def test_remove_deletes_identity(world):  # noqa: F811
    m = Machine()
    mid = _install(world, m)
    with sqlite3.connect(world["db"]) as c:            # the account's region changed since the install
        c.execute("UPDATE hive_accounts SET default_region = 'eu-west-1' WHERE id = 'swarmai'")
    cfg = _cfg(world["db"], mid)
    out = asyncio.run(sp.remove(mid))
    assert out["deleted"] and out["left"] == []
    assert ("delete_profile", "us-east-1", cfg["profile_arn"].rsplit("/", 1)[1]) in world["aws"].calls
    assert world["aws"].profiles == {} and len(world["aws"].anchors) == 1        # the anchor stays
    assert ("delete", "dev-dsk-alice-1a-0123abcd") in world["roles"]
    assert 'rm -rf "$HOME/.swarmai-hive/identity"' in next(c for c in m.calls if c[0] == "remove")[1]


def test_readded_name_gets_new_cn(world):  # noqa: F811
    first = Machine()
    mid = _install(world, first)
    old = _cfg(world["db"], mid)
    old_cn = _cn(_cert(first.certs[0]))
    asyncio.run(sp.remove(mid))
    second = Machine()
    mid2 = _install(world, second)
    new = _cfg(world["db"], mid2)
    assert mid2 != mid and new["cn"] != old_cn and new["anchor_arn"] == old["anchor_arn"]
    trust = world["aws"].trust["SwarmAI-Hive-dev-dsk-alice-1a-0123abcd"]
    assert not _trusts(trust, new["anchor_arn"], old_cn) and _trusts(trust, new["anchor_arn"], new["cn"])


def test_a_failure_after_the_new_container_is_kept_never_stops_it(world, monkeypatch):  # noqa: F811
    """Once the new container is kept (the old one removed), a later step that fails, here
    narrowing the role's trust, leaves the new one running with its identity recorded (so it
    is renewed); the end of the move is finished by the next round."""
    from core import hive_idle
    from core.carriers import registry
    hid = _old_machine(world)
    m = Machine()
    world["monkeypatch"].setattr(sp, "_run", m.run)
    real = store.FakeAWS._iam
    broken = {"on": True}

    def iam(self):
        client = real(self)
        update = client.update_assume_role_policy

        def flaky(**kw):
            if broken["on"]:
                raise RuntimeError("IAM did not answer")
            return update(**kw)
        client.update_assume_role_policy = flaky
        return client
    monkeypatch.setattr(store.FakeAWS, "_iam", iam)
    asyncio.run(sp.claim_update(hid))
    asyncio.run(sp.update(hid, "2.5.6"))
    status, error, version, _ = store.status_of(world["db"], hid)
    assert (status, version) == ("running", "2.5.6") and "IAM did not answer" in error and "rollback" not in m.steps()
    cfg = _cfg(world["db"], hid)
    assert cfg["identity"] == "rolesanywhere" and cfg["move_pending"] is True and cfg["not_after"] > time.time()
    assert cfg["aws_profile"] == "swarmai-hive-old-box-ra"
    broken["on"] = False
    monkeypatch.setattr(hive_idle, "_host_version", lambda: None)
    monkeypatch.setattr(registry, "with_duty", lambda duty: [])

    async def look():
        await hive_idle.check_once()
        await asyncio.gather(*list(sp._TASKS))
    asyncio.run(look())
    cfg = _cfg(world["db"], hid)
    assert "move_pending" not in cfg and "source_profile" not in cfg and m.steps()[-1] == "update profile"
    assert _principals(world["aws"].trust["SwarmAI-Hive-old-box"]) == [{"Service": "rolesanywhere.amazonaws.com"}]


def test_values_for_the_machines_config_are_checked(world):  # noqa: F811
    """What a renewal writes into the machine's AWS config, and the profile name the container's
    environment gets, are of their fixed shapes, whatever the stored settings say."""
    from core.carriers.ssh import config
    m = Machine()
    mid = _install(world, m)
    world["monkeypatch"].setattr(sp, "_run", m.run)
    before = len(m.calls)
    _set_cfg(world["db"], mid, profile_arn="arn:aws:rolesanywhere:us-east-1:123456789012:profile/x\ncredential_process = id")
    with pytest.raises(sp.SshInstallError, match="unexpected value"):
        asyncio.run(sp.renew(mid))
    assert len(m.calls) == before
    with sqlite3.connect(world["db"]) as c:
        row = dict(zip(("name", "carrier_config"), c.execute(
            "SELECT name, carrier_config FROM hive_instances WHERE id = ?", (mid,)).fetchone()))
    cfg = json.loads(row["carrier_config"])
    for bad in ("swarmai-hive-x\nAWS_X=1", "default", "swarmai-hive-"):
        cfg["aws_profile"] = bad
        assert config({"carrier_config": json.dumps(cfg)})["aws_profile"] is None
