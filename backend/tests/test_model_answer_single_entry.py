"""Every backend call for a model's answer goes through jobs/bedrock.py (design
docs/design/2026-10-28-structured-model-output.md, step 4).

There, structured() makes the API shape the answer and text() refuses a reply cut at
max_tokens; before this, thirteen call sites asked for JSON in the prompt and parsed
the text ten different ways, and an unusable reply surfaced as "not valid JSON", a
silent empty result, or a half-parsed dict. This test fails when a new direct
Bedrock runtime call (a boto3 bedrock-runtime client, or converse / invoke_model on one)
appears anywhere else in backend/, so the next one is routed through the entry instead
of growing its own parser. It does not see a hand-signed HTTP request: the one in
core/bedrock_thinking_gateway.py proxies the CLI's own traffic and parses no answer.
"""
from __future__ import annotations

import ast
import functools
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# Calls on a bedrock-runtime client that produce a model reply.
_RUNTIME_CALLS = {"converse", "converse_stream", "invoke_model", "invoke_model_with_response_stream"}
_ENTRY = "jobs/bedrock.py"
# A file that calls Bedrock runtime for something other than an answer, and why.
_ALLOWED = {
    "routers/system.py": "the onboarding connectivity check: one token, the reply is discarded",
}
_SKIP_DIRS = {".venv", "tests", "apps", "node_modules", "__pycache__", "build", "dist", "templates"}


def _sources():
    import os
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py") and not name.endswith("_test.py"):
                path = Path(root) / name
                yield path.relative_to(BACKEND).as_posix(), path


def _runtime_call_sites(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _RUNTIME_CALLS:
            lines.append(node.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "client":
            # boto3.client("bedrock-runtime") or boto3.client(service_name="bedrock-runtime")
            names = node.args[:1] + [k.value for k in node.keywords if k.arg == "service_name"]
            if any(isinstance(a, ast.Constant) and a.value == "bedrock-runtime" for a in names):
                lines.append(node.lineno)
    return lines


@functools.lru_cache(maxsize=1)
def _flagged() -> dict[str, list[int]]:
    out = {}
    for rel, path in _sources():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # exact pre-filter: a call names its attribute (or the client name) in the source
        if not any(k in text for k in ("converse", "invoke_model", "bedrock-runtime")):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = _runtime_call_sites(tree)
        if lines:
            out[rel] = lines
    return out


def test_model_answers_come_only_through_jobs_bedrock() -> None:
    stray = {f: ls for f, ls in _flagged().items() if f != _ENTRY and f not in _ALLOWED}
    assert not stray, (
        f"direct Bedrock runtime calls outside {_ENTRY}: {stray}. Ask through "
        "jobs.bedrock.structured() or text(); if the call is not for an answer, add it to "
        "_ALLOWED with the reason")


def test_every_allowed_file_is_one_the_scan_flags() -> None:
    # An exemption for a file that no longer calls Bedrock is dead and would hide the
    # next real call there; and the entry itself must be seen, or the scan is blind.
    flagged = _flagged()
    assert _ENTRY in flagged, "the scan does not see jobs/bedrock.py's own calls"
    assert set(_ALLOWED) <= set(flagged), set(_ALLOWED) - set(flagged)


def test_the_scan_catches_each_call_shape() -> None:
    src = (
        "import boto3\n"
        "c = boto3.client('bedrock-runtime')\n"
        "c.converse(modelId='m')\n"
        "c.invoke_model(modelId='m', body='{}')\n"
        "x.invoke_model_with_response_stream()\n"
        "y.converse_stream()\n"
        "boto3.client('bedrock')\n"          # control plane: not a reply
        "c.list_foundation_models()\n"
        "boto3.client(service_name='bedrock-runtime')\n"
    )
    assert _runtime_call_sites(ast.parse(src)) == [2, 3, 4, 5, 6, 9]
