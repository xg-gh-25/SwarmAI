"""The SwarmAI bucket of an account is named by one rule (hive/bucket_name.py), used by the
provisioner's deploy, hive/release.sh and the permission check.

Buckets made before the whole-account-ID name carry the last 4 digits (the Hives deployed
into them keep that name in their record), and release.sh kept uploading there while every
deploy since created and read a second, 12-digit bucket. Both now look the names up in the
same order and use the first one this account owns: an existing 4-digit bucket is still
found, and the 12-digit one is created only when none is owned. release.sh's block runs
under bash with an ``aws`` stub; no AWS call is made.
"""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from hive import bucket_name

ACCOUNT = "533267412361"
LEGACY = "swarmai-hive-2361-us-east-1"
CANONICAL = "swarmai-hive-533267412361-us-east-1"
REPO = Path(__file__).resolve().parents[2]
RELEASE_SH = REPO / "hive" / "release.sh"


def test_candidates_find_the_4_digit_name_first_and_create_the_whole_id_one():
    assert bucket_name.candidates(ACCOUNT, "us-east-1") == [LEGACY, CANONICAL]
    assert bucket_name.canonical(ACCOUNT, "us-east-1") == CANONICAL
    assert bucket_name.candidates("", "eu-west-1") == ["swarmai-hive-releases-eu-west-1"]
    assert bucket_name.owner(CANONICAL) == {"ExpectedBucketOwner": ACCOUNT}
    assert bucket_name.owner(LEGACY) == {}
    # a 4-digit name is pinned to the account it was found under, never to another one
    assert bucket_name.owner(LEGACY, ACCOUNT) == {"ExpectedBucketOwner": ACCOUNT}
    assert bucket_name.owner(LEGACY, "111111111111") == {}
    assert bucket_name.owner(CANONICAL, "111111111111") == {"ExpectedBucketOwner": ACCOUNT}
    assert bucket_name.owner("swarmai-hive-releases-us-east-1", ACCOUNT) == {}


def test_one_rule_names_the_bucket():
    """Nothing else builds an account's bucket name: the provisioner's names are the
    module's, and release.sh / aws_permissions call it."""
    from core import aws_permissions
    from hive import provisioner
    assert provisioner.swarmai_bucket_name is bucket_name.canonical
    assert provisioner.bucket_owner is bucket_name.owner
    assert aws_permissions.BUCKET_PREFIX == bucket_name.PREFIX
    built = re.compile(r"swarmai-hive-(\$\{|\{)|\{BUCKET_PREFIX\}\{|S3_ACCT: -4")
    for path in ("backend/hive/provisioner.py", "backend/core/aws_permissions.py", "hive/release.sh"):
        text = (REPO / path).read_text()
        assert not built.search(text), f"{path} builds the bucket name itself"
    assert "backend/hive/bucket_name.py" in RELEASE_SH.read_text()
    assert "bucket_name.canonical(account_id, region)" in (
        REPO / "backend/core/aws_permissions.py").read_text()


@pytest.mark.parametrize("argv", [["1234", "us-east-1"], [ACCOUNT, "us-east-1; rm -rf /"],
                                  [ACCOUNT], [ACCOUNT, "US-EAST-1"]])
def test_cli_refuses_a_bad_account_or_region(argv):
    r = subprocess.run([sys.executable, "-I", str(REPO / "backend/hive/bucket_name.py"), *argv],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and r.stdout == ""


def test_cli_prints_the_candidates_in_order():
    r = subprocess.run([sys.executable, "-I", str(REPO / "backend/hive/bucket_name.py"),
                        ACCOUNT, "us-gov-west-1"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert r.stdout.split() == ["swarmai-hive-2361-us-gov-west-1",
                                "swarmai-hive-533267412361-us-gov-west-1"]


# ── the provisioner's deploy ──────────────────────────────────────────


def _s3(head_codes: dict[str, str]):
    """An S3 client whose head_bucket answers by name: missing -> 404, a code -> that error,
    "ok" -> owned."""
    s3 = MagicMock()

    def head_bucket(Bucket, ExpectedBucketOwner=None):
        assert ExpectedBucketOwner == ACCOUNT
        code = head_codes.get(Bucket, "404")
        if code == "ok":
            return {}
        if code == "network":
            raise EndpointConnectionError(endpoint_url="https://s3.amazonaws.com")
        raise ClientError({"Error": {"Code": code}}, "HeadBucket")

    s3.head_bucket.side_effect = head_bucket
    session = MagicMock()
    session.client.return_value = s3
    return session, s3


async def _ensure(session):
    from hive.provisioner import HiveProvisioner
    p = HiveProvisioner(Path("/tmp/test.db"))
    return await p._ensure_s3_bucket(session, "us-east-1", ACCOUNT)


@pytest.mark.asyncio
async def test_provisioner_finds_an_owned_legacy_bucket():
    session, s3 = _s3({LEGACY: "ok"})
    assert await _ensure(session) == LEGACY
    s3.create_bucket.assert_not_called()
    kw = s3.put_public_access_block.call_args.kwargs
    assert kw["Bucket"] == LEGACY and kw["ExpectedBucketOwner"] == ACCOUNT


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_code", ["404", "403"], ids=["absent", "another-account"])
async def test_foreign_legacy_bucket_is_not_used(legacy_code):
    """No 4-digit bucket, or one this account does not own: the whole-ID bucket is created
    and used, with its owner on every call."""
    session, s3 = _s3({LEGACY: legacy_code})
    assert await _ensure(session) == CANONICAL
    assert s3.create_bucket.call_args.kwargs["Bucket"] == CANONICAL
    kw = s3.put_public_access_block.call_args.kwargs
    assert kw["Bucket"] == CANONICAL and kw["ExpectedBucketOwner"] == ACCOUNT


@pytest.mark.asyncio
async def test_an_unclear_lookup_stops_the_deploy_instead_of_making_a_second_bucket():
    session, s3 = _s3({LEGACY: "network"})
    with pytest.raises(EndpointConnectionError):
        await _ensure(session)
    s3.create_bucket.assert_not_called()


# ── hive/release.sh ───────────────────────────────────────────────────


def _release_block() -> str:
    text = RELEASE_SH.read_text()
    m = re.search(r"\n[ \t]*# BEGIN hive bucket[^\n]*\n(.*?)\n[ \t]*# END hive bucket\n", text, re.S)
    assert m, "release.sh marks its bucket block"
    return m.group(1)


# aws: head-bucket answers like the CLI (exit 0 when owned, else the S3 error on stderr);
# put-object succeeds only with the expected owner. Every call is logged.
_AWS_STUB = r"""#!/bin/bash
echo "$*" >> "__TMP__/aws.log"
if [ "$1 $2" = "s3api head-bucket" ]; then
  [ "$5" = "--expected-bucket-owner" ] && [ "$6" = "__ACCOUNT__" ] || exit 254
  answer=$(awk -v b="$4" '$1 == b { print $2 }' "__TMP__/heads")
  case "${answer:-404}" in
    ok) exit 0 ;;
    404) echo "An error occurred (404) when calling the HeadBucket operation: Not Found" >&2; exit 254 ;;
    403) echo "An error occurred (403) when calling the HeadBucket operation: Forbidden" >&2; exit 254 ;;
    *) echo "Could not connect to the endpoint URL: \"https://$4.s3.amazonaws.com/\"" >&2; exit 255 ;;
  esac
fi
if [ "$1 $2" = "s3api put-object" ]; then
  case " $* " in *" --expected-bucket-owner __ACCOUNT__ "*) exit 0 ;; esac
  exit 254
fi
exit 1
"""


def _run_release_block(tmp: Path, heads: dict[str, str], account: str = ACCOUNT,
                       region: str = "us-east-1") -> tuple[dict[str, str], str]:
    bindir = tmp / "bin"
    bindir.mkdir()
    (tmp / "heads").write_text("".join(f"{k} {v}\n" for k, v in heads.items()))
    aws = bindir / "aws"
    aws.write_text(_AWS_STUB.replace("__TMP__", str(tmp)).replace("__ACCOUNT__", ACCOUNT))
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    script = ("set -euo pipefail\n"
              f'PROJECT_ROOT="{REPO}"\nS3_ACCT="{account}"\nS3_REGION="{region}"\n'
              f'VERSION=9.9.9\nPACKAGE_NAME=swarmai-hive-v9.9.9-linux-arm64\n'
              f'DIST_DIR="{tmp}"\nARCHIVE="{tmp}/pkg.tar.gz"\nUPLOADED_TO=""\n'
              # set before the block by the script (the package's build identity sidecar)
              f'BUILD_INFO="{tmp}/pkg.build.json"\n'
              + _release_block() + '\necho "BUCKET=${S3_BUCKET}"\necho "NEW=${S3_NEW_BUCKET}"\n'
              'echo "UPLOADED=${UPLOADED_TO}"\n')
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines()
                if line.split("=", 1)[0] in ("BUCKET", "NEW", "UPLOADED"))
    return vals, r.stdout


def _aws_log(tmp: Path) -> str:
    log = tmp / "aws.log"
    return log.read_text() if log.exists() else ""


@pytest.mark.parametrize("canonical", ["ok", "404"], ids=["both-owned", "4-digit-only"])
def test_release_sh_uploads_to_the_owned_4_digit_bucket(tmp_path, canonical):
    vals, _ = _run_release_block(tmp_path, {LEGACY: "ok", CANONICAL: canonical})
    assert vals["BUCKET"] == LEGACY
    assert vals["UPLOADED"] == f"s3://{LEGACY}/v9.9.9/swarmai-hive-v9.9.9-linux-arm64.tar.gz"
    log = _aws_log(tmp_path)
    assert f"s3api head-bucket --bucket {LEGACY} --expected-bucket-owner {ACCOUNT}" in log
    puts = [ln for ln in log.splitlines() if ln.startswith("s3api put-object")]
    # the package, its checksums.txt and its build.json, all to the one owned bucket
    assert len(puts) == 3 and all(f"--bucket {LEGACY} " in ln for ln in puts)
    assert any("--key v9.9.9/build.json " in ln for ln in puts)
    assert all(f"--expected-bucket-owner {ACCOUNT}" in ln for ln in puts)


def test_release_sh_uploads_to_the_whole_id_bucket_when_it_is_the_one_owned(tmp_path):
    vals, _ = _run_release_block(tmp_path, {LEGACY: "403", CANONICAL: "ok"})
    assert vals["BUCKET"] == CANONICAL and vals["UPLOADED"].startswith(f"s3://{CANONICAL}/")


@pytest.mark.parametrize("legacy", ["404", "403"], ids=["absent", "another-account"])
def test_release_sh_targets_the_full_id_name_without_a_bucket(tmp_path, legacy):
    """No bucket owned: nothing is uploaded, and the WARN names the bucket a first deploy
    creates (the same name the provisioner creates)."""
    vals, out = _run_release_block(tmp_path, {LEGACY: legacy, CANONICAL: "404"})
    assert vals["BUCKET"] == "" and vals["UPLOADED"] == ""
    assert vals["NEW"] == CANONICAL
    assert f"The first Hive deploy creates {CANONICAL}" in out
    assert "put-object" not in _aws_log(tmp_path)


def test_release_sh_skips_the_upload_on_an_unclear_lookup(tmp_path):
    """A network error on the 4-digit name is not 'not owned': nothing is uploaded to the
    whole-ID name the Hives may not read."""
    vals, out = _run_release_block(tmp_path, {LEGACY: "network", CANONICAL: "ok"})
    assert vals["BUCKET"] == "" and vals["UPLOADED"] == ""
    assert "could not tell which S3 bucket is this account's" in out
    assert "Could not connect" in out
    log = _aws_log(tmp_path)
    assert CANONICAL not in log and "put-object" not in log


def test_release_sh_skips_an_account_the_rule_refuses(tmp_path):
    vals, out = _run_release_block(tmp_path, {}, region="nowhere")
    assert vals["BUCKET"] == ""
    assert "not a shape the bucket rule accepts" in out
    assert _aws_log(tmp_path) == ""


def test_release_sh_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(RELEASE_SH)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


# ── the owner on the provisioner's later calls ─────────────────────────


@pytest.mark.asyncio
async def test_package_sync_pins_the_owner_of_a_4_digit_bucket():
    """A 4-digit bucket carries no owner in its name: the account the Hive was deployed
    under is passed on every head/put of the package sync."""
    from hive.provisioner import HiveProvisioner
    s3 = MagicMock()
    # the bucket holds the package and its checksums.txt but no build.json (as real S3
    # answers a missing key), so only the version decides
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
    session = MagicMock()
    session.client.return_value = s3
    p = HiveProvisioner(Path("/tmp/test.db"))
    await p._sync_release_to_s3(session, LEGACY, "9.9.9", "us-east-1", ACCOUNT)
    calls = s3.head_object.call_args_list + s3.get_object.call_args_list
    assert s3.head_object.call_args_list and s3.get_object.call_args_list
    assert all(c.kwargs.get("ExpectedBucketOwner") == ACCOUNT for c in calls)
