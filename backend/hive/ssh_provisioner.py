"""Install, update and remove the Hive on an SSH machine (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §5.3-§5.4, decisions D2, D4, D7).

Everything runs from the Host over SSH; the person only gives the machine's SSH host and the
AWS profile on it that reaches the SwarmAI account. A machine is a ``hive_instances`` row with
``carrier = 'ssh'`` (core/carriers/ssh), whose ``status`` goes ``provisioning → running``, or
``error`` with ``error_message`` saying which step failed and why. Every step can be run again
(``retry``): an image already built, a role or profile already there, is taken as it is.

Steps of ``add`` / ``retry`` (``_install``):

1. **preflight** (one ssh call, read-only): the machine answers in BatchMode, ``docker`` runs
   for this user, the home's disk has room, the Hive's port is free; its uid, gid, user, home,
   architecture, cores and memory are read for the image and the panel.
2. **image**: the Host takes the release package of its own version from the account's bucket,
   checks it against the ``checksums.txt`` beside it (as an EC2 Hive's first boot does), and
   sends it with the Dockerfile (hive/ssh_image) to the machine, which builds
   ``swarmai-hive-ssh:<version>``. The package is root's inside the image: the Hive's own gate
   code cannot be changed by a task running as the same uid.
3. **identity**: the person's ``source_profile`` is resolved inside the image; its role's ARN
   is what the Hive's role trusts.
4. **role** (D4): ``SwarmAI-Hive-<name>`` with the EC2 Hive's policy and its ``HiveName`` tag
   (it reads release packages, writes only its own ``bridge/<name>/`` asks and closed marks,
   reads only its own answers, calls Bedrock), trusting that role.
5. **profile**: one new profile ``swarmai-hive-<name>`` in the machine's ``~/.aws/config``
   (``role_arn`` + ``source_profile``), between marker lines; nothing else in the file is
   changed. Checked inside the image: the ARN must be the Hive's role.
6. **start**: the environment file (mode 600, sent on ssh's stdin, never on a command line)
   and the container (``--restart unless-stopped``, the machine's network, the person's
   ``$HOME`` and the configured mounts), then ``/health`` through the tunnel.

``update`` (D7) builds the new version's image, stops the old container, starts the new one and
waits for ``/health``; when the new one does not come up, the old one is started again. While
it runs, ``updated_at`` is refreshed every minute, so ``release_stale_updates`` never takes it
for an EC2 update stopped in the middle. ``remove`` deletes the container, the images, the role
and the added profile, and the row; the Hive's data (``~/.swarmai-hive/data``) stays on the
machine.

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
PROFILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
MOUNT_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,200}(:ro)?$")
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_ROLE_ARN_RE = re.compile(r"^arn:aws:sts::(\d{12}):assumed-role/([A-Za-z0-9+=,.@_-]{1,64})/")
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


async def _merge_config(machine_id: str, **keys: Any) -> dict[str, Any]:
    """Set *keys* in the machine's ``carrier_config``, reading it under the write lock: the
    install, an update and the card's switches each change only their own keys, so a switch
    turned while an image builds is never written back over. The merged settings."""
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

def check_settings(ssh_host: Any, source_profile: Any = "", mounts: Any = None, cert_renew_hint: Any = "",
                   remote_port: Any = 18321) -> None:
    """ValueError for anything that may not reach the machine's shell or its config file. An
    empty *source_profile* and *mounts* None mean the install finds them on the machine."""
    if not ssh_tunnel.valid_host(ssh_host):
        raise ValueError("not an SSH host (letters, digits, dots and hyphens, optionally user@)")
    if not isinstance(source_profile, str) or (source_profile and not PROFILE_RE.fullmatch(source_profile)):
        raise ValueError("not an AWS profile name")
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
#: at most this many of the machine's profiles are asked for their identity on an install
MAX_PROFILE_TRIES = 8


def _found_profiles(facts: dict[str, str]) -> list[str]:
    """The machine's AWS profile names (its preflight's ``profiles``) that may be used as the
    source of the Hive's role: of a profile name's shape, not one SwarmAI added
    (``swarmai-hive-*``), in name order."""
    names = {n.strip() for n in (facts.get("profiles") or "").split(",")}
    return sorted(n for n in names if n and PROFILE_RE.fullmatch(n) and not n.startswith("swarmai-hive-"))


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
               step: str = "") -> str:
    """Run *remote* (a fixed command line, every value in it quoted) on the machine with
    *data* on stdin; its stdout. SshInstallError with the last line it printed on failure,
    or when it ran longer than *timeout* (it is then killed)."""
    proc = await asyncio.create_subprocess_exec(*_ssh_cmd(host, remote), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(data or b""), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise SshInstallError(f"{step}: did not finish within {timeout:.0f}s") from None
    if proc.returncode != 0:
        lines = [ln for ln in (err or out).decode(errors="replace").splitlines() if ln.strip()
                 and "post-quantum" not in ln and "store now, decrypt later" not in ln and "pq.html" not in ln]
        why = lines[-1].strip()[:300] if lines else f"exit code {proc.returncode}"
        raise SshInstallError(f"{step}: {why}")
    return out.decode(errors="replace")


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
if docker info >/dev/null 2>&1; then echo "docker=ok"; else echo "docker=no"; fi
if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then echo "port=taken"; else echo "port=free"; fi
if docker inspect swarmai-hive >/dev/null 2>&1; then echo "hive_container=yes"; else echo "hive_container=no"; fi
# what the Host chooses from (hive/ssh_provisioner._found_profiles, _found_mounts)
profiles="$( { sed -n -e 's/^\[profile \([^]]*\)\][[:space:]]*$/\1/p' -e 's/^\[default\][[:space:]]*$/default/p' "$home/.aws/config"
  sed -n 's/^\[\([^]]*\)\][[:space:]]*$/\1/p' "$home/.aws/credentials"; } 2>/dev/null | sort -u | paste -sd, - )"
echo "profiles=$profiles"
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

PROFILE_SH = r'''
set -eu
name="$1"; role="$2"; source="$3"; region="$4"
cfg="$HOME/.aws/config"; mkdir -p "$HOME/.aws"; chmod 700 "$HOME/.aws"; touch "$cfg"; chmod 600 "$cfg"
begin="# swarmai-hive $name begin (written by SwarmAI; removed with the machine)"
if grep -qF "$begin" "$cfg"; then echo "profile=kept"; exit 0; fi
cp -p "$cfg" "$cfg.swarmai-bak.$(date +%s)"
printf '\n%s\n[profile swarmai-hive-%s]\nrole_arn = %s\nsource_profile = %s\nregion = %s\n# swarmai-hive %s end\n' \
  "$begin" "$name" "$role" "$source" "$region" "$name" >> "$cfg"
echo "profile=added"
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
  -v "$HOME:$HOME" $mounts --env-file "$env" -e HOME="$HOME" \
  "swarmai-hive-ssh:$ver" >/dev/null
'''

START_SH = r'''
set -eu
ver="$1"; shift
''' + _ENV_IN + r'''
docker rm -f swarmai-hive >/dev/null 2>&1 || true
''' + _RUN_HIVE + r'''
echo started
'''

SWAP_SH = r'''
set -eu
ver="$1"; shift
''' + _ENV_IN + r'''
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
echo removed
'''


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


def _context(package: bytes) -> bytes:
    """The image's build context as a tar: the Dockerfile and the checked package."""
    from hive.ssh_image import DOCKERFILE
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, body in (("Dockerfile", DOCKERFILE.encode()), ("swarmai-hive.tar.gz", package)):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(body), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


# -- the role (D4) --------------------------------------------------------------------------

def role_name(name: str) -> str:
    return f"SwarmAI-Hive-{name}"


def trust_policy(role_arn: str) -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"AWS": role_arn}, "Action": ["sts:AssumeRole", "sts:TagSession"]}]}


async def _ensure_role(acct: dict[str, Any], region: str, name: str, trusted_role_arn: str) -> str:
    """The Hive's role, created or brought up to date: the EC2 Hive's inline policy and
    ``HiveName`` tag (hive/provisioner._put_hive_role_policy), trusting *trusted_role_arn*."""
    from hive.provisioner import MANAGED_TAG, _boundary_arn, _put_hive_role_policy
    session = await _session(acct, region)
    rname = role_name(name)

    def ensure() -> str:
        iam = session.client("iam")
        doc = json.dumps(trust_policy(trusted_role_arn))
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


async def _iam_role_arn(acct: dict[str, Any], region: str, assumed_arn: str) -> str:
    """The IAM role ARN (with its path) of an assumed-role session ARN."""
    m = _ROLE_ARN_RE.match(assumed_arn)
    if not m:
        raise SshInstallError(f"identity: the profile is not a role session ({assumed_arn[:80]})")
    session = await _session(acct, region)
    return await asyncio.to_thread(lambda: session.client("iam").get_role(RoleName=m.group(2))["Role"]["Arn"])


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
        "AWS_PROFILE": f"swarmai-hive-{row['name']}",
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

    facts = _parse_kv(await _run(host, _bash(PREFLIGHT_SH, str(cfg["remote_port"])), step="preflight"))
    if facts.get("docker") != "ok":
        raise SshInstallError("preflight: docker does not run for this user on the machine")
    if facts.get("port") == "taken" and facts.get("hive_container") != "yes":
        raise SshInstallError(f"preflight: port {cfg['remote_port']} is taken on the machine")
    try:
        if int(facts.get("disk_free_gb") or 0) < 5:
            raise SshInstallError("preflight: less than 5 GB free in the home directory")
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
    await _run(host, _bash(BUILD_SH, version, str(uid), str(gid), user, home), _context(package),
               BUILD_TIMEOUT_S, "image")

    account = str(acct_row.get("account_id"))
    if cfg["source_profile"]:
        out = await _run(host, _bash(IDENTITY_SH, version, cfg["source_profile"]), timeout=120, step="identity")
        source_arn = _arn(out)
        m = _ROLE_ARN_RE.match(source_arn)
        if not m or m.group(1) != account or m.group(2).startswith("SwarmAI-Hive-"):
            raise SshInstallError(f"identity: profile {cfg['source_profile']} is not a role of the SwarmAI account "
                                  f"{account} other than a Hive's own ({source_arn[:80]})")
    else:                                      # added without one: the profile that reaches the account
        profile, source_arn, tried = await _find_profile(host, version, _found_profiles(facts), account)
        if not profile:
            raise SshInstallError(f"identity: no AWS profile on the machine reaches the SwarmAI account {account} "
                                  f"(tried: {', '.join(tried) or 'none found in ~/.aws'})")
        cfg["source_profile"] = profile
        await _merge_config(machine_id, source_profile=profile)
    trusted = await _iam_role_arn(acct_row, region, str(source_arn))
    hive_role = await _ensure_role(acct_row, region, name, trusted)
    await _set(machine_id, iam_role_name=role_name(name))
    await _run(host, _bash(PROFILE_SH, name, hive_role, cfg["source_profile"], region), step="profile")
    # IAM is eventually consistent: a new role or trust can take a few seconds to be assumable
    got: Any = ""
    for _ in range(6):
        out = await _retry_identity(host, version, name)
        try:
            got = json.loads(out.strip().splitlines()[-1]) if out.strip() else ""
        except ValueError:
            got = ""
        if isinstance(got, str) and f":assumed-role/{role_name(name)}/" in got:
            break
        await asyncio.sleep(5)
    else:
        raise SshInstallError(f"profile check: swarmai-hive-{name} does not resolve to {role_name(name)} "
                              f"({str(got)[:80] or 'no answer'})")

    password = row.get("auth_password") or secrets.token_urlsafe(24)
    user_name = row.get("auth_user") or "admin"
    await _set(machine_id, auth_user=user_name, auth_password=password)
    row.update(auth_user=user_name, auth_password=password)
    pass_hash = await asyncio.to_thread(_hash, password)
    await _run(host, _bash(START_SH, version, *cfg["mounts"]), env_file(row, cfg, pass_hash, region),
               timeout=120, step="start")
    await _health(machine_id, version=version)
    await _set(machine_id, status="running", version=version, error_message=None)
    logger.info("ssh_provisioner: %s runs the Hive v%s", name, version)


def _arn(out: str) -> str:
    """The ARN the identity script printed last (a JSON string), or ''."""
    try:
        got = json.loads(out.strip().splitlines()[-1]) if out.strip() else ""
    except ValueError:
        return ""
    return got if isinstance(got, str) else ""


async def _find_profile(host: str, version: str, profiles: list[str], account: str) -> tuple[str, str, list[str]]:
    """The first of *profiles* (at most MAX_PROFILE_TRIES) whose identity in the container is a
    role of *account* other than a Hive's own narrow role (``SwarmAI-Hive-*``: a new Hive's role
    trusting it would let that Hive's tasks assume this one): (profile, its ARN, the profiles
    tried); ('', '', tried) when none is."""
    tried: list[str] = []
    for profile in profiles[:MAX_PROFILE_TRIES]:
        tried.append(profile)
        try:
            arn = _arn(await _run(host, _bash(IDENTITY_SH, version, profile), timeout=120, step="identity"))
        except SshInstallError as e:           # this profile cannot sign in: the next one
            logger.info("ssh_provisioner: profile %s gives no identity (%s)", profile, str(e)[:120])
            continue
        m = _ROLE_ARN_RE.match(arn)
        if m and m.group(1) == account and not m.group(2).startswith("SwarmAI-Hive-"):
            return profile, arn, tried
    return "", "", tried


async def _retry_identity(host: str, version: str, name: str) -> str:
    try:
        return await _run(host, _bash(IDENTITY_SH, version, f"swarmai-hive-{name}"), timeout=120, step="profile check")
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
        why = str(e) if isinstance(e, (SshInstallError, ValueError)) else f"{type(e).__name__}: {str(e)[:200]}"
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


async def add(ssh_host: str, source_profile: str = "", name: Optional[str] = None, mounts: Optional[tuple] = None,
              cert_renew_hint: str = "", reaches_private: bool = True, general_tasks: bool = True,
              remote_port: int = 18321) -> dict[str, Any]:
    """Record a new SSH machine (``provisioning``) and install it in the background.
    ValueError for unusable settings; LookupError when the SwarmAI account is not connected;
    FileExistsError for a name already taken. Only *ssh_host* is needed: an empty
    *source_profile* and *mounts* None are found on the machine by the install (and the
    mounts again at each update); given ones are kept as given."""
    import uuid

    import aiosqlite
    from hive import accounts
    check_settings(ssh_host, source_profile, None if mounts is None else list(mounts), cert_renew_hint, remote_port)
    name = name or default_name(ssh_host)
    if not NAME_RE.fullmatch(name):
        raise ValueError("a name is lower-case letters, digits and hyphens, starting with a letter")
    if accounts.swarmai_account() is None:
        raise LookupError("connect the SwarmAI account first (Settings)")
    await accounts.ensure_swarmai_row(Path(_db_path()))
    cfg = {"ssh_host": ssh_host, "remote_port": int(remote_port), "local_port": await _free_local_port(),
           "source_profile": source_profile, "reaches_private": bool(reaches_private),
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
    at that version; the old container restored when the new one does not come up. Ends
    'running' (either version) or 'error' with why."""
    from core.carriers.ssh import config
    stop = asyncio.Event()
    beat = asyncio.get_running_loop().create_task(_heartbeat(machine_id, stop))
    swapped = False
    try:
        if not VERSION_RE.fullmatch(version):
            raise SshInstallError("update: unexpected version")
        row = await _row(machine_id)
        cfg = config(row)
        facts = _parse_kv(await _run(cfg["ssh_host"], _bash(PREFLIGHT_SH, str(cfg["remote_port"])), step="preflight"))
        uid, gid, user, home = _who(facts)
        if cfg["mounts_auto"]:                 # a directory added to the machine since is mounted now
            cfg["mounts"] = _found_mounts(facts, home)
        acct = await _account(row["account_ref"])
        region = _region(acct)
        package = await _package(acct, region, row["s3_bucket"], version)
        await _run(cfg["ssh_host"], _bash(BUILD_SH, version, str(uid), str(gid), user, home),
                   _context(package), BUILD_TIMEOUT_S, "image")
        # the environment again, from the Host (on stdin): nothing found on the machine is used
        if not row.get("auth_password"):
            raise SshInstallError("update: the machine has no password recorded; retry its install")
        cfg["_home"] = home
        pass_hash = await asyncio.to_thread(_hash, row["auth_password"])
        await _run(cfg["ssh_host"], _bash(SWAP_SH, version, *cfg["mounts"]), env_file(row, cfg, pass_hash, region),
                   timeout=180, step="update")
        swapped = True
        await _health(machine_id, version=version)
        await _run(cfg["ssh_host"], _bash(COMMIT_SH, version), timeout=120, step="update")
        if cfg["mounts_auto"]:                 # recorded once the container with them is the one running
            await _merge_config(machine_id, mounts=cfg["mounts"])
        await _set(machine_id, status="running", version=version, error_message=None)
        logger.info("ssh_provisioner: %s updated to v%s", row["name"], version)
    except Exception as e:  # noqa: BLE001 — restore what ran before, say why
        why = str(e)[:400]
        # before the swap the old container never stopped: the machine still runs it
        still_runs = not swapped
        if swapped:
            try:
                row = await _row(machine_id)
                await _run(config(row)["ssh_host"], _bash(ROLLBACK_SH), timeout=120, step="rollback")
                why += "; the previous version was started again"
                still_runs = True
            except Exception as e2:  # noqa: BLE001
                why += f"; restoring the previous version failed too ({str(e2)[:120]})"
        logger.warning("ssh_provisioner: update of %s failed (%s)", machine_id, why)
        try:
            await _set(machine_id, status="running" if still_runs else "error",
                       error_message=f"update to v{version} failed: {why}"[:500])
        except Exception:  # noqa: BLE001
            pass
    finally:
        stop.set()
        await beat


async def remove(machine_id: str) -> dict[str, Any]:
    """Remove the Hive from the machine (container, images, environment file, the added
    profile), its role, then its row. The Hive's data stays on the machine. A machine that
    cannot be reached is still removed here; what was left on it is said."""
    from core.carriers.ssh import config
    row = await _row(machine_id)
    left: list[str] = []
    try:
        cfg = config(row)
        await _run(cfg["ssh_host"], _bash(REMOVE_SH), timeout=120, step="remove")
        await _run(cfg["ssh_host"], _bash(UNPROFILE_SH, row["name"]), step="remove profile")
    except Exception as e:  # noqa: BLE001 — unreachable: say what may be left
        left.append(f"on the machine: the container and the swarmai-hive-{row['name']} profile ({str(e)[:160]})")
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
