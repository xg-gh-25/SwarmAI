"""A small Hive store for the SSH-machine tests (design 2026-11-24-swarm-out-ssh-machine):
the ``hive_accounts`` / ``hive_instances`` tables with every column the migrations give them
(through v23), one SwarmAI account row, and helpers to add an EC2 Hive, an Elastic Workers row
and an SSH machine. Points ``database.db`` and ``core.swarm_out`` at it."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ACCOUNTS = """CREATE TABLE hive_accounts (id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '', auth_method TEXT NOT NULL DEFAULT 'access_keys',
  auth_config TEXT NOT NULL DEFAULT '{}', default_region TEXT NOT NULL DEFAULT 'us-east-1',
  created_at TEXT NOT NULL, verified_at TEXT)"""
INSTANCES = """CREATE TABLE hive_instances (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  owner_name TEXT, hive_type TEXT DEFAULT 'shared', account_ref TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT 'us-east-1', instance_type TEXT NOT NULL DEFAULT 'm7g.xlarge',
  ec2_instance_id TEXT, ec2_public_ip TEXT, elastic_ip_alloc_id TEXT, security_group_id TEXT,
  iam_role_name TEXT, iam_instance_profile_arn TEXT, cloudfront_dist_id TEXT, cloudfront_domain TEXT,
  s3_bucket TEXT, ssh_key_name TEXT, auth_user TEXT DEFAULT 'admin', auth_password TEXT,
  status TEXT NOT NULL DEFAULT 'pending', version TEXT, error_message TEXT, seed_data TEXT,
  shared_content TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, custom_ami_id TEXT,
  origin_secret TEXT, carrier TEXT NOT NULL DEFAULT 'ec2', runtime_arn TEXT, idle_stop_minutes INTEGER, carrier_config TEXT)"""

ACCOUNT = "123456789012"
BUCKET = "swarmai-hive-9012-us-east-1"


def make(tmp_path: Path, monkeypatch) -> Path:
    import database
    from core import swarm_out
    db = tmp_path / "hive.db"
    with sqlite3.connect(db) as c:
        c.execute(ACCOUNTS)
        c.execute(INSTANCES)
        c.execute("INSERT INTO hive_accounts (id, account_id, label, auth_method, created_at) "
                  "VALUES ('swarmai', ?, 'SwarmAI account', 'swarmai', '2026-11-24')", (ACCOUNT,))
    monkeypatch.setattr(database.db, "db_path", db)
    monkeypatch.setattr(swarm_out, "_db_path", lambda: str(db))
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: _state(tmp_path))
    return db


def _state(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    return d


def add(db: Path, hid: str, name: str, carrier: str = "ec2", status: str = "running", **extra) -> None:
    row = {"id": hid, "name": name, "account_ref": "swarmai", "status": status, "carrier": carrier,
           "created_at": "2026-11-24T00:00:00", "updated_at": "2026-11-24T00:00:00",
           "s3_bucket": BUCKET, "auth_user": "admin", "auth_password": "pw-" + hid, "version": "2.5.5"}  # pragma: allowlist secret  (a test value, not a credential)
    if carrier == "ec2":
        row.update(cloudfront_domain=f"{name}.cloudfront.net", ec2_instance_id=f"i-{hid}")
    row.update(extra)
    if isinstance(row.get("carrier_config"), dict):
        row["carrier_config"] = json.dumps(row["carrier_config"])
    with sqlite3.connect(db) as c:
        c.execute(f"INSERT INTO hive_instances ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                  tuple(row.values()))


def ssh_config(local_port: int = 28411, **over) -> dict:
    cfg = {"ssh_host": "dev-dsk-test.example.com", "remote_port": 18321, "local_port": local_port,
           "source_profile": "swarmai", "reaches_private": True, "general_tasks": True,
           "mounts": ["/apollo:ro", "/workplace"], "cert_renew_hint": "mwinit -o"}
    cfg.update(over)
    return cfg


def status_of(db: Path, hid: str) -> tuple:
    with sqlite3.connect(db) as c:
        return c.execute("SELECT status, error_message, version, updated_at FROM hive_instances WHERE id = ?",
                         (hid,)).fetchone()
