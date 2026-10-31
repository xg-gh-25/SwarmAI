"""The SwarmAI AWS account API (design docs/design/2026-10-31-swarmai-aws-account.md).

Step 1 is read-only for the person and the agent alike (``core/swarm_out_ops.py`` rows
``aws_account.*``): see the account, re-verify it, run the capability checks. The one
route that hands out credentials answers only a caller holding this boot's token from
``<data dir>/aws/daemon.json`` (the ``python-backend aws-credentials`` subcommand that
the ``swarmai`` profile's ``credential_process`` runs).

Changing the account (wizard, settings) arrives with design §9 step 4.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from core import aws_account, executors
from core.app_config_manager import AppConfigManager

router = APIRouter()


@router.get("")
async def get_account():
    return aws_account.view()


def _require_app(user_action: str | None) -> None:
    """A POST that does work (ada, STS, a Bedrock call) comes from the app or the agent,
    never from a web page: ``X-Swarm-User-Action`` is not a CORS-safelisted header, so a
    cross-site page cannot send it without a preflight the daemon refuses."""
    if not user_action:
        raise HTTPException(status_code=403, detail="X-Swarm-User-Action required")


@router.post("/verify")
async def verify_account(
        user_action: str | None = Header(default=None, alias="X-Swarm-User-Action")):
    """Resolve and pin again now (one STS call; ada or the profile may run)."""
    _require_app(user_action)
    try:
        await executors.run_in("aws_account", aws_account.resolve, True)
    except aws_account.AccountError:
        pass  # the record's state says what went wrong
    return aws_account.view()


@router.post("/check")
async def check_capabilities(
        user_action: str | None = Header(default=None, alias="X-Swarm-User-Action")):
    """Run the capability checks (one 1-token Bedrock call plus read-only AWS calls)."""
    _require_app(user_action)
    await executors.run_in("aws_account", aws_account.run_capability_checks,
                           AppConfigManager.instance())
    return aws_account.view()


_STATUS = {"unconnected": 404, "needs_login": 401, "account_mismatch": 409}


@router.get("/credentials")
async def get_credentials(x_swarm_credentials_token: str | None = Header(default=None)):
    """Short-term credentials in the ``credential_process`` format, pinned to the account."""
    if not aws_account.token_ok(x_swarm_credentials_token):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        creds = await executors.run_in("aws_account", aws_account.resolve)
    except aws_account.AccountError as exc:
        return JSONResponse({"error": exc.kind, "message": exc.message},
                            status_code=_STATUS.get(exc.kind, 503))
    return creds.process_json()
