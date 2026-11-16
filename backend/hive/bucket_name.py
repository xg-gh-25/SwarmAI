"""The S3 bucket of a SwarmAI account in a region: the one rule for every place that names it
(hive/provisioner.py deploy, hive/release.sh upload, core/aws_permissions check).

A new bucket is named with the whole account ID (``swarmai-hive-<12 digits>-<region>``): S3
names are global, and the older name, with only the last 4 digits, could already belong to
another account. Buckets made before that change keep the 4-digit name, and the Hives deployed
into them keep it in their record (``s3_bucket``), so it is still looked up: ``candidates``
lists the names in the order they are looked for, the 4-digit one first, and its last name is
the one a new deployment creates. A name is used only when this account owns the bucket
(head-bucket with ``ExpectedBucketOwner``); none owned -> the last name, created.

Standard library only, so hive/release.sh can run it with the system python3:
``python3 backend/hive/bucket_name.py <account_id> <region>`` prints the candidates, one per
line (exit 2 on an account or region that is not the expected shape).
"""

from __future__ import annotations

import re
import sys

PREFIX = "swarmai-hive-"
ACCOUNT_RE = re.compile(r"\d{12}")
# AWS region names (us-east-1, ap-southeast-2, us-gov-west-1, eu-central-2, ...)
REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d{1,2}")
_OWNER_RE = re.compile(r"^swarmai-hive-(\d{12})-")


def canonical(account_id: str, region: str) -> str:
    """The name a new deployment creates: with the whole account ID; without one (no
    account known) the old shared name."""
    if ACCOUNT_RE.fullmatch(account_id or ""):
        return f"{PREFIX}{account_id}-{region}"
    return f"{PREFIX}releases-{region}"


def legacy(account_id: str, region: str) -> str | None:
    """The name buckets made before the whole-ID name carry: the last 4 digits."""
    if ACCOUNT_RE.fullmatch(account_id or ""):
        return f"{PREFIX}{account_id[-4:]}-{region}"
    return None


def candidates(account_id: str, region: str) -> list[str]:
    """The names to look for, in order; the last one is the one created when none is owned."""
    old = legacy(account_id, region)
    return ([old] if old else []) + [canonical(account_id, region)]


def owner(bucket: str, account_id: str = "") -> dict:
    """``ExpectedBucketOwner`` for a bucket: the account its name carries, or, for a
    4-digit name, *account_id* when the name is that account's (a deployment found it owned
    by that account). Empty for the shared name, or a 4-digit name with no account given."""
    m = _OWNER_RE.match(bucket or "")
    if m:
        return {"ExpectedBucketOwner": m.group(1)}
    if ACCOUNT_RE.fullmatch(account_id or "") and (bucket or "").startswith(
            f"{PREFIX}{account_id[-4:]}-"):
        return {"ExpectedBucketOwner": account_id}
    return {}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not ACCOUNT_RE.fullmatch(argv[0]) or not REGION_RE.fullmatch(argv[1]):
        print("usage: bucket_name.py <12-digit account id> <region>", file=sys.stderr)
        return 2
    print("\n".join(candidates(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
