"""Tests for the non-owner sensitive-MCP gate (Phase-0 gap G2).

The load-bearing logic is ``strip_sensitive_mcps`` (the SINGLE source the
``build_options`` TRUSTED branch calls) built on the ``_is_sensitive_mcp``
predicate + the ``_SENSITIVE_MCP_SUBSTRINGS`` set. These tests drive the REAL
``strip_sensitive_mcps`` — NOT a locally re-derived copy of the comprehension
(that was the M1 test-theater finding: a mirror can't catch a regression in the
original). Mutation-sensitive: empty ``_SENSITIVE_MCP_SUBSTRINGS`` or break the
strip and these go RED.
"""

from __future__ import annotations

import pytest

from core.prompt_builder import (
    _SENSITIVE_MCP_SUBSTRINGS,
    _is_sensitive_mcp,
    strip_sensitive_mcps,
)


# --- the sensitive predicate against LIVE MCP names --------------------------

@pytest.mark.parametrize("name", [
    "user-aws-outlook-mcp",   # live id in mcp-dev.json — XG email (act-as-XG)
    "user-aws-sentral-mcp",   # live id in mcp-dev.json — XG revenue/CRM
    "aws-outlook",
    "aws-sentral",
    "outlook-mcp",
    "sentral-mcp",
    "AWS-OUTLOOK-MCP",        # case-insensitive
])
def test_sensitive_mcps_flagged(name):
    # Remove the matching substring from _SENSITIVE_MCP_SUBSTRINGS -> RED.
    assert _is_sensitive_mcp(name) is True


@pytest.mark.parametrize("name", [
    "channel-tools",          # the always-safe channel MCP
    "user-builder-mcp",
    "user-slack-mcp",
    "user-hs-kmine-mcp",
    "user-aws-knowledge-mcp",
    "playwright",
    "git",
])
def test_non_sensitive_mcps_allowed(name):
    # A trusted teammate keeps these (skills + safe integrations) — false-positive
    # here would break legitimate trusted workflows (pre-mortem risk).
    assert _is_sensitive_mcp(name) is False


def test_sensitive_set_is_single_source_and_nonempty():
    # Fail-closed contract: the set exists, is non-empty, and is the ONE place
    # the gate reads from (a rename/addition is caught by substring match).
    assert _SENSITIVE_MCP_SUBSTRINGS
    assert "aws-outlook" in _SENSITIVE_MCP_SUBSTRINGS
    assert "aws-sentral" in _SENSITIVE_MCP_SUBSTRINGS


# --- integration: the REAL strip_sensitive_mcps (single source) --------------
# These call the SAME function build_options calls — a regression in the strip
# (not just the predicate) is caught here (M1 fix).

def test_trusted_strip_removes_sensitive_keeps_safe():
    servers = {
        "channel-tools": {},
        "user-builder-mcp": {},
        "user-slack-mcp": {},
        "user-aws-outlook-mcp": {},
        "user-aws-sentral-mcp": {},
    }
    kept = strip_sensitive_mcps(servers)
    # sensitive stripped
    assert "user-aws-outlook-mcp" not in kept
    assert "user-aws-sentral-mcp" not in kept
    # safe kept (trusted teammate still has skills + channel-tools + builder)
    assert "channel-tools" in kept
    assert "user-builder-mcp" in kept
    assert "user-slack-mcp" in kept
    assert len(kept) == 3


def test_trusted_strip_is_fail_closed_for_new_sensitive_name():
    # A newly-added integration whose name contains a sensitive substring is
    # stripped by DEFAULT — no per-integration allowlisting needed.
    servers = {"user-aws-outlook-v2-mcp": {}, "user-notes-mcp": {}}
    kept = strip_sensitive_mcps(servers)
    assert "user-aws-outlook-v2-mcp" not in kept  # caught by 'aws-outlook'
    assert "user-notes-mcp" in kept


def test_all_safe_servers_survive_strip():
    servers = {"channel-tools": {}, "user-builder-mcp": {}}
    kept = strip_sensitive_mcps(servers)
    assert kept == servers  # nothing sensitive -> untouched
