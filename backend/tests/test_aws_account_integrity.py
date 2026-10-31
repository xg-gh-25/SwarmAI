"""Who may talk to AWS directly (design docs/design/2026-10-31-swarmai-aws-account.md §9,
final test 1).

Question: does any backend module other than ``core/aws_account.py`` import an AWS SDK
(boto3, botocore, aiobotocore, amazon_transcribe, awscrt) without being on the list
below with its reason — and is every listed module still a real importer (a stale entry
would hide the next one)? A new direct AWS caller turns this red: it must use
``core.aws_account`` (``boto_session``, ``resolve``, ``cli_settings``) or be added here
with the reason it cannot.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SDKS = ("boto3", "botocore", "aiobotocore", "amazon_transcribe", "awscrt")

#: module → why it imports an AWS SDK itself
ALLOWED = {
    "core/aws_account.py": "the entry point",
    "core/bedrock_thinking_gateway.py":
        "SigV4 signing (botocore.auth); credentials come from aws_account, and the "
        "default chain only when there is no account",
    "core/credential_validator.py": "the spawn pre-flight when there is no account",
    "core/voice_transcribe.py": "the amazon_transcribe client; credentials from aws_account",
    "jobs/bedrock.py": "client config; the default chain only when there is no account",
    "jobs/executor.py": "frozen keys for the CLI only when there is no account",
    "routers/settings.py": "the credential probe when there is no account",
    "routers/system.py": "the setup wizard's verify (replaced by the account page, step 4)",
    "core/swarm_out_bridge.py": "Hive (design step 3)",
    "hive/provisioner.py": "Hive (design step 3)",
    "routers/hive.py": "Hive (design step 3)",
    "skills/s_pollinate/scripts/tts/backends/polly.py":
        "a skill script run in the agent's shell, as the user",
}


def _importers() -> "dict[str, set[str]]":
    out: dict[str, set[str]] = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv/")) or "/.venv/" in rel or "node_modules" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names if a.name.split(".")[0] in SDKS}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module \
                    and node.module.split(".")[0] in SDKS:
                found.add(node.module.split(".")[0])
        if found:
            out[rel] = found
    return out


def test_only_listed_modules_talk_to_aws_directly():
    importers = _importers()
    unlisted = sorted(set(importers) - set(ALLOWED))
    assert not unlisted, (
        f"{unlisted} import an AWS SDK directly: use core.aws_account (boto_session, "
        f"resolve, cli_settings), or add the module to ALLOWED with the reason")


def test_every_listed_module_still_imports_one():
    importers = _importers()
    stale = sorted(set(ALLOWED) - set(importers))
    assert not stale, f"{stale} no longer import an AWS SDK: remove them from ALLOWED"
