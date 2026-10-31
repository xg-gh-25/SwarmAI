"""Finding the AWS accounts this machine can reach (core/aws_detect.py; design
2026-10-31-swarmai-aws-account §6.1 ②).

Questions answered:
1. stage 1 reads every profile and what it says without running anything except ADA's
   own profile print: an ada credential_process with the ada found here is auto-checkable,
   another program needs the person's click, SSO and console-login profiles are signed
   in to in the app, an assume-role from static keys without MFA is automatic, with MFA
   it waits; SwarmAI's own profile is never listed.
2. stage 2: a login row never runs, a check row runs only when the person asked, a row
   is looked up from the configuration (never from the request), and a check that hangs
   is killed with its whole process group at the hang guard, with stdin closed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core import aws_account as aa
from core import aws_detect as ad

ADA = "/opt/tools/ada"


@pytest.fixture
def aws_files(tmp_path, monkeypatch):
    conf = tmp_path / "config"
    creds = tmp_path / "credentials"
    hang = tmp_path / "hang.sh"
    hang.write_text(f"#!/bin/sh\necho $$ > {tmp_path}/pid\nsleep 60\n")
    hang.chmod(0o755)
    conf.write_text(f"""
[default]
credential_process = {ADA} credentials print --account=111111111111 --role=Admin --provider=isengard

[profile vault]
credential_process = aws-vault export work --format=json

[profile hangs]
credential_process = {hang}

[profile corp-sso]
sso_session = corp
sso_account_id = 222222222222
sso_role_name = Admin

[sso-session corp]
sso_start_url = https://d-1.awsapps.com/start
sso_region = eu-west-1

[profile console]
login_session = arn:aws:iam::333333333333:user/me

[profile deploy]
role_arn = arn:aws:iam::444444444444:role/path/Deployer
source_profile = keys

[profile deploy-mfa]
role_arn = arn:aws:iam::444444444444:role/Deployer
source_profile = keys
mfa_serial = arn:aws:iam::444444444444:mfa/me

[profile swarmai]
credential_process = /x/python-backend aws-credentials
""")
    creds.write_text("[keys]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = x\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(conf))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setattr(aa, "find_ada", lambda: ADA)
    monkeypatch.setattr(aa, "_same_file", lambda a, b: a == b)
    monkeypatch.setattr(aa, "_ada_profile_print",
                        lambda b: {"account": "555555555555", "role": "Admin", "provider": "Isengard"})
    return tmp_path


def test_stage1(aws_files):
    rows = {r["profile"]: r for r in ad.stage1()}
    assert "swarmai" not in rows
    assert rows[""]["kind"] == "ada_default" and rows[""]["account_id"] == "555555555555"
    assert rows[""]["ada"]["provider"] == "isengard"
    assert (rows["default"]["kind"], rows["default"]["verify"], rows["default"]["account_id"]) == \
        ("ada", "auto", "111111111111")
    assert (rows["vault"]["kind"], rows["vault"]["verify"]) == ("process", "check")
    sso = rows["corp-sso"]
    assert (sso["kind"], sso["verify"], sso["account_id"], sso["role"]) == ("sso", "login", "222222222222", "Admin")
    assert sso["sso"] == {"start_url": "https://d-1.awsapps.com/start", "sso_region": "eu-west-1"}
    assert (rows["console"]["verify"], rows["console"]["account_id"]) == ("login", "333333333333")
    assert (rows["deploy"]["verify"], rows["deploy"]["account_id"], rows["deploy"]["role"]) == \
        ("auto", "444444444444", "Deployer")
    assert rows["deploy-mfa"]["verify"] == "check"
    assert (rows["keys"]["kind"], rows["keys"]["verify"]) == ("keys", "auto")


def test_login_and_unasked_check_rows_never_run(aws_files, monkeypatch):
    monkeypatch.setattr(ad.subprocess, "Popen", lambda *a, **k: pytest.fail("must not run"))
    assert ad.verify(ad.find_row("corp-sso"), user_asked=False)["status"] == "needs_login"
    assert ad.verify(ad.find_row("vault"), user_asked=False)["status"] == "check"
    assert ad.find_row("not-in-config") is None


def test_a_hanging_check_is_killed_with_its_children(aws_files, monkeypatch):
    monkeypatch.setattr(ad, "_HANG_GUARD_S", 3)
    started = time.time()
    out = ad.verify(ad.find_row("hangs"), user_asked=True)
    assert out["status"] in ("timeout", "failed") and time.time() - started < 20
    pid_file = Path(aws_files) / "pid"
    if pid_file.exists():  # the profile's program ran: it must be gone now
        pid = int(pid_file.read_text())
        time.sleep(0.3)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_the_job_carries_no_secret_and_stdin_is_closed(aws_files, monkeypatch):
    seen = {}

    class P:
        pid = 1

        def __init__(self, argv, **kw):
            seen.update(kw)

        def communicate(self, timeout=None):
            return ('{"status": "verified", "account_id": "111111111111", "arn": "a"}', "")

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(ad.subprocess, "Popen", P)
    monkeypatch.setattr(ad.os, "killpg", lambda *a: None)
    out = ad.verify(ad.find_row("keys"), user_asked=False)
    assert out["status"] == "verified"
    assert seen["stdin"] is ad.subprocess.DEVNULL and seen["start_new_session"] is True
    job = seen["env"][ad.JOB_ENV]
    assert "AKIA" not in job and '"profile": "keys"' in job
    assert "AWS_PROFILE" not in seen["env"]
