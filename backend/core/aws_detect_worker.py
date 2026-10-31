"""One stage-2 check of ``core.aws_detect``, in its own process (``-m core.aws_detect_worker``).

Reads the job from the environment (``aws_detect.JOB_ENV``), resolves that one profile
(or ADA's own profile) the way the daemon would, asks STS who it is, and prints one JSON
line. Never prints a credential. The parent kills this process group at its hang guard.
"""
from __future__ import annotations

import json
import os
import sys


def _out(**kw) -> None:
    print(json.dumps(kw))


def main() -> int:
    from core import aws_account
    from core.aws_detect import JOB_ENV
    try:
        job = json.loads(os.environ.get(JOB_ENV) or "{}")
    except ValueError:
        _out(status="failed", detail="bad job")
        return 0
    kind = job.get("kind")
    try:
        if kind in ("ada", "ada_default"):
            ada = job.get("ada") or {}
            creds = aws_account._ada_creds({  # noqa: SLF001
                "account_id": ada.get("account_id"),
                "ada": {"role": ada.get("role"), "provider": ada.get("provider"), "bin": ada.get("bin")}})
        else:
            import boto3
            creds = aws_account._frozen(  # noqa: SLF001
                boto3.Session(profile_name=job.get("profile")).get_credentials())
        ident = aws_account._sts_identity(creds, job.get("region") or "us-east-1")  # noqa: SLF001
    except aws_account.NeedsLogin as exc:
        _out(status="needs_login", detail=exc.message)
        return 0
    except aws_account.AccountError as exc:
        _out(status="failed", detail=exc.message)
        return 0
    except Exception as exc:  # noqa: BLE001 — every failure is one line for that row
        code = ""
        resp = getattr(exc, "response", None)
        if isinstance(resp, dict):
            code = (resp.get("Error") or {}).get("Code", "")
        invalid = code in ("InvalidClientTokenId", "SignatureDoesNotMatch", "AccessDenied",
                           "ExpiredToken", "UnrecognizedClientException")
        _out(status="invalid" if invalid else "failed",
             detail=(code or type(exc).__name__) if invalid else
             f"{code or type(exc).__name__}: {aws_account._redact(str(exc))}")  # noqa: SLF001
        return 0
    _out(status="verified", account_id=str(ident.get("Account")), arn=str(ident.get("Arn")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
