"""Install, update and remove the Hive on an SSH machine (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.3-§5.4, decisions D2, D4, D7).

Everything runs from the Host over SSH; the person only gives the machine's SSH host: the
machine needs no AWS setup of its own (design docs/design/2026-11-24-ssh-machine-roles-anywhere.md).
A machine is a ``hive_instances`` row with
``carrier = 'ssh'`` (core/carriers/ssh), whose ``status`` goes ``provisioning → running``, or
``error`` with ``error_message`` saying which step failed and why. Every step can be run again
(``retry``): an image already built, a role or profile already there, is taken as it is.

Steps of ``add`` / ``retry`` (``_install``):

1. **preflight** (one ssh call, read-only): the machine answers in BatchMode, ``docker`` runs
   for this user, the home's disk has room, the Hive's port is free; its uid, gid, user, home,
   architecture, cores and memory are read for the image and the panel.
2. **image**: the Host takes the release package of its own version from the account's bucket,
   checks it against the ``checksums.txt`` beside it (as an EC2 Hive's first boot does), and
   sends it with the Dockerfile (hive/ssh_image) and the IAM Roles Anywhere credential helper
   (hive/ssh_identity.helper_binary, checked against its SHA256) to the machine, which builds
   ``swarmai-hive-ssh:<version>-h<helper version>`` (``_tag``). The package is root's inside
   the image: the Hive's own gate code cannot be changed by a task running as the same uid.
3. **identity** (hive/ssh_identity): the Host's CA and its trust anchor; the role (D4)
   ``SwarmAI-Hive-<name>`` with the EC2 Hive's policy and its ``HiveName`` tag (it reads
   release packages, writes only its own ``bridge/<name>/`` asks and closed marks, reads only
   its own answers, calls Bedrock), trusting only a certificate of that anchor with this
   install's CN; the machine's Roles Anywhere profile; then the machine makes a new key in
   ``~/.swarmai-hive/identity/`` (it never leaves the machine) and the Host signs its
   30-day certificate.
4. **profile**: one profile ``swarmai-hive-<name>`` in the machine's ``~/.aws/config`` (a
   ``credential_process`` running the helper with that certificate), between marker lines;
   nothing else in the file is changed. Checked inside the image: the ARN must be the Hive's
   role. What was set up is recorded in ``carrier_config`` (``identity``, ``anchor_arn``,
   ``role_arn``, ``profile_arn``, ``identity_region``, ``cn``, ``not_after``, ``aws_profile``).
5. **start**: the environment file (mode 600, sent on ssh's stdin, never on a command line)
   and the container (``--restart unless-stopped``, the machine's network, the person's
   ``$HOME`` and the configured mounts), then ``/health`` through the tunnel.

``update`` (D7) builds the new version's image, stops the old container, starts the new one and
waits for ``/health`` and the new container's identity; when the new one does not come up, the
old one is started again. A machine installed before its own identity (on a profile of the
person's) moves to it in its update, and back with the old container when that fails. While
it runs, ``updated_at`` is refreshed every minute, so ``release_stale_updates`` never takes it
for an EC2 update stopped in the middle. ``renew_due`` (hive_idle.check_once, every round)
gives a machine whose certificate has less than ten days left a new key and certificate.
``remove`` deletes the container, the images, the machine's identity and Roles Anywhere
profile, the role and the added profile, and the row; the Hive's data
(``~/.swarmai-hive/data``) stays on the machine.

Security: every value that reaches the machine's shell is checked against a strict pattern and
quoted (``shlex.quote``); the scripts themselves are fixed text. The Hive's password never
leaves the Host: the machine gets its bcrypt hash, inside the environment file, on stdin.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import secrets
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any, Optional

from core.carriers import ssh_tunnel
from hive import ssh_identity

logger = logging.getLogger(__name__)

CARRIER = "ssh"
#: the container's name on the machine (one Hive per machine)
CONTAINER = "swarmai-hive"
IMAGE = "swarmai-hive-ssh"
#: where the Hive keeps its files on the machine
HIVE_DIR = "~/.swarmai-hive"
#: local ports the Host's tunnels are given (core/carriers/ssh_tunnel), one per machine
LOCAL_PORTS = range(28400, 29000)
#: hang guards, per step (a step that never finishes; a slow one is normal)
SSH_TIMEOUT_S = 60
BUILD_TIMEOUT_S = 1800
HEALTH_TIMEOUT_S = 120
HEARTBEAT_S = 60

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
MOUNT_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,200}(:ro)?$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_ARN_RE = re.compile(r"^arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[A-Za-z0-9+=,.@_/:-]{1,200}$")
_HINT_RE = re.compile(r"^[^\n\r`$\\]{0,120}$")


class SshInstallError(RuntimeError):
    """One step failed; the message names the step and is safe to show."""


# -- the store ---------------------------------------------------------------------------

def _db_path() -> str:
    from database import db
    return str(db.db_path)


async def _row(machine_id: str) -> dict[str, Any]:
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("SELECT * FROM hive_instances WHERE id = ? AND carrier = ?", (machine_id, CARRIER))
        got = await cur.fetchone()
    if got is None:
        raise SshInstallError("no such SSH machine")
    return dict(got)


async def _set(machine_id: str, **fields: Any) -> None:
    import aiosqlite
    allowed = {"status", "error_message", "version", "instance_type", "iam_role_name", "carrier_config",
               "s3_bucket", "auth_user", "auth_password"}
    assert set(fields) <= allowed, set(fields) - allowed
    sets = ", ".join(f"{k} = ?" for k in fields)
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        # updated_at in SQLite's own text form: release_stale_updates compares it with
        # datetime('now', ...) as text, and an ISO value ('T') would never look old
        await conn.execute(f"UPDATE hive_instances SET {sets}, updated_at = datetime('now') "
                           "WHERE id = ? AND carrier = ?", (*fields.values(), machine_id, CARRIER))
        await conn.commit()


async def _merge_config(machine_id: str, _drop: tuple = (), **keys: Any) -> dict[str, Any]:
    """Set *keys* (and remove *_drop*) in the machine's ``carrier_config``, reading it under
    the write lock: the install, an update and the card's switches each change only their own
    keys, so a switch turned while an image builds is never written back over. The merged
    settings."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute("SELECT carrier_config FROM hive_instances WHERE id = ? AND carrier = ?",
                                 (machine_id, CARRIER))
        got = await cur.fetchone()
        if got is None:
            await conn.rollback()
            raise SshInstallError("no such SSH machine")
        raw = json.loads(got[0] or "{}")
        raw.update(keys)
        for k in _drop:
            raw.pop(k, None)
        await conn.execute("UPDATE hive_instances SET carrier_config = ?, updated_at = datetime('now') "
                           "WHERE id = ? AND carrier = ?", (json.dumps(raw), machine_id, CARRIER))
        await conn.commit()
    return raw


def _iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def claim_update(machine_id: str) -> None:
    """running → updating, atomically; RuntimeError when the machine is not running."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "UPDATE hive_instances SET status = 'updating', updated_at = datetime('now') "
            "WHERE id = ? AND carrier = ? AND status = 'running'", (machine_id, CARRIER))
        await conn.commit()
    if cur.rowcount == 0:
        raise RuntimeError("the SSH machine is not running")


async def release_update_claim(machine_id: str) -> None:
    """Give back an update claim nothing ran under (updating → running)."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        await conn.execute("UPDATE hive_instances SET status = 'running', updated_at = datetime('now') "
                           "WHERE id = ? AND carrier = ? AND status = 'updating'", (machine_id, CARRIER))
        await conn.commit()


# -- settings ------------------------------------------------------------------------------

def check_settings(ssh_host: Any, mounts: Any = None, cert_renew_hint: Any = "", remote_port: Any = 18321) -> None:
    """ValueError for anything that may not reach the machine's shell or its config file.
    *mounts* None means the install finds them on the machine."""
    if not ssh_tunnel.valid_host(ssh_host):
        raise ValueError("not an SSH host (letters, digits, dots and hyphens, optionally user@)")
    if mounts is not None and (not isinstance(mounts, (list, tuple)) or not all(_mount_ok(m) for m in mounts)):
        raise ValueError("each mount is an absolute path, optionally ending in :ro "
                         "(not the root, not a docker socket)")
    if not isinstance(cert_renew_hint, str) or not _HINT_RE.fullmatch(cert_renew_hint):
        raise ValueError("the certificate hint is one short line of plain text")
    if not ssh_tunnel.valid_port(remote_port):
        raise ValueError("the machine's port must be between 1024 and 65535")


#: the directories under / that belong to the system itself, never mounted from the machine:
#: the container has its own, and a machine's own toolchains and workspaces live elsewhere
STANDARD_TOP_DIRS = frozenset({"bin", "boot", "dev", "etc", "home", "lib", "lib32", "lib64", "libx32",
                               "lost+found", "media", "mnt", "opt", "proc", "root", "run", "sbin", "snap",
                               "srv", "sys", "tmp", "usr", "var"})

def _found_mounts(facts: dict[str, str], home: str) -> list[str]:
    """The machine's own top-level directories to mount (its preflight's ``topdirs``,
    ``path:rw:real`` or ``path:ro:real``, *real* where a link points): directly under /, not a
    system directory, and neither is where it really is (a link to /var/run or / is not
    mounted, docker would follow it); not a system directory
    (``STANDARD_TOP_DIRS``), not the home or a directory above the home's path or real path
    (one above it would show other users' homes), and passing ``_mount_ok``. Read-write
    when the user can write it or a folder directly in it (``/workplace/<user>``), else
    read-only. Holds on any Linux machine: it names no
    directory of one company or machine."""
    import posixpath
    homes = {h.rstrip("/") for h in (home, facts.get("home_real") or home) if h}
    out: set[str] = set()
    for item in (facts.get("topdirs") or "").split(","):
        path, _, rest = item.strip().partition(":")
        mode, _, real = rest.partition(":")
        real = real or path
        if mode not in ("rw", "ro") or posixpath.dirname(path) != "/" \
                or posixpath.basename(path) in STANDARD_TOP_DIRS:
            continue
        top = real.lstrip("/").split("/", 1)[0]
        if not real.startswith("/") or not top or top in STANDARD_TOP_DIRS:
            continue                           # the root, or a link into a system directory
        if any(h == p or h.startswith(p + "/") for h in homes for p in (path, real)):
            continue
        mount = path if mode == "rw" else f"{path}:ro"
        if _mount_ok(mount) and _mount_ok(real):
            out.add(mount)
    return sorted(out)


#: what no mount may show inside the container: the docker socket (root on the machine) and
#: /tmp, where the start script keeps the environment file for the moment docker reads it
_NEVER_MOUNTED = ("/var/run/docker.sock", "/run/docker.sock", "/tmp")


def _mount_ok(m: Any) -> bool:
    """A mount the machine's start script may split on spaces and pass to ``docker run -v``:
    an absolute path in its normal form (no '.', '..', '//' or trailing '/'), optionally
    ':ro', never the root, a docker socket or a folder above one (each gives a run root on the
    machine), and never /tmp (``_NEVER_MOUNTED``)."""
    import posixpath
    if not isinstance(m, str) or not MOUNT_RE.fullmatch(m):
        return False
    path = m.removesuffix(":ro")
    if posixpath.normpath(path) != path or path == "/" or path.startswith("//") \
            or posixpath.basename(path).startswith("docker.sock"):
        return False
    # the path itself or a folder above it: a mount of /var shows /var/run/docker.sock too
    return not any(p == path or p.startswith(path + "/") for p in _NEVER_MOUNTED)


def default_name(ssh_host: str) -> str:
    """The first label of the host, lower-cased, as a Hive name (``dev-dsk-alice-1a``)."""
    label = ssh_host.split("@")[-1].split(".")[0].lower()
    label = re.sub(r"[^a-z0-9-]", "-", label).strip("-")[:40] or "ssh-machine"
    return label if label[0].isalpha() else f"m-{label}"[:40]


async def _free_local_port() -> int:
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("SELECT carrier_config FROM hive_instances WHERE carrier = ?", (CARRIER,))
        taken = set()
        for (raw,) in await cur.fetchall():
            try:
                taken.add(int(json.loads(raw or "{}").get("local_port")))
            except (TypeError, ValueError):
                pass
    for port in LOCAL_PORTS:
        if port not in taken and not await asyncio.to_thread(ssh_tunnel._port_answers, port, 0.2):
            return port
    raise SshInstallError("no free local port for the tunnel")


# -- running things on the machine ------------------------------------------------------------

def _ssh_cmd(host: str, remote: str) -> list[str]:
    if not ssh_tunnel.valid_host(host):
        raise SshInstallError("not an SSH host")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "--", host, remote]


async def _run(host: str, remote: str, data: Optional[bytes] = None, timeout: float = SSH_TIMEOUT_S,
               step: str = "", tries: int = ssh_tunnel.CONNECT_TRIES) -> str:
    """Run *remote* (a fixed command line, every value in it quoted) on the machine with
    *data* on stdin; its stdout. A connection that failed while it was being set up is tried
    again, up to *tries* attempts: ssh's own connection-phase message
    (``ssh_tunnel.connect_failed``) and no BEGUN marker on stdout, which the machine prints
    before the command, so a command whose marker arrived is never run twice whatever ssh
    printed after (the marker can still be lost on a link that stalls just after the command
    began, so a step sent here must be safe to run twice: SWAP_SH's attempt label).
    SshInstallError with the last line it printed on failure (the command failed, ssh refused
    the key or the host, or the last attempt could not connect), or when it ran longer than
    *timeout* (it is then killed, and not run again)."""
    for attempt in range(1, max(1, tries) + 1):
        proc = await asyncio.create_subprocess_exec(*_ssh_cmd(host, f"printf '{BEGUN}\\n'; {remote}"),
                                                    stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(data or b""), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise SshInstallError(f"{step}: did not finish within {timeout:.0f}s") from None
        # the marker's line, wherever it is (a login script may print before it), is taken off
        mark = out.find(BEGUN.encode() + b"\n")
        began = mark >= 0
        if began:
            out = out[:mark] + out[mark + len(BEGUN) + 1:]
        if proc.returncode == 0:
            return out.decode(errors="replace")
        text = (err or out).decode(errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()
                 and "post-quantum" not in ln and "store now, decrypt later" not in ln and "pq.html" not in ln]
        why = lines[-1].strip()[:300] if lines else f"exit code {proc.returncode}"
        if attempt < tries and not began \
                and ssh_tunnel.connect_failed(proc.returncode, (err or b"").decode(errors="replace")):
            logger.info("ssh_provisioner: %s could not connect to %s (%s): trying again", step, host, why[:160])
            await asyncio.sleep(ssh_tunnel.CONNECT_RETRY_S)
            continue
        raise SshInstallError(f"{step}: {why}")
    raise SshInstallError(f"{step}: could not connect")    # not reached: the loop returns or raises


#: what the machine prints before every command _run sends (its first stdout line, taken off
#: the output): a connection that printed it had started the command
BEGUN = "__swarmai_begun__"


def _bash(script: str, *args: str) -> str:
    """``bash -c <script> bash <args...>``: the script fixed, each argument quoted."""
    return "bash -c " + shlex.quote(script) + " bash " + " ".join(shlex.quote(a) for a in args)


PREFLIGHT_SH = r'''
set -u
home="$HOME"; port="$1"
echo "uid=$(id -u)"; echo "gid=$(id -g)"; echo "user=$(id -un)"; echo "home=$home"
echo "arch=$(uname -m)"; echo "cores=$(nproc 2>/dev/null || echo 0)"
echo "mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo 2>/dev/null || echo 0)"
echo "disk_free_gb=$(df -Pk "$home" | awk 'NR==2 {printf "%d", $4/1048576}')"
if docker info >/dev/null 2>&1; then echo "docker=ok"
elif ! command -v docker >/dev/null 2>&1; then echo "docker=missing"
elif docker info 2>&1 | grep -qi "permission denied"; then echo "docker=denied"
else echo "docker=stopped"; fi
if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then echo "port=taken"; else echo "port=free"; fi
if docker inspect swarmai-hive >/dev/null 2>&1; then echo "hive_container=yes"; else echo "hive_container=no"; fi
# what the Host chooses the mounts from (hive/ssh_provisioner._found_mounts)
dirs=""
for d in /*; do
  [ -d "$d" ] || continue
  real="$(readlink -f "$d" 2>/dev/null || echo "$d")"
  # writable when the user can write it or a folder directly in it (/workplace/<user>)
  if [ -w "$d" ] || [ -n "$(find "$d" -mindepth 1 -maxdepth 1 -type d -writable -print -quit 2>/dev/null)" ]; then
    mode=rw; else mode=ro; fi
  dirs="$dirs${dirs:+,}$d:$mode:$real"
done
echo "topdirs=$dirs"
echo "home_real=$(readlink -f "$home" 2>/dev/null || echo "$home")"
'''

# every script that names the image is given its tag (``_tag``: the version and the helper's)
BUILD_SH = r'''
set -eu
ver="$1"; uid="$2"; gid="$3"; user="$4"; home="$5"
dir="$HOME/.swarmai-hive"; mkdir -p "$dir"; chmod 700 "$dir"
if docker image inspect "swarmai-hive-ssh:$ver" >/dev/null 2>&1; then echo "image=kept"; exit 0; fi
rm -rf "$dir/ctx"; mkdir -p "$dir/ctx"; tar -xf - -C "$dir/ctx"
docker build -q -t "swarmai-hive-ssh:$ver" --build-arg HIVE_UID="$uid" --build-arg HIVE_GID="$gid" \
  --build-arg HIVE_USER="$user" --build-arg HIVE_HOME="$home" "$dir/ctx" >/dev/null
rm -rf "$dir/ctx"; echo "image=built"
'''

IDENTITY_SH = r'''
set -eu
ver="$1"; profile="$2"
docker run --rm --network host -u "$(id -u):$(id -g)" -v "$HOME:$HOME" -e HOME="$HOME" -e AWS_PROFILE="$profile" \
  "swarmai-hive-ssh:$ver" /opt/swarmai/backend/.venv/bin/python -c \
  'import boto3, json; print(json.dumps(boto3.client("sts").get_caller_identity()["Arn"]))'
'''

# the identity of the Hive running now, from inside its own container (its environment's AWS_PROFILE)
WHOAMI_SH = r'''
set -eu
docker exec swarmai-hive /opt/swarmai/backend/.venv/bin/python -c \
  'import boto3, json; print(json.dumps(boto3.client("sts").get_caller_identity()["Arn"]))'
'''

# A new key for the machine's identity, made inside the image with no network and only the
# identity folder mounted: written next to the current one (key.pem.new, 0600) until its
# certificate comes (CERT_SH). Only the certificate request is printed; the key never leaves.
KEYGEN_SH = r'''
set -eu
ver="$1"
dir="$HOME/.swarmai-hive/identity"; mkdir -p "$dir"; chmod 700 "$HOME/.swarmai-hive" "$dir"
docker run --rm --network none -u "$(id -u):$(id -g)" -v "$dir:$dir" -e ID_DIR="$dir" \
  "swarmai-hive-ssh:$ver" /opt/swarmai/backend/.venv/bin/python -c '
import os
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
key = ec.generate_private_key(ec.SECP256R1())
path = os.path.join(os.environ["ID_DIR"], "key.pem.new")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()))
os.close(fd)
req = x509.CertificateSigningRequestBuilder().subject_name(
    x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "swarmai-hive")])).sign(key, hashes.SHA256())
print(req.public_bytes(serialization.Encoding.PEM).decode(), end="")
'
'''

# The certificate the Host signed (stdin) for the key KEYGEN_SH made: both put in place. The
# helper reads both files at each credential fetch; credentials already given stay valid.
CERT_SH = r'''
set -eu
dir="$HOME/.swarmai-hive/identity"
[ -f "$dir/key.pem.new" ] || { echo "the machine has no new key for this certificate" >&2; exit 1; }
(umask 077; cat > "$dir/cert.pem.new")
grep -q -- "-----BEGIN CERTIFICATE-----" "$dir/cert.pem.new" || { echo "no certificate came" >&2; exit 1; }
chmod 600 "$dir/cert.pem.new"
mv -f "$dir/key.pem.new" "$dir/key.pem"; mv -f "$dir/cert.pem.new" "$dir/cert.pem"
echo cert=written
'''

# The Hive's AWS profile swarmai-hive-<id> (<id>: the machine's name, or <name>-ra while it
# moves to its own identity): written between marker lines, replacing the block of that id;
# nothing else in the file changes. credential_process does not expand ~: paths are absolute.
PROFILE_SH = r'''
set -eu
id="$1"; anchor="$2"; profile="$3"; role="$4"; region="$5"
cfg="$HOME/.aws/config"; mkdir -p "$HOME/.aws"; chmod 700 "$HOME/.aws"; touch "$cfg"; chmod 600 "$cfg"
idd="$HOME/.swarmai-hive/identity"
begin="# swarmai-hive $id begin"; end="# swarmai-hive $id end"
cp -p "$cfg" "$cfg.swarmai-bak.$(date +%s)"
# the earlier block of this id out, and the blank lines it leaves at the end (one comes back
# before the new block): written again at each renewal, the file does not grow
awk -v b="$begin" -v e="$end" 'index($0, b) == 1 {skip = 1; next} skip && index($0, e) == 1 {skip = 0; next} !skip' \
  "$cfg" | awk '/^$/ {n++; next} {for (; n > 0; n--) print ""; print}' > "$cfg.swarmai-tmp"
printf '\n%s (written by SwarmAI; removed with the machine)\n[profile swarmai-hive-%s]\ncredential_process = /opt/swarmai/bin/aws_signing_helper credential-process --certificate %s/cert.pem --private-key %s/key.pem --trust-anchor-arn %s --profile-arn %s --role-arn %s\nregion = %s\n%s\n' \
  "$begin" "$id" "$idd" "$idd" "$anchor" "$profile" "$role" "$region" "$end" >> "$cfg.swarmai-tmp"
chmod 600 "$cfg.swarmai-tmp"; mv "$cfg.swarmai-tmp" "$cfg"
echo profile=written
'''

UNPROFILE_SH = r'''
set -eu
name="$1"; cfg="$HOME/.aws/config"
[ -f "$cfg" ] || exit 0
begin="# swarmai-hive $name begin"; end="# swarmai-hive $name end"
grep -qF "$begin" "$cfg" || exit 0
cp -p "$cfg" "$cfg.swarmai-bak.$(date +%s)"
awk -v b="$begin" -v e="$end" 'index($0, b) == 1 {skip = 1; next} skip && index($0, e) == 1 {skip = 0; next} !skip' \
  "$cfg" > "$cfg.swarmai-tmp" && chmod 600 "$cfg.swarmai-tmp" && mv "$cfg.swarmai-tmp" "$cfg"
'''

# The Hive container, the same for an install and an update. A mount 'path:ro' becomes
# '-v path:path:ro'. The environment file (it decides how the Hive process starts) comes from
# the Host on stdin and lives only for this script, in a private temporary directory outside
# the home: the home is mounted writable and a run has the Hive's own uid, so a file kept
# there could be changed by a run (a PYTHONPATH or a password hash that holds across
# restarts). docker keeps the environment in the container's own configuration, so a
# restart needs no file. check_settings refuses a mount that would show /tmp in a container.
_ENV_IN = r'''
dir="$HOME/.swarmai-hive"; mkdir -p "$dir/data"; chmod 700 "$dir"; rm -f "$dir/hive.env"
tmp="$(mktemp -d /tmp/swarmai-hive-env.XXXXXXXX)"; trap 'rm -rf "$tmp"' EXIT
env="$tmp/hive.env"; (umask 077; cat > "$env")
'''

_RUN_HIVE = r'''
mounts=""
for m in "$@"; do
  src="${m%:ro}"
  if [ "$src" = "$m" ]; then mounts="$mounts -v $src:$src"; else mounts="$mounts -v $src:$src:ro"; fi
done
# shellcheck disable=SC2086
docker run -d --name swarmai-hive --restart unless-stopped --network host -u "$(id -u):$(id -g)" \
  -v "$HOME:$HOME" $mounts --env-file "$env" -e HOME="$HOME" ${attempt:+--label swarmai.attempt=$attempt} \
  "swarmai-hive-ssh:$ver" >/dev/null
'''

START_SH = r'''
set -eu
ver="$1"; shift; attempt=""
''' + _ENV_IN + r'''
docker rm -f swarmai-hive >/dev/null 2>&1 || true
''' + _RUN_HIVE + r'''
echo started
'''

# Arguments: the version, the attempt (an id update() makes for each call) and the mounts. Safe
# to run twice within one call: _run sends a command again only when ssh's connection failed
# before the machine's start marker arrived, and on a link that stalls just after the command
# began that marker can be lost. When this attempt's container already runs (labelled with the
# attempt, the old one kept as swarmai-hive-old), the second run changes nothing, so the
# container to go back to is never removed. Another update() is another attempt: it swaps.
SWAP_SH = r'''
set -eu
ver="$1"; attempt="$2"; shift 2
''' + _ENV_IN + r'''
if docker inspect swarmai-hive-old >/dev/null 2>&1 \
    && [ "$(docker inspect -f '{{index .Config.Labels "swarmai.attempt"}}' swarmai-hive 2>/dev/null)" = "$attempt" ] \
    && [ "$(docker inspect -f '{{.State.Running}}' swarmai-hive 2>/dev/null)" = "true" ]; then
  echo swapped; exit 0
fi
docker rm -f swarmai-hive-old >/dev/null 2>&1 || true
if docker inspect swarmai-hive >/dev/null 2>&1; then
  docker stop swarmai-hive >/dev/null; docker rename swarmai-hive swarmai-hive-old
fi
''' + _RUN_HIVE + r'''
echo swapped
'''

ROLLBACK_SH = r'''
set -eu
docker rm -f swarmai-hive >/dev/null 2>&1 || true
if docker inspect swarmai-hive-old >/dev/null 2>&1; then
  docker rename swarmai-hive-old swarmai-hive; docker start swarmai-hive >/dev/null; echo restored
fi
'''

COMMIT_SH = r'''
set -eu
keep="$1"
docker rm -f swarmai-hive-old >/dev/null 2>&1 || true
for img in $(docker images --format '{{.Repository}}:{{.Tag}}' swarmai-hive-ssh); do
  [ "$img" = "swarmai-hive-ssh:$keep" ] || docker rmi "$img" >/dev/null 2>&1 || true
done
'''

REMOVE_SH = r'''
set -u
docker rm -f swarmai-hive swarmai-hive-old >/dev/null 2>&1 || true
for img in $(docker images --format '{{.Repository}}:{{.Tag}}' swarmai-hive-ssh); do docker rmi "$img" >/dev/null 2>&1 || true; done
rm -f "$HOME/.swarmai-hive/hive.env"
rm -rf "$HOME/.swarmai-hive/identity"
echo removed
'''


#: what the person does on the machine when docker is not usable there (the preflight's
#: ``docker``): the one thing SwarmAI asks of a machine besides SSH, it does not install it
DOCKER_HINTS = {
    "missing": "docker is not installed on the machine; install Docker Engine there "
               "(https://docs.docker.com/engine/install/), then Retry",
    "denied": "this user may not use docker on the machine; add it to the docker group there "
              "(sudo usermod -aG docker $USER, then sign in again), then Retry",
    "stopped": "docker is installed on the machine but not running; start it there "
               "(sudo systemctl start docker), then Retry",
}


def _ssh_hint(error: str, host: str) -> str:
    """What the person can do when this Mac cannot reach the machine over SSH, read from ssh's
    own last line; '' when it says nothing known."""
    low = error.lower()
    if "permission denied" in low:
        return (f"; this computer cannot sign in to {host} without a password: add your SSH key there "
                f"(ssh-copy-id {host}) or renew your SSH certificate, then Retry")
    if "host key verification failed" in low:
        return f"; this computer does not know {host}'s host key yet: run ssh {host} once in a terminal and accept it, then Retry"
    if "could not resolve hostname" in low or "name or service not known" in low:
        return f"; {host} does not resolve from this computer: check the name, or join the network it is on, then Retry"
    if any(w in low for w in ("timed out", "connection refused", "no route to host", "did not finish within")):
        return f"; {host} does not answer on SSH: check that it is running and reachable from this computer, then Retry"
    return ""


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in text.splitlines():
        k, sep, v = ln.partition("=")
        if sep and re.fullmatch(r"[a-z_]{1,20}", k):
            out[k] = v.strip()
    return out


# -- the package ---------------------------------------------------------------------------

def _host_version() -> str:
    from core import hive_idle
    v = hive_idle._host_version()
    if v is None:
        raise SshInstallError("this Host has no release version to install (a development build)")
    return v


async def _session(acct: dict[str, Any], region: str):
    from hive.provisioner import HiveProvisioner
    return await HiveProvisioner(Path(_db_path()))._session(acct, region, "manage")


async def _bucket(acct: dict[str, Any], region: str) -> str:
    """The account's SwarmAI bucket in *region* (the one EC2 Hives use; created when there is
    none, ``HiveProvisioner._ensure_s3_bucket``)."""
    from hive.provisioner import HiveProvisioner
    return await HiveProvisioner(Path(_db_path()))._ensure_s3_bucket(
        await _session(acct, region), region, str(acct.get("account_id") or ""))


async def _package(acct: dict[str, Any], region: str, bucket: str, version: str) -> bytes:
    """The release package of *version*, from the account's bucket (put there first when it is
    not, as an EC2 deploy does), checked against the ``checksums.txt`` beside it."""
    from hive.provisioner import HiveProvisioner, bucket_owner
    prov = HiveProvisioner(Path(_db_path()))
    session = await _session(acct, region)
    await prov._sync_release_to_s3(session, bucket, version, region, str(acct.get("account_id") or ""))
    name = f"swarmai-hive-v{version}-linux-arm64.tar.gz"

    def get() -> tuple[bytes, str]:
        s3 = session.client("s3", region_name=region)
        owner = bucket_owner(bucket)
        data = s3.get_object(Bucket=bucket, Key=f"v{version}/{name}", **owner)["Body"].read()
        sums = s3.get_object(Bucket=bucket, Key=f"v{version}/checksums.txt", **owner)["Body"].read().decode()
        return data, sums

    data, sums = await asyncio.to_thread(get)
    want = next((ln.split()[0].lower() for ln in sums.splitlines() if name in ln and ln.split()), "")
    if not re.fullmatch(r"[0-9a-f]{64}", want) or hashlib.sha256(data).hexdigest() != want:
        raise SshInstallError(f"image: the package v{version} does not match its checksums.txt; not installed")
    return data


def _tag(version: str) -> str:
    """The image's tag: the Hive's version and the credential helper's, so an image built before
    the helper was in it (same Hive version) is never taken as it is (BUILD_SH keeps a tag it has)."""
    return f"{version}-h{ssh_identity.HELPER_VERSION}"


async def _helper(facts: dict[str, str]) -> bytes:
    """The credential helper for the machine's architecture, checked against its SHA256."""
    return await asyncio.to_thread(ssh_identity.helper_binary, facts.get("arch", ""))


def _context(package: bytes, helper: bytes) -> bytes:
    """The image's build context as a tar: the Dockerfile, the checked package and the checked
    credential helper."""
    from hive.ssh_image import DOCKERFILE
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, body, mode in (("Dockerfile", DOCKERFILE.encode(), 0o644), ("swarmai-hive.tar.gz", package, 0o644),
                                 ("aws_signing_helper", helper, 0o755)):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(body), mode, int(time.time())
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


# -- the role (D4) --------------------------------------------------------------------------

def role_name(name: str) -> str:
    return f"SwarmAI-Hive-{name}"


async def _ensure_role(acct: dict[str, Any], region: str, name: str, trust: dict[str, Any]) -> str:
    """The Hive's role, created or brought up to date: the EC2 Hive's inline policy and
    ``HiveName`` tag (hive/provisioner._put_hive_role_policy), with the trust policy *trust*."""
    from hive.provisioner import MANAGED_TAG, _boundary_arn, _put_hive_role_policy
    session = await _session(acct, region)
    rname = role_name(name)

    def ensure() -> str:
        iam = session.client("iam")
        doc = json.dumps(trust)
        try:
            kwargs: dict[str, Any] = {}
            boundary = _boundary_arn(session, iam)
            if boundary:
                kwargs["PermissionsBoundary"] = boundary
            arn = iam.create_role(RoleName=rname, AssumeRolePolicyDocument=doc,
                                  Description=f"SwarmAI Hive role for the SSH machine {name}",
                                  Tags=[{"Key": "HiveName", "Value": name}, MANAGED_TAG], **kwargs)["Role"]["Arn"]
        except iam.exceptions.EntityAlreadyExistsException:
            arn = iam.get_role(RoleName=rname)["Role"]["Arn"]
            iam.update_assume_role_policy(RoleName=rname, PolicyDocument=doc)
        _put_hive_role_policy(iam, rname)
        return arn

    return await asyncio.to_thread(ensure)


async def _role_trust(acct: dict[str, Any], region: str, name: str) -> dict[str, Any]:
    """The trust policy the Hive's role has now ({} when there is no such role)."""
    session = await _session(acct, region)

    def get() -> dict[str, Any]:
        iam = session.client("iam")
        try:
            doc = iam.get_role(RoleName=role_name(name))["Role"].get("AssumeRolePolicyDocument") or {}
        except iam.exceptions.NoSuchEntityException:
            return {}
        if isinstance(doc, str):
            from urllib.parse import unquote
            doc = json.loads(unquote(doc))
        return doc if isinstance(doc, dict) else {}
    return await asyncio.to_thread(get)


async def _set_role_trust(acct: dict[str, Any], region: str, name: str, trust: dict[str, Any]) -> None:
    session = await _session(acct, region)
    await asyncio.to_thread(lambda: session.client("iam").update_assume_role_policy(
        RoleName=role_name(name), PolicyDocument=json.dumps(trust)))


def _also_roles_anywhere(trust: dict[str, Any], anchor_arn: str, cn: str) -> dict[str, Any]:
    """*trust* as it is (the person's role a machine was installed with) and this install's
    Roles Anywhere certificate: a moving machine's old and new container both get credentials."""
    kept = [st for st in (trust.get("Statement") or []) if isinstance(st, dict)
            and not str(st.get("Sid") or "").startswith("RolesAnywhere")]
    return {"Version": "2012-10-17", "Statement": kept + [ssh_identity.roles_anywhere_statement(anchor_arn, cn)]}


async def _delete_role(acct: dict[str, Any], region: str, name: str) -> None:
    session = await _session(acct, region)

    def delete() -> None:
        iam = session.client("iam")
        rname = role_name(name)
        try:
            for p in iam.list_role_policies(RoleName=rname).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=rname, PolicyName=p)
            iam.delete_role(RoleName=rname)
        except iam.exceptions.NoSuchEntityException:
            pass

    await asyncio.to_thread(delete)


# -- the steps ------------------------------------------------------------------------------

def _hash(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(14)).decode()


def env_file(row: dict[str, Any], cfg: dict[str, Any], pass_hash: str, region: str) -> bytes:
    """The container's environment file. Every value has a fixed shape (checked here again):
    an environment file has no quoting."""
    lines = {
        "SWARMAI_MODE": "hive", "SWARMAI_CARRIER": CARRIER,
        "SWARMAI_PORT": str(int(cfg["remote_port"])),
        "SWARM_DATA_DIR": f"{cfg['_home']}/.swarmai-hive/data",
        "SWARMAI_HIVE_NAME": row["name"], "SWARMAI_HIVE_BUCKET": row["s3_bucket"], "AWS_REGION": region,
        "AWS_PROFILE": cfg.get("aws_profile") or f"swarmai-hive-{row['name']}",
        "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "SWARM_RESUME_VIA_QUERY": "true",
        "HIVE_USER": row["auth_user"], "HIVE_PASS_HASH": pass_hash,
    }
    for k, v in lines.items():
        if not re.fullmatch(r"[A-Za-z0-9_./:$@+-]{1,200}", v):
            raise SshInstallError(f"start: {k} has an unexpected value")
    return ("".join(f"{k}={v}\n" for k, v in lines.items())).encode()


async def _health(machine_id: str, timeout: float = HEALTH_TIMEOUT_S, version: Optional[str] = None) -> None:
    """``/health`` through the tunnel answers ``healthy`` (at *version* when given)."""
    import httpx
    from core.carriers.ssh import config
    row = await _row(machine_id)
    cfg = config(row)
    end = time.monotonic() + timeout
    last = ""
    while time.monotonic() < end:
        try:
            await ssh_tunnel.ensure(machine_id, cfg["ssh_host"], cfg["local_port"], cfg["remote_port"])
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"http://127.0.0.1:{cfg['local_port']}/health")
            d = r.json() if r.status_code == 200 else {}
            if d.get("status") == "healthy" and (version is None or str(d.get("version")) == version):
                return
            last = f"HTTP {r.status_code}, version {d.get('version')}"
        except Exception as e:  # noqa: BLE001 — not up yet
            last = str(e)[:200] or type(e).__name__
        await asyncio.sleep(3)
    raise SshInstallError(f"start: the Hive did not answer /health within {timeout:.0f}s ({last})")


def _who(facts: dict[str, str]) -> tuple[int, int, str, str]:
    """The uid, gid, user name and home the machine's preflight reported, each of a fixed
    shape (they go into the image build and the environment file)."""
    try:
        uid, gid = int(facts["uid"]), int(facts["gid"])
    except (KeyError, ValueError):
        raise SshInstallError("preflight: the machine did not say who the user is") from None
    user, home = facts.get("user", ""), facts.get("home", "")
    if uid < 0 or gid < 0 or not re.fullmatch(r"[a-z_][a-z0-9_.-]{0,31}", user) \
            or not re.fullmatch(r"/[A-Za-z0-9._/-]{1,200}", home) or ".." in home:
        raise SshInstallError("preflight: unexpected user name or home directory")
    return uid, gid, user, home


def _region(acct_row: dict[str, Any]) -> str:
    """The account's region, of a region's shape: it is written into the machine's AWS config."""
    from core.hive_ssh_mode import REGION_RE
    region = acct_row.get("default_region") or "us-east-1"
    if not REGION_RE.fullmatch(region):
        raise SshInstallError("the SwarmAI account's region has an unexpected value")
    return region


async def _install(machine_id: str) -> None:
    """Steps 1-6 (module docstring); each failure ends the row in 'error' with why."""
    from core.carriers.ssh import config
    from hive import accounts
    row = await _row(machine_id)
    raw = json.loads(row.get("carrier_config") or "{}")
    cfg = config(row)
    host, name = cfg["ssh_host"], row["name"]
    acct_row = await _account(row["account_ref"])
    region = _region(acct_row)
    version = _host_version()

    try:
        out = await _run(host, _bash(PREFLIGHT_SH, str(cfg["remote_port"])), step="preflight")
    except SshInstallError as e:               # the first contact: say what the person can do about it
        raise SshInstallError(f"{e}{_ssh_hint(str(e), host)}") from None
    facts = _parse_kv(out)
    if facts.get("docker") != "ok":
        raise SshInstallError(f"preflight: {DOCKER_HINTS.get(facts.get('docker', ''), DOCKER_HINTS['missing'])}")
    if facts.get("port") == "taken" and facts.get("hive_container") != "yes":
        raise SshInstallError(f"preflight: port {cfg['remote_port']} is taken on the machine by another program; "
                              "free it there, then Retry")
    try:
        if int(facts.get("disk_free_gb") or 0) < 5:
            raise SshInstallError("preflight: less than 5 GB free in the home directory; free some space there, "
                                  "then Retry")
    except ValueError:
        raise SshInstallError("preflight: the machine did not say how much disk is free") from None
    uid, gid, user, home = _who(facts)
    desc = f"{facts.get('arch', '?')} · {facts.get('cores', '?')} vCPU · {facts.get('mem_gb', '?')} GB"
    found: dict[str, Any] = {"_home": home}
    if raw.get("mounts_auto"):                 # added without mounts: the machine's own, found now
        found["mounts"] = cfg["mounts"] = _found_mounts(facts, home)
    await _set(machine_id, instance_type=desc)
    await _merge_config(machine_id, **found)
    cfg["_home"] = home

    if not row.get("s3_bucket"):
        bucket = await _bucket(acct_row, region)
        await _set(machine_id, s3_bucket=bucket)
        row["s3_bucket"] = bucket

    package = await _package(acct_row, region, row["s3_bucket"], version)
    helper = await _helper(facts)
    tag = _tag(version)
    await _run(host, _bash(BUILD_SH, tag, str(uid), str(gid), user, home), _context(package, helper),
               BUILD_TIMEOUT_S, "image")

    cn = ssh_identity.cn_for(name, machine_id)
    ca, anchor, hive_role, profile_arn = await _identity_aws(acct_row, region, name, cn)
    await _set(machine_id, iam_role_name=role_name(name))
    # recorded at once: a removal deletes the profile even when a later step fails
    await _merge_config(machine_id, profile_arn=profile_arn, identity_region=region)
    not_after = await _new_certificate(host, tag, ca, cn)
    aws_profile = f"swarmai-hive-{name}"
    await _run(host, _bash(PROFILE_SH, name, anchor, profile_arn, hive_role, region), step="profile")
    await _check_identity(host, tag, aws_profile, name)
    await _merge_config(machine_id, identity=IDENTITY, anchor_arn=anchor, role_arn=hive_role, cn=cn,
                        not_after=not_after, aws_profile=aws_profile, renew_error=None)
    cfg["aws_profile"] = aws_profile

    password = row.get("auth_password") or secrets.token_urlsafe(24)
    user_name = row.get("auth_user") or "admin"
    await _set(machine_id, auth_user=user_name, auth_password=password)
    row.update(auth_user=user_name, auth_password=password)
    pass_hash = await asyncio.to_thread(_hash, password)
    await _run(host, _bash(START_SH, tag, *cfg["mounts"]), env_file(row, cfg, pass_hash, region),
               timeout=120, step="start")
    await _health(machine_id, version=version)
    await _set(machine_id, status="running", version=version, error_message=None)
    logger.info("ssh_provisioner: %s runs the Hive v%s", name, version)


#: ``carrier_config.identity`` of a machine with its own identity (one installed on a profile of
#: the person's has none, and moves to it in its next update)
IDENTITY = "rolesanywhere"
#: a just-created role can take a few seconds before Roles Anywhere accepts it in a profile
PROFILE_TRIES = 5


async def _identity_aws(acct: dict[str, Any], region: str, name: str, cn: str,
                        keep_trust: bool = False) -> tuple[Any, str, str, str]:
    """In AWS: the Host's CA and its trust anchor in *region*, the Hive's role trusting only a
    certificate of that anchor with the CN *cn* (*keep_trust*: and what it trusts now), and the
    machine's Roles Anywhere profile. (CA, anchor ARN, role ARN, profile ARN)."""
    session = await _session(acct, region)
    ca, anchor = await asyncio.to_thread(ssh_identity.anchor_for, session, str(acct.get("account_id") or ""), region)
    trust = ssh_identity.trust_policy(anchor, cn)
    if keep_trust:
        trust = _also_roles_anywhere(await _role_trust(acct, region, name), anchor, cn)
    hive_role = await _ensure_role(acct, region, name, trust)
    for attempt in range(PROFILE_TRIES):
        try:
            profile_arn = await asyncio.to_thread(ssh_identity.ensure_profile, session, region,
                                                  f"swarmai-hive-{name}", hive_role)
            break
        except Exception as e:  # noqa: BLE001 — only AWS's "not yet" is waited out, anything else raised
            code = ((getattr(e, "response", None) or {}).get("Error") or {}).get("Code")
            if code != "ValidationException" or attempt == PROFILE_TRIES - 1:
                raise
            await asyncio.sleep(5)
    _check_arns(anchor, hive_role, profile_arn)
    return ca, anchor, hive_role, profile_arn


def _check_arns(*arns: Any) -> None:
    """Each value written into the machine's AWS config is an ARN of a fixed shape."""
    for arn in arns:
        if not isinstance(arn, str) or not _ARN_RE.fullmatch(arn):
            raise SshInstallError("identity: an ARN for the machine's AWS config has an unexpected value")


_CSR_RE = re.compile(r"-----BEGIN CERTIFICATE REQUEST-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE REQUEST-----")


async def _new_certificate(host: str, tag: str, ca: Any, cn: str, profile: tuple = ()) -> float:
    """A new key on the machine (KEYGEN_SH), its certificate signed here for *cn* and put next
    to it (CERT_SH). With *profile* (PROFILE_SH's arguments: a new trust anchor), the profile
    is written in the same script, so the files and the anchor the profile names always match.
    The certificate's end (Unix seconds)."""
    out = await _run(host, _bash(KEYGEN_SH, tag), timeout=120, step="identity key")
    m = _CSR_RE.search(out)
    if not m:
        raise SshInstallError("identity key: the machine gave no certificate request")
    pem, not_after = await asyncio.to_thread(ssh_identity.sign_csr, ca, m.group(0) + "\n", cn)
    await _run(host, _bash(CERT_SH + PROFILE_SH, *profile) if profile else _bash(CERT_SH), pem.encode(),
               step="identity certificate")
    return not_after


async def _check_identity(host: str, tag: str, aws_profile: str, name: str) -> None:
    """*aws_profile* resolves, inside the image, to the Hive's role. IAM is eventually
    consistent: a new role or trust can take a few seconds to be assumable."""
    got: Any = ""
    for _ in range(6):
        out = await _retry_identity(host, tag, aws_profile)
        got = _arn(out)
        if f":assumed-role/{role_name(name)}/" in got:
            return
        await asyncio.sleep(5)
    raise SshInstallError(f"profile check: {aws_profile} does not resolve to {role_name(name)} "
                          f"({str(got)[:80] or 'no answer'})")


def _arn(out: str) -> str:
    """The ARN the identity script printed last (a JSON string), or ''."""
    try:
        got = json.loads(out.strip().splitlines()[-1]) if out.strip() else ""
    except ValueError:
        return ""
    return got if isinstance(got, str) else ""


async def _retry_identity(host: str, tag: str, aws_profile: str) -> str:
    try:
        # one attempt each: _check_identity's own loop tries again
        return await _run(host, _bash(IDENTITY_SH, tag, aws_profile), timeout=120, step="profile check", tries=1)
    except SshInstallError as e:
        logger.info("ssh_provisioner: profile check not yet (%s)", e)
        return ""


async def _account(account_ref: str) -> dict[str, Any]:
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM hive_accounts WHERE id = ?", (account_ref,))
        got = await cur.fetchone()
    if got is None:
        raise SshInstallError("the machine's account is not known here")
    return dict(got)


async def _guarded(machine_id: str, coro) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 — every failure ends in 'error' with why
        why = str(e) if isinstance(e, (SshInstallError, ssh_identity.IdentityError, ValueError)) \
            else f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("ssh_provisioner: %s failed (%s)", machine_id, why)
        try:
            await _set(machine_id, status="error", error_message=why[:500])
        except Exception:  # noqa: BLE001 — the row may be gone
            pass


_TASKS: set[asyncio.Task] = set()


def _keep(coro, name: str) -> None:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def add(ssh_host: str, name: Optional[str] = None, mounts: Optional[tuple] = None,
              cert_renew_hint: str = "", reaches_private: bool = True, general_tasks: bool = True,
              remote_port: int = 18321) -> dict[str, Any]:
    """Record a new SSH machine (``provisioning``) and install it in the background.
    ValueError for unusable settings; LookupError when the SwarmAI account is not connected;
    FileExistsError for a name already taken. Only *ssh_host* is needed: *mounts* None are
    found on the machine by the install (and again at each update); given ones are kept as
    given. The machine's AWS identity is its own (``_identity_aws``)."""
    import uuid

    import aiosqlite
    from hive import accounts
    check_settings(ssh_host, None if mounts is None else list(mounts), cert_renew_hint, remote_port)
    name = name or default_name(ssh_host)
    if not NAME_RE.fullmatch(name):
        raise ValueError("a name is lower-case letters, digits and hyphens, starting with a letter")
    if accounts.swarmai_account() is None:
        raise LookupError("connect the SwarmAI account first (Settings)")
    await accounts.ensure_swarmai_row(Path(_db_path()))
    cfg = {"ssh_host": ssh_host, "remote_port": int(remote_port), "local_port": await _free_local_port(),
           "reaches_private": bool(reaches_private),
           "general_tasks": bool(general_tasks), "mounts": list(mounts or ()), "mounts_auto": mounts is None,
           "cert_renew_hint": cert_renew_hint}
    now = _iso()
    row = {"id": str(uuid.uuid4()), "name": name, "account_ref": accounts.SWARMAI_ROW_ID,
           "region": (accounts.swarmai_account() or {}).get("region") or "us-east-1",
           "instance_type": "ssh", "status": "provisioning", "carrier": CARRIER,
           "carrier_config": json.dumps(cfg), "auth_user": "admin", "created_at": now, "updated_at": now}
    async with accounts.fleet_lock, aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("SELECT 1 FROM hive_instances WHERE name = ?", (name,))
        if await cur.fetchone():
            raise FileExistsError(f"a Hive named {name} already exists")
        cols = ", ".join(row)
        await conn.execute(f"INSERT INTO hive_instances ({cols}) VALUES ({', '.join('?' for _ in row)})",
                           tuple(row.values()))
        await conn.commit()
    _keep(_guarded(row["id"], _install(row["id"])), f"ssh-install-{row['id'][:8]}")
    return {k: v for k, v in row.items() if k != "carrier_config"} | {"config": cfg}


async def retry(machine_id: str) -> None:
    """Run the install again (a row in 'error'): every step that is already done is kept."""
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute("UPDATE hive_instances SET status = 'provisioning', error_message = NULL, "
                                 "updated_at = datetime('now') WHERE id = ? AND carrier = ? AND status = 'error'",
                                 (machine_id, CARRIER))
        await conn.commit()
    if cur.rowcount == 0:
        raise RuntimeError("only an SSH machine in error is retried")
    _keep(_guarded(machine_id, _install(machine_id)), f"ssh-retry-{machine_id[:8]}")


async def _heartbeat(machine_id: str, stop: asyncio.Event) -> None:
    import aiosqlite
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), HEARTBEAT_S)
        except asyncio.TimeoutError:
            async with aiosqlite.connect(_db_path()) as conn:
                await conn.execute("UPDATE hive_instances SET updated_at = datetime('now') "
                                   "WHERE id = ? AND status = 'updating'", (machine_id,))
                await conn.commit()


async def update(machine_id: str, version: str) -> None:
    """A claimed ('updating') machine to *version*: new image, container swapped, ``/health``
    at that version and the new container's identity the Hive's role; the old container
    restored when the new one does not come up. A machine without its own identity yet
    (installed on a profile of the person's) moves to it here (design §4.5): before the swap
    the role trusts both, the new container uses a new profile ``swarmai-hive-<name>-ra``;
    once the new one is kept, the role trusts only the certificate and the old profile is
    removed. Ends 'running' (either version) or 'error' with why."""
    from core.carriers.ssh import config
    stop = asyncio.Event()
    beat = asyncio.get_running_loop().create_task(_heartbeat(machine_id, stop))
    swapped = committed = False
    try:
        if not VERSION_RE.fullmatch(version):
            raise SshInstallError("update: unexpected version")
        row = await _row(machine_id)
        cfg = config(row)
        host, name = cfg["ssh_host"], row["name"]
        facts = _parse_kv(await _run(host, _bash(PREFLIGHT_SH, str(cfg["remote_port"])), step="preflight"))
        uid, gid, user, home = _who(facts)
        if cfg["mounts_auto"]:                 # a directory added to the machine since is mounted now
            cfg["mounts"] = _found_mounts(facts, home)
        acct = await _account(row["account_ref"])
        region = _region(acct)
        package = await _package(acct, region, row["s3_bucket"], version)
        helper = await _helper(facts)
        tag = _tag(version)
        await _run(host, _bash(BUILD_SH, tag, str(uid), str(gid), user, home), _context(package, helper),
                   BUILD_TIMEOUT_S, "image")
        moving = cfg["identity"] != IDENTITY
        if moving:                             # the old container keeps its profile and the trust in it
            cn = ssh_identity.cn_for(name, machine_id)
            ca, anchor, hive_role, profile_arn = await _identity_aws(acct, region, name, cn, keep_trust=True)
            await _merge_config(machine_id, profile_arn=profile_arn, identity_region=region)
            not_after = await _new_certificate(host, tag, ca, cn)
            cfg["aws_profile"] = f"swarmai-hive-{name}-ra"
            await _run(host, _bash(PROFILE_SH, f"{name}-ra", anchor, profile_arn, hive_role, region), step="profile")
            await _check_identity(host, tag, cfg["aws_profile"], name)
        # the environment again, from the Host (on stdin): nothing found on the machine is used
        if not row.get("auth_password"):
            raise SshInstallError("update: the machine has no password recorded; retry its install")
        cfg["_home"] = home
        pass_hash = await asyncio.to_thread(_hash, row["auth_password"])
        await _run(host, _bash(SWAP_SH, tag, secrets.token_hex(8), *cfg["mounts"]), env_file(row, cfg, pass_hash, region),
                   timeout=180, step="update")
        swapped = True
        await _health(machine_id, version=version)
        got = _arn(await _run(host, _bash(WHOAMI_SH), timeout=120, step="identity check"))
        if f":assumed-role/{role_name(name)}/" not in got:
            raise SshInstallError(f"identity check: the new container is not {role_name(name)} ({got[:80] or 'no answer'})")
        await _run(host, _bash(COMMIT_SH, tag), timeout=120, step="update")
        committed = True
        if moving:                             # the identity the running container has, recorded at once
            await _merge_config(machine_id, identity=IDENTITY, anchor_arn=anchor, role_arn=hive_role, cn=cn,
                                not_after=not_after, aws_profile=cfg["aws_profile"], renew_error=None,
                                move_pending=True)
            await _set(machine_id, version=version)
            await _finish_move(machine_id)
        if cfg["mounts_auto"]:                 # recorded once the container with them is the one running
            await _merge_config(machine_id, mounts=cfg["mounts"])
        await _set(machine_id, status="running", version=version, error_message=None)
        logger.info("ssh_provisioner: %s updated to v%s", name, version)
    except Exception as e:  # noqa: BLE001 — restore what ran before, say why
        why = str(e)[:400]
        # before the swap the old container never stopped: the machine still runs it; once the
        # new one is kept (committed) there is no old one, and the new one runs
        still_runs = not swapped or committed
        if swapped and not committed:
            try:
                row = await _row(machine_id)
                await _run(config(row)["ssh_host"], _bash(ROLLBACK_SH), timeout=120, step="rollback")
                why += "; the previous version was started again"
                still_runs = True
            except Exception as e2:  # noqa: BLE001
                why += f"; restoring the previous version failed too ({str(e2)[:120]})"
        logger.warning("ssh_provisioner: update of %s failed (%s)", machine_id, why)
        try:
            kept = {"version": version} if committed else {}    # the new container is the one running
            await _set(machine_id, status="running" if still_runs else "error",
                       error_message=f"update to v{version} failed: {why}"[:500], **kept)
        except Exception:  # noqa: BLE001
            pass
    finally:
        stop.set()
        await beat


async def _finish_move(machine_id: str) -> None:
    """The end of a machine's move to its own identity, once its new container is kept: the
    role trusts only the certificate, the person's profile block goes. Run again by
    ``renew_due`` until it succeeds (``move_pending``)."""
    from core.carriers.ssh import config
    row = await _row(machine_id)
    cfg = config(row)
    acct = await _account(row["account_ref"])
    region = cfg["identity_region"] or _region(acct)
    await _set_role_trust(acct, region, row["name"], ssh_identity.trust_policy(cfg["anchor_arn"], cfg["cn"]))
    await _run(cfg["ssh_host"], _bash(UNPROFILE_SH, row["name"]), step="update profile")
    await _merge_config(machine_id, _drop=("source_profile", "move_pending"))


# -- the machine's certificate, renewed (design §4.4) ---------------------------------------

#: machines whose renewal is under way (one at a time per machine)
_RENEWING: set[str] = set()


def renewal_due(cfg: dict[str, Any], now: Optional[float] = None) -> bool:
    """A machine with its own identity whose certificate has less than RENEW_BEFORE_S left
    (or no end recorded)."""
    if cfg.get("identity") != IDENTITY:
        return False
    until = cfg.get("not_after")
    now = time.time() if now is None else now
    return not isinstance(until, (int, float)) or until - now < ssh_identity.RENEW_BEFORE_S


async def renew_due(now: Optional[float] = None) -> list[str]:
    """Start the renewal of every running machine whose certificate is due (``renewal_due``),
    and the end of a move a failed step left (``move_pending``); a machine with neither is not
    contacted. Names of the machines started. Run by hive_idle.check_once every round,
    whatever the versions and the Host's build."""
    from core.carriers import ssh
    from core.carriers.ssh import config
    started: list[str] = []
    for row in await ssh._rows():
        if row.get("status") != "running" or row["id"] in _RENEWING:
            continue
        try:
            cfg = config(row)
        except ValueError:
            continue
        if not (cfg["move_pending"] or renewal_due(cfg, now)):
            continue
        _RENEWING.add(row["id"])
        _keep(_renew_guarded(row["id"], cfg["move_pending"]), f"ssh-renew-{row['id'][:8]}")
        started.append(str(row["name"]))
    return started


async def _renew_guarded(machine_id: str, finish_move: bool = False) -> None:
    try:
        await (_finish_move(machine_id) if finish_move else renew(machine_id))
    except Exception as e:  # noqa: BLE001 — said on the card; tried again next round
        why = str(e) if isinstance(e, (SshInstallError, ssh_identity.IdentityError, ValueError)) \
            else f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("ssh_provisioner: renewing the identity of %s failed (%s)", machine_id, why)
        try:
            await _merge_config(machine_id, renew_error=why[:300])
        except Exception:  # noqa: BLE001 — the row may be gone
            pass
    finally:
        _RENEWING.discard(machine_id)


async def renew(machine_id: str) -> float:
    """A new key and certificate for a running machine with its own identity (its container
    keeps running: the helper reads the files at each credential fetch). A trust anchor that
    changed (the CA rotated, or the Host's CA made again) is brought into the role's trust and
    the profile: the role trusts both anchors until the machine resolves with the new one, then
    only the new one. The end is recorded only once the profile resolves to the Hive's role with the
    new certificate; until then the next round tries again. The new certificate's end."""
    from core.carriers.ssh import config
    row = await _row(machine_id)
    cfg = config(row)
    if cfg["identity"] != IDENTITY or not row.get("version"):
        raise SshInstallError("renew: the machine has no identity of its own yet")
    name, host = row["name"], cfg["ssh_host"]
    acct = await _account(row["account_ref"])
    region = cfg["identity_region"] or _region(acct)
    cn = cfg["cn"] or ssh_identity.cn_for(name, machine_id)
    session = await _session(acct, region)
    account = str(acct.get("account_id") or "")
    ca, anchor = await asyncio.to_thread(ssh_identity.anchor_for, session, account, region)
    aws_profile = cfg["aws_profile"] or f"swarmai-hive-{name}"
    role_arn = cfg["role_arn"] or f"arn:aws:iam::{account}:role/{role_name(name)}"
    _check_arns(anchor, role_arn, cfg["profile_arn"])
    moved = anchor != cfg["anchor_arn"]
    if moved:                                  # both anchors until the machine works with the new one
        both = ssh_identity.trust_policy(anchor, cn)
        if cfg["anchor_arn"]:
            both["Statement"].append(dict(ssh_identity.roles_anywhere_statement(cfg["anchor_arn"], cn),
                                          Sid="RolesAnywherePrevious"))
        await _set_role_trust(acct, region, name, both)
    tag = _tag(str(row["version"]))
    profile = (aws_profile.removeprefix("swarmai-hive-"), anchor, cfg["profile_arn"], role_arn, region) if moved else ()
    not_after = await _new_certificate(host, tag, ca, cn, profile)
    await _check_identity(host, tag, aws_profile, name)
    if moved:
        await _set_role_trust(acct, region, name, ssh_identity.trust_policy(anchor, cn))
    await _merge_config(machine_id, not_after=not_after, anchor_arn=anchor, renew_error=None)
    logger.info("ssh_provisioner: %s has a new identity certificate", name)
    return not_after


async def remove(machine_id: str) -> dict[str, Any]:
    """Remove the Hive from the machine (container, images, environment file, its identity,
    the added profile), its Roles Anywhere profile and its role, then its row. The trust
    anchor and the Host's CA stay for the other machines. The Hive's data stays on the
    machine. A machine that cannot be reached is still removed here; what was left on it is
    said."""
    from core.carriers.ssh import config
    row = await _row(machine_id)
    left: list[str] = []
    cfg: dict[str, Any] = {}
    try:
        cfg = config(row)
        await _run(cfg["ssh_host"], _bash(REMOVE_SH), timeout=120, step="remove")
        # its own block, and the one a move writes (left by a move that did not finish)
        ids = {row["name"], f"{row['name']}-ra", str(cfg.get("aws_profile") or "").removeprefix("swarmai-hive-")} - {""}
        for profile_id in sorted(ids):
            await _run(cfg["ssh_host"], _bash(UNPROFILE_SH, profile_id), step="remove profile")
    except Exception as e:  # noqa: BLE001 — unreachable: say what may be left
        left.append(f"on the machine: the container, its identity and the swarmai-hive-{row['name']} profile "
                    f"({str(e)[:160]})")
    try:
        acct = await _account(row["account_ref"])
        if cfg.get("profile_arn"):
            region = cfg.get("identity_region") or _region(acct)
            session = await _session(acct, region)
            await asyncio.to_thread(ssh_identity.delete_profile, session, region, cfg["profile_arn"])
    except Exception as e:  # noqa: BLE001
        left.append(f"in AWS: the Roles Anywhere profile swarmai-hive-{row['name']} ({str(e)[:160]})")
    try:
        acct = await _account(row["account_ref"])
        await _delete_role(acct, acct.get("default_region") or "us-east-1", row["name"])
    except Exception as e:  # noqa: BLE001
        left.append(f"in AWS: the role {role_name(row['name'])} ({str(e)[:160]})")
    ssh_tunnel.stop(machine_id)
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as conn:
        await conn.execute("DELETE FROM hive_instances WHERE id = ? AND carrier = ?", (machine_id, CARRIER))
        await conn.commit()
    return {"deleted": True, "left": left, "data_kept": "~/.swarmai-hive/data on the machine"}


async def set_options(machine_id: str, general_tasks: Optional[bool] = None,
                      reaches_private: Optional[bool] = None, cert_renew_hint: Optional[str] = None) -> dict[str, Any]:
    """Change the settings that take effect without a reinstall."""
    keys: dict[str, Any] = {}
    if general_tasks is not None:
        keys["general_tasks"] = bool(general_tasks)
    if reaches_private is not None:
        keys["reaches_private"] = bool(reaches_private)
    if cert_renew_hint is not None:
        if not _HINT_RE.fullmatch(cert_renew_hint):
            raise ValueError("the certificate hint is one short line of plain text")
        keys["cert_renew_hint"] = cert_renew_hint
    return await _merge_config(machine_id, **keys)
