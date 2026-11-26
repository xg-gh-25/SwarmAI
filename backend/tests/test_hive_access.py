"""Tests for hive/access.py and the access list scripts of hive/user_data.py.

Design docs/design/2026-11-26-hive-access-credentials.md: the Host's own key plus one key
per person, in one list file both auth layers read; ``apply_access`` is its only writer,
under the update claim; an existing Hive moves to it on its next update.

The Host side runs against a real SQLite store and a fake provisioner that records every
SSM script (the list each one carries is decoded from it). The on-instance Python
(editor, list check) runs in-process with ``os.chown`` and the ``swarm`` group stubbed;
a Caddyfile it writes is checked with ``caddy validate`` when a caddy binary is found
(``CADDY_BIN`` or PATH).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import grp
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hive import access
from hive import user_data as ud

HASH_A = "$2b$14$" + "a" * 53
HASH_B = "$2b$14$" + "b" * 53
#: stand-in values for a Host key and an old shared password
OLD_KEY = "-".join(["old", "host", "key"])
SHARED = "-".join(["tiger", "cloud", "seven", "lamp"])


# ── fixtures ─────────────────────────────────────────────────────────────────

def _store(tmp_path: Path, **row) -> Path:
    db = tmp_path / "data.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE hive_instances (id TEXT PRIMARY KEY, name TEXT, carrier TEXT,
        ec2_instance_id TEXT, account_ref TEXT, region TEXT, auth_user TEXT, auth_password TEXT,
        status TEXT, cloudfront_domain TEXT)""")
    conn.execute("""CREATE TABLE hive_access (id TEXT PRIMARY KEY, hive_id TEXT NOT NULL,
        user TEXT NOT NULL, label TEXT, pass_hash TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, UNIQUE (hive_id, user))""")
    values = dict(id="h1", name="demo", carrier="ec2", ec2_instance_id="i-1", account_ref="acc",
                  region="us-east-1", auth_user="host", auth_password=OLD_KEY,
                  status="running", cloudfront_domain="d1.cloudfront.net")
    values.update(row)
    conn.execute(f"INSERT INTO hive_instances ({', '.join(values)}) VALUES "
                 f"({', '.join('?' for _ in values)})", tuple(values.values()))
    conn.commit()
    conn.close()
    return db


def _hive(db: Path) -> dict:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM hive_instances WHERE id='h1'").fetchone())
    conn.close()
    return row


def _access_rows(db: Path) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM hive_access ORDER BY user")]
    conn.close()
    return rows


class FakeProvisioner:
    """What apply_access uses of HiveProvisioner. ``results``: the SSM status of each
    script in turn (default Success); ``claim_fails``: the update claim is refused."""

    def __init__(self, db: Path, results=None, claim_fails: bool = False):
        self.db_path = db
        self.scripts: list[str] = []
        self.results = list(results or [])
        self.claim_fails = claim_fails

    async def claim_update(self, hive_id):
        # like the real claim: running -> updating, refused for any other status
        if self.claim_fails or _hive(self.db_path)["status"] != "running":
            raise RuntimeError("not running")
        await self._update_instance(hive_id, status="updating")

    async def _update_instance(self, hive_id, **fields):
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"UPDATE hive_instances SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                     (*fields.values(), hive_id))
        conn.commit()
        conn.close()

    async def _get_account(self, ref):
        return {"id": ref}

    async def _session(self, account, region, purpose):
        return SimpleNamespace(purpose=purpose)

    def _run_ssm_script(self, session, region, ec2_id, script, comment):
        self.scripts.append(script)
        status = self.results.pop(0) if self.results else "Success"
        out = {"Success": "Access list applied: 2 keys",
               "Exit3": "ERROR: this Hive has no access list yet; update it first"}
        return ("Failed" if status == "Exit3" else status), out.get(status, "ERROR: caddy did not restart")


def _list_of(script: str) -> list[tuple[str, str]]:
    m = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64 -d > \"\$NEW\"", script)
    assert m, "the script carries no list"
    return [tuple(line.split(" ")) for line in base64.b64decode(m.group(1)).decode().splitlines()]


@pytest.fixture(autouse=True)
def _cheap_bcrypt(monkeypatch):
    """bcrypt at cost 4 for speed; the list still checks the hash shape."""
    import bcrypt
    monkeypatch.setattr(access, "caddy_hash_password",
                        lambda pw: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode())


def _verify(password: str, hashed: str) -> bool:
    import bcrypt
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ── Host side ────────────────────────────────────────────────────────────────

def test_add_access_validates_and_stores_hash_only(tmp_path):
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    for bad in ("", "Alice", "a b", "host", "host-b", "admin", "x" * 33, "a\nb", None):
        with pytest.raises(access.AccessError) as e:
            asyncio.run(access.add_access(prov, "h1", bad))
        assert e.value.status == 400
    assert prov.scripts == [] and _access_rows(db) == []

    got = asyncio.run(access.add_access(prov, "h1", "alice"))
    assert got["user"] == "alice" and got["url"] == "https://d1.cloudfront.net"
    assert len(got["password"]) >= 32
    rows = _access_rows(db)
    assert [r["user"] for r in rows] == ["alice"]
    assert got["password"] not in str(rows) and _verify(got["password"], rows[0]["pass_hash"])
    # the list applied: the Host key first, then alice; the Host key verifies against it
    applied = _list_of(prov.scripts[-1])
    assert [u for u, _ in applied] == ["host", "alice"]
    assert _verify(OLD_KEY, applied[0][1])
    assert _hive(db)["status"] == "running"  # the claim was given back

    listed = asyncio.run(access.list_access(str(db), "h1"))
    assert listed == [{"user": "alice", "label": "alice", "created_at": rows[0]["created_at"],
                       "legacy": False}]
    assert "pass_hash" not in str(listed) and rows[0]["pass_hash"] not in str(listed)

    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(prov, "h1", "alice"))
    assert e.value.status == 400


def test_add_access_failure_keeps_no_row(tmp_path):
    db = _store(tmp_path)
    prov = FakeProvisioner(db, results=["Failed"])
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(prov, "h1", "alice"))
    assert e.value.status == 502
    assert _access_rows(db) == []
    assert _hive(db)["status"] == "running"

    # any other failure of the apply (the account, SSM raising) removes the row too
    async def no_session(*a, **k):
        raise RuntimeError("credentials expired")
    prov._session = no_session
    with pytest.raises(RuntimeError):
        asyncio.run(access.add_access(prov, "h1", "alice"))
    assert _access_rows(db) == [] and _hive(db)["status"] == "running"


def test_a_restored_host_is_told_to_rotate(tmp_path):
    db = _store(tmp_path, auth_password=None)
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(FakeProvisioner(db), "h1", "alice"))
    assert e.value.status == 409 and "rotate the Host key" in str(e.value)


def test_revoke_applies_list_without_entry(tmp_path):
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    asyncio.run(access.add_access(prov, "h1", "alice"))
    asyncio.run(access.add_access(prov, "h1", "bob"))
    asyncio.run(access.revoke_access(prov, "h1", "alice"))
    assert [u for u, _ in _list_of(prov.scripts[-1])] == ["host", "bob"]
    assert [r["user"] for r in _access_rows(db)] == ["bob"]
    # revoking again applies the list again (a repeat converges, never a 404)
    before = len(prov.scripts)
    asyncio.run(access.revoke_access(prov, "h1", "alice"))
    assert len(prov.scripts) == before + 1 and [u for u, _ in _list_of(prov.scripts[-1])] == ["host", "bob"]


@pytest.mark.parametrize("failure", ["Failed", "busy"])
def test_a_failed_revoke_keeps_the_key_listed_and_can_be_redone(tmp_path, failure):
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    asyncio.run(access.add_access(prov, "h1", "bob"))
    if failure == "busy":
        prov.claim_fails = True
    else:
        prov.results = ["Failed"]
    with pytest.raises(access.AccessError):
        asyncio.run(access.revoke_access(prov, "h1", "bob"))
    # the Hive may still accept bob, so the Host still shows him
    assert [r["user"] for r in _access_rows(db)] == ["bob"]
    prov.claim_fails = False
    asyncio.run(access.revoke_access(prov, "h1", "bob"))
    assert [u for u, _ in _list_of(prov.scripts[-1])] == ["host"]
    assert _access_rows(db) == []


def test_apply_reads_the_host_key_after_the_claim(tmp_path):
    """A rotation that finished while an add waited for the claim is not written back over:
    the add's list carries the Host key the store has once the add holds the claim."""
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    real_claim = prov.claim_update

    async def claim_after_a_rotation(hive_id):
        await prov._update_instance(hive_id, auth_user="host-b", auth_password="rotated-key")
        await real_claim(hive_id)
    prov.claim_update = claim_after_a_rotation
    asyncio.run(access.add_access(prov, "h1", "alice"))
    applied = _list_of(prov.scripts[-1])
    assert [u for u, _ in applied] == ["host-b", "alice"]
    assert _verify("rotated-key", applied[0][1])


def test_apply_refuses_when_claim_fails(tmp_path):
    db = _store(tmp_path, status="updating")
    prov = FakeProvisioner(db, claim_fails=True)
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(prov, "h1", "alice"))
    assert e.value.status == 409
    assert prov.scripts == []          # nothing sent to the Hive
    assert _access_rows(db) == []      # the row of the refused add is gone
    assert _hive(db)["status"] == "updating"  # the update's claim is left alone


def test_apply_refuses_unmigrated_hive(tmp_path):
    db = _store(tmp_path, auth_user="admin", auth_password=SHARED)
    prov = FakeProvisioner(db)
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(prov, "h1", "alice"))
    assert e.value.status == 409 and "update it first" in str(e.value)
    assert prov.scripts == []
    # a Hive whose box has no list yet (the script's exit 3) is refused the same way
    (tmp_path / "b").mkdir()
    db2 = _store(tmp_path / "b")
    prov2 = FakeProvisioner(db2, results=["Exit3"])
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.add_access(prov2, "h1", "alice"))
    assert e.value.status == 409 and "update it first" in str(e.value)


def test_only_ec2_rows_are_touched(tmp_path):
    for row in ({"carrier": "ssh", "ec2_instance_id": None}, {"carrier": "elastic", "ec2_instance_id": None},
                {"carrier": "ec2", "ec2_instance_id": None}):
        d = tmp_path / row["carrier"] / str(row["ec2_instance_id"])
        d.mkdir(parents=True)
        db = _store(d, **row, auth_user="admin", auth_password="pw")
        prov = FakeProvisioner(db)
        assert asyncio.run(access.migrate(prov, "h1")) is False
        for call in (lambda: access.apply_access(prov, "h1", ("host", "k"), claimed=True),
                     lambda: access.add_access(prov, "h1", "alice"),
                     lambda: access.rotate_host_key(prov, "h1")):
            with pytest.raises(access.AccessError) as e:
                asyncio.run(call())
            assert e.value.status == 409
        assert prov.scripts == []
        assert _hive(db)["auth_user"] == "admin" and _access_rows(db) == []


def test_migrate_keeps_admin_as_legacy_until_apply_succeeds(tmp_path):
    db = _store(tmp_path, auth_user="admin", auth_password=SHARED, status="updating")
    failing = FakeProvisioner(db, results=["Failed"])
    with pytest.raises(access.AccessError):
        asyncio.run(access.migrate(failing, "h1"))
    row = _hive(db)
    assert (row["auth_user"], row["auth_password"]) == ("admin", SHARED)

    prov = FakeProvisioner(db)
    assert asyncio.run(access.migrate(prov, "h1")) is True
    row = _hive(db)
    assert row["auth_user"] == "host" and row["auth_password"] != SHARED
    assert len(row["auth_password"]) >= 32
    assert row["status"] == "updating"  # the update holds the claim; migrate never gives it back
    legacy = _access_rows(db)
    assert [(r["user"], r["label"], r["legacy"]) for r in legacy] == [("admin", access.LEGACY_LABEL, 1)]
    assert _verify(SHARED, legacy[0]["pass_hash"])
    applied = _list_of(prov.scripts[-1])
    assert [u for u, _ in applied] == ["host", "admin"]
    assert _verify(row["auth_password"], applied[0][1])
    assert asyncio.run(access.migrate(prov, "h1")) is False  # once only


def test_rotate_host_key_two_phase(tmp_path, monkeypatch):
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    asyncio.run(access.add_access(prov, "h1", "alice"))
    checked = []

    async def works(row, user, key):
        checked.append((user, key, _hive(db)["auth_user"]))
        return True
    monkeypatch.setattr(access, "_key_works", works)
    before = len(prov.scripts)
    asyncio.run(access.rotate_host_key(prov, "h1"))
    first, second = prov.scripts[before:]
    row = _hive(db)
    assert row["auth_user"] == "host-b" and row["auth_password"] != OLD_KEY
    assert [u for u, _ in _list_of(first)] == ["host-b", "host", "alice"]
    assert _verify(row["auth_password"], _list_of(first)[0][1])
    assert _verify(OLD_KEY, _list_of(first)[1][1])
    # the new key was checked while the store still held the old one
    assert checked == [("host-b", row["auth_password"], "host")]
    assert [u for u, _ in _list_of(second)] == ["host-b", "alice"]
    assert "host-b" in second.split("COUNT=$(python3", 1)[1].split("\n", 1)[0]  # the Host user

    # a new key the Hive does not accept is never stored; the old one still works
    async def refused(row, user, key):
        return False
    monkeypatch.setattr(access, "_key_works", refused)
    old = _hive(db)["auth_password"]
    with pytest.raises(access.AccessError) as e:
        asyncio.run(access.rotate_host_key(prov, "h1"))
    assert e.value.status == 502 and _hive(db)["auth_password"] == old

    # a restored Host (no key) gets one in a single apply
    asyncio.run(prov._update_instance("h1", auth_password=None))
    before = len(prov.scripts)
    asyncio.run(access.rotate_host_key(prov, "h1"))
    assert len(prov.scripts) == before + 1 and _hive(db)["auth_password"]


# ── on-instance scripts ──────────────────────────────────────────────────────

def _run_py(source: str, argv: list[str], monkeypatch) -> tuple[str, object]:
    """Run on-instance Python in-process: chown recorded, the swarm group stubbed."""
    chowned = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowned.append((str(p), u, g)))
    monkeypatch.setattr(grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=4242))
    monkeypatch.setattr(sys, "argv", ["-c"] + argv)
    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            exec(compile(source, "<on-instance>", "exec"), {"__name__": "__main__"})
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


def _old_caddyfile(user: str = "admin", hashed: str = HASH_A) -> str:
    """The Caddyfile a Hive rendered before the access list: one inline credential."""
    caddy = ud.render_user_data(s3_bucket="b", version="1.0.0", auth_user="host", auth_hash=HASH_A,
                                region="us-east-1", origin_secret="s" * 40)
    caddy = caddy.split("cat > /etc/caddy/Caddyfile << 'CADDY'\n", 1)[1].split("\nCADDY\n", 1)[0] + "\n"
    return caddy.replace(ud.ACCESS_IMPORT_LINE, f"        {user} {hashed}\n")


def _caddy_validate(caddyfile: Path, listfile: Path) -> None:
    caddy = os.environ.get("CADDY_BIN") or shutil.which("caddy")
    if not caddy:
        return
    probe = caddyfile.with_suffix(".probe")
    probe.write_text(caddyfile.read_text().replace(ud.ACCESS_LIST_PATH, str(listfile))
                     .replace("/var/log/caddy/", str(caddyfile.parent) + "/"))
    r = subprocess.run([caddy, "validate", "--config", str(probe), "--adapter", "caddyfile"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]


def test_access_fix_editor_moves_inline_line_to_file(tmp_path, monkeypatch):
    caddyfile, listfile = tmp_path / "Caddyfile", tmp_path / "auth" / "hive-users"
    monkeypatch.setattr(ud, "ACCESS_LIST_DIR", str(listfile.parent))
    caddyfile.write_text(_old_caddyfile())
    source = ud.access_fix_editor_source().replace(repr(ud.ACCESS_LIST_DIR), repr(str(listfile.parent)))
    assert not re.search(r"__[A-Z_]+__", source)  # every slot filled

    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert (out, code) == ("changed\n", None)
    assert listfile.read_text() == f"admin {HASH_A}\n"
    assert oct(listfile.stat().st_mode & 0o777) == oct(0o640)
    assert (oct(listfile.parent.stat().st_mode & 0o777)) == oct(0o750)
    text = caddyfile.read_text()
    assert ud._ACCESS_BLOCK in text and HASH_A not in text
    _caddy_validate(caddyfile, listfile)

    # a second run: already on, nothing rewritten
    before = caddyfile.read_text()
    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert out == "on\n" and code in (None, 0) and caddyfile.read_text() == before
    # never a hash on the output
    assert HASH_A not in out


@pytest.mark.parametrize("shape", ["legacy-star", "two-lines", "nested"])
def test_access_fix_editor_leaves_unknown_shape(tmp_path, monkeypatch, shape):
    caddyfile, listfile = tmp_path / "Caddyfile", tmp_path / "auth" / "hive-users"
    text = _old_caddyfile()
    if shape == "legacy-star":
        text = text.replace("    basic_auth @protected {", "    basicauth * {")
    elif shape == "two-lines":
        text = text.replace(f"        admin {HASH_A}\n", f"        admin {HASH_A}\n        bob {HASH_B}\n")
    else:
        text = text.replace("    basic_auth @protected {", "    basic_auth @protected {\n    basic_auth @other {", 1)
    caddyfile.write_text(text)
    source = ud.access_fix_editor_source().replace(repr(ud.ACCESS_LIST_DIR), repr(str(listfile.parent)))
    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert out == "unknown\n" and code in (None, 0)
    assert caddyfile.read_text() == text and not listfile.exists()


def _check_source() -> str:
    return ud._ACCESS_APPLY_CHECK_TEMPLATE.replace("__LINE__", repr(ud.ACCESS_LINE_PATTERN))


@pytest.mark.parametrize("content,why", [
    ("", "no line for the Host"),
    (f"alice {HASH_A}\n", "no line for the Host"),
    (f"host {HASH_A}\nhost {HASH_B}\n", "malformed or repeated"),
    (f"host {HASH_A}\nBob {HASH_B}\n", "malformed or repeated"),
    (f"host not-a-hash\n", "malformed or repeated"),
    (f"host {HASH_A} extra\n", "malformed or repeated"),
])
def test_apply_script_rejects_bad_list(tmp_path, monkeypatch, content, why):
    new, env = tmp_path / "new", tmp_path / "hive-auth.env"
    new.write_text(content)
    env.write_text("HIVE_USER=admin\nHIVE_PASS_HASH=x\nOTHER=1\n")
    out, code = _run_py(_check_source(), [str(new), "host", str(env)], monkeypatch)
    assert isinstance(code, str) and why in code
    assert env.read_text() == "HIVE_USER=admin\nHIVE_PASS_HASH=x\nOTHER=1\n"  # untouched


def test_apply_script_accepts_a_good_list_and_sets_the_fallback(tmp_path, monkeypatch):
    new, env = tmp_path / "new", tmp_path / "hive-auth.env"
    new.write_text(f"host {HASH_A}\nalice {HASH_B}\n")
    env.write_text("HIVE_USER=admin\nHIVE_PASS_HASH=old\nOTHER=1\n")
    out, code = _run_py(_check_source(), [str(new), "host", str(env)], monkeypatch)
    assert (out, code) == ("2\n", None)
    assert env.read_text() == f"OTHER=1\nHIVE_USER=host\nHIVE_PASS_HASH={HASH_A}\n"


def test_scripts_print_no_secret():
    """The list reaches the Hive only base64-encoded and every echo of the scripts is a
    fixed message, a count or a reason: no hash, no key."""
    script = ud.access_apply_sh([("host", HASH_A), ("alice", HASH_B)], "host")
    fix = ud.access_list_fix_sh()
    for text in (script, fix):
        assert HASH_A not in text and HASH_B not in text
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("echo ") and "base64 -d" not in s:
                assert not re.search(r"\$(NEW|ACCESS_LIST\b|HASH|LIST_B64)", s), s
    # the only output of the check program is the count, of the editor on/changed/unknown
    assert "print(len(users))" in ud._ACCESS_APPLY_CHECK_TEMPLATE
    prints = re.findall(r"print\((.*)\)", ud._ACCESS_FIX_EDITOR_TEMPLATE)
    assert sorted(set(prints)) == ['"changed"', '"on"', '"unknown"']
    # a Hive without the list refuses with the exit code the Host maps to "update first"
    assert 'echo "ERROR: this Hive has no access list yet; update it first"\n  exit 3' in script


def _update(output: str, migrate):
    from tests.test_hive_hardening import _update_script
    from unittest.mock import patch
    with patch("hive.access.migrate", new=migrate):
        return asyncio.run(_update_script(ssm=("Success", output)))


def test_update_runs_the_access_step_before_the_install_and_migrates_after():
    """The access list step sits with the other Caddy steps, before the backup and the
    install (its failure stops the update with the Hive untouched); a healthy update whose
    step reports the list moves the Hive, one that does not leaves it."""
    calls = []

    async def migrate(prov, hive_id):
        calls.append(hive_id)
        return True
    script, update = _update("...\nAccess list: on\n=== Update to v1.2.3 verified ===", migrate)
    lines = [ln.strip() for ln in script.splitlines()]
    step = lines.index("# ── Caddy: one access list for both auth layers (hive/user_data.py) ──")
    binary = next(i for i, ln in enumerate(lines) if ln.startswith("# ── Caddy binary"))
    backup = lines.index("rsync -a --delete --exclude='backend/.venv' /opt/swarmai/ /opt/swarmai.bak/")
    assert binary < step < backup
    assert calls == ["inst-1"]
    assert update.call_args.kwargs.get("error_message") is None

    calls.clear()
    _update("...\nAccess list: off\n", migrate)
    assert calls == []


def test_a_failed_move_leaves_the_update_done_and_says_so():
    async def migrate(prov, hive_id):
        raise access.AccessError(502, "The Hive did not apply the access list (Failed): x")
    _, update = _update("Access list: on\n", migrate)
    last = update.call_args.kwargs
    assert last["status"] == "running" and last["version"] == "1.2.3"
    assert "one key per person failed" in last["error_message"]


def test_a_new_hives_caddyfile_validates_with_the_list(tmp_path):
    caddy = ud.render_user_data(s3_bucket="b", version="1.0.0", auth_user="host", auth_hash=HASH_A,
                                region="us-east-1", origin_secret="s" * 40, access=[("alice", HASH_B)])
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(caddy.split("cat > /etc/caddy/Caddyfile << 'CADDY'\n", 1)[1].split("\nCADDY\n", 1)[0] + "\n")
    listfile = tmp_path / "hive-users"
    listfile.write_text(ud.access_list_text([("host", HASH_A), ("alice", HASH_B)]))
    _caddy_validate(caddyfile, listfile)



# ── the admin API stays off with or without the origin guard ────────────────

def _caddyfile_of(script: str) -> str:
    return script.split("cat > /etc/caddy/Caddyfile << 'CADDY'\n", 1)[1].split("\nCADDY\n", 1)[0] + "\n"


def _admin_off(text: str) -> int:
    return len(re.findall(r"^\s*admin off\s*$", text, re.M))


def test_admin_off_without_the_guard_and_through_its_removal(tmp_path, monkeypatch):
    plain = _caddyfile_of(ud.render_user_data(s3_bucket="b", version="1.0.0", auth_user="host",
                                               auth_hash=HASH_A, region="us-east-1"))
    assert _admin_off(plain) == 1 and ud.ADMIN_OFF_GLOBAL in plain
    listfile = tmp_path / "hive-users"
    listfile.write_text(f"host {HASH_A}\n")
    for name, text in (("plain", plain),):
        f = tmp_path / name
        f.write_text(text)
        _caddy_validate(f, listfile)

    guarded = _caddyfile_of(ud.render_user_data(s3_bucket="b", version="1.0.0", auth_user="host",
                                                 auth_hash=HASH_A, region="us-east-1",
                                                 origin_secret="s" * 40))
    f = tmp_path / "Caddyfile"
    f.write_text(guarded)
    # removing the guard puts the admin-only global block back
    src = ud.origin_guard_editor_source()
    out, code = _run_py(src, ["remove", str(f)], monkeypatch)
    removed = f.read_text()
    assert "ORIGIN-GUARD" not in removed and _admin_off(removed) == 1 and ud.ADMIN_OFF_GLOBAL in removed
    _caddy_validate(f, listfile)
    # adding it again leaves one global block, the guard's, with the API off
    monkeypatch.setattr(sys, "stdin", io.StringIO("s" * 40))
    out, code = _run_py(src, ["apply", str(f)], monkeypatch)
    again = f.read_text()
    assert ud.ADMIN_OFF_GLOBAL not in again and _admin_off(again) == 1
    assert again.count("\n{\n") + again.startswith("{\n") == 1
    _caddy_validate(f, listfile)


@pytest.mark.parametrize("guarded", [True, False])
def test_the_access_step_turns_the_admin_api_off_on_an_old_hive(tmp_path, monkeypatch, guarded):
    secret = "s" * 40 if guarded else None
    old = _caddyfile_of(ud.render_user_data(s3_bucket="b", version="1.0.0", auth_user="host",
                                             auth_hash=HASH_A, region="us-east-1",
                                             origin_secret=secret))
    old = old.replace(ud.ACCESS_IMPORT_LINE, f"        admin {HASH_A}\n")
    old = re.sub(r"^\s*admin off\s*\n", "", old, flags=re.M).replace("{\n}\n", "")
    assert _admin_off(old) == 0
    caddyfile, listfile = tmp_path / "Caddyfile", tmp_path / "auth" / "hive-users"
    caddyfile.write_text(old)
    source = ud.access_fix_editor_source().replace(repr(ud.ACCESS_LIST_DIR), repr(str(listfile.parent)))
    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert out == "changed\n"
    new = caddyfile.read_text()
    assert _admin_off(new) == 1 and ud._ACCESS_BLOCK in new
    _caddy_validate(caddyfile, listfile)
    # a Hive that already imports the list but still has the API on gets it off too
    caddyfile.write_text(re.sub(r"^\s*admin off\s*\n", "", new, flags=re.M).replace("{\n}\n", ""))
    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert out == "changed\n" and _admin_off(caddyfile.read_text()) == 1
    out, code = _run_py(source, [str(caddyfile), str(listfile)], monkeypatch)
    assert out == "on\n"


def test_an_update_that_cannot_move_the_hive_says_so():
    async def migrate(prov, hive_id):
        raise AssertionError("not called")
    from tests.test_hive_hardening import _update_script
    from unittest.mock import patch
    with patch("hive.access.migrate", new=migrate):
        _, update = asyncio.run(_update_script(ssm=("Success", "Access list: off\n"), auth_user="admin"))
    assert "Replace the Hive" in update.call_args.kwargs["error_message"]



def test_no_other_access_change_runs_inside_a_revoke_or_a_rotation(tmp_path, monkeypatch):
    """A revoke holds the claim until its row is gone, a rotation across both of its
    applies: an add that tries to run in between is refused, so it can never write a list
    that still holds the revoked key or only the old Host key."""
    db = _store(tmp_path)
    prov = FakeProvisioner(db)
    asyncio.run(access.add_access(prov, "h1", "bob"))
    refused = []
    real_delete = access._delete_row

    async def delete_with_an_add_in_between(db_path, hive_id, user):
        if user == "bob":  # the revoke's own delete (the refused add deletes its row too)
            with pytest.raises(access.AccessError) as e:
                await access.add_access(prov, hive_id, "alice")
            refused.append(e.value.status)
        return await real_delete(db_path, hive_id, user)
    monkeypatch.setattr(access, "_delete_row", delete_with_an_add_in_between)
    asyncio.run(access.revoke_access(prov, "h1", "bob"))
    monkeypatch.setattr(access, "_delete_row", real_delete)
    assert refused == [409] and _access_rows(db) == []

    async def works_with_an_add_in_between(row, user, key):
        with pytest.raises(access.AccessError) as e:
            await access.add_access(prov, "h1", "carol")
        refused.append(e.value.status)
        return True
    monkeypatch.setattr(access, "_key_works", works_with_an_add_in_between)
    asyncio.run(access.rotate_host_key(prov, "h1"))
    assert refused == [409, 409]
    assert [u for u, _ in _list_of(prov.scripts[-1])] == ["host-b"]
    assert _hive(db)["status"] == "running"



def test_a_second_rotation_starts_from_the_first_ones_key(tmp_path, monkeypatch):
    """A double click: the second rotation read the row before the first one finished.
    Once it holds the claim it starts from the key the first one stored, so the list it
    applies keeps the stored key and the Host is never left with a key the Hive refuses."""
    db = _store(tmp_path)
    prov = FakeProvisioner(db)

    async def works(row, user, key):
        return True
    monkeypatch.setattr(access, "_key_works", works)
    real_claim = prov.claim_update
    ran_first = []

    async def first_rotation_finishes_before_this_claim(hive_id):
        if not ran_first:
            ran_first.append(1)
            prov.claim_update = real_claim
            await access.rotate_host_key(prov, hive_id)   # the first click, done
        await real_claim(hive_id)
    prov.claim_update = first_rotation_finishes_before_this_claim
    asyncio.run(access.rotate_host_key(prov, "h1"))       # the second click
    row = _hive(db)
    final = _list_of(prov.scripts[-1])
    assert [u for u, _ in final] == [row["auth_user"]]
    assert _verify(row["auth_password"], final[0][1])
    assert row["auth_user"] == "host"  # host -> host-b (first) -> host (second)
