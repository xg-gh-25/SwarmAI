"""Tests for image_read_dedup_guard — a PreToolUse(Read) hook that dedupes
redundant image reads within a session.

What is tested (identity-based dedup, NOT content matching):
  AC1: 2nd Read of an unchanged image (same abspath, same st_mtime_ns) -> DENY;
       1st Read -> approve.
  AC2: Read of a CHANGED image (mtime advanced) -> approve (no false-deny).
  AC3: Non-image Read (.py/.md) and non-Read tools -> approve untouched (fail-safe).
  AC4: create_image_read_dedup_guard returns an INDEPENDENT closure per call =>
       two instances (= two sessions) do not share cache (no cross-session
       false-deny of a fresh session's first read).
  AC7: A Read carrying an 'offset' or 'limit' param on an already-cached unchanged
       image -> approve (escape valve for a compaction-evicted image the agent must
       re-see deliberately).

Methodology: drive the REAL guard closure with real temp files (no mock of the
function under test — only the filesystem, which is a local-substitutable
boundary). Deny is asserted by permissionDecision=='deny' in hookSpecificOutput;
approve by the absence of a deny (decision=='approve' or no deny key).
"""
import os

import pytest

from core.security_hooks import create_image_read_dedup_guard


def _is_deny(result: dict) -> bool:
    hso = result.get("hookSpecificOutput") or {}
    return hso.get("permissionDecision") == "deny"


async def _read(guard, path, **extra):
    tool_input = {"file_path": str(path), **extra}
    return await guard({"tool_name": "Read", "tool_input": tool_input}, "tu_1", None)


@pytest.mark.asyncio
async def test_ac1_second_read_of_unchanged_image_is_denied(tmp_path):
    img = tmp_path / "poster.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    guard = create_image_read_dedup_guard()

    first = await _read(guard, img)
    assert not _is_deny(first), "first read of an image must be approved"

    second = await _read(guard, img)
    assert _is_deny(second), "second read of the SAME unchanged image must be denied"
    reason = second["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason and len(reason) > 12, "deny must carry an informative stub reason"


@pytest.mark.asyncio
async def test_ac2_changed_image_is_approved(tmp_path):
    img = tmp_path / "hero.png"
    img.write_bytes(b"first")
    guard = create_image_read_dedup_guard()

    first = await _read(guard, img)
    assert not _is_deny(first)

    # Regenerate the image -> mtime advances -> NOT a duplicate.
    st = img.stat()
    img.write_bytes(b"regenerated-bigger-content")
    os.utime(img, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    second = await _read(guard, img)
    assert not _is_deny(second), "a regenerated (mtime-advanced) image must be approved"


@pytest.mark.asyncio
async def test_ac3_non_image_and_non_read_are_approved(tmp_path):
    py = tmp_path / "mod.py"
    py.write_text("x = 1\n")
    guard = create_image_read_dedup_guard()

    # Non-image file: repeat reads never denied.
    assert not _is_deny(await _read(guard, py))
    assert not _is_deny(await _read(guard, py)), ".py re-read must never be deduped"

    # Non-Read tool: approved untouched even for an image path.
    img = tmp_path / "x.png"
    img.write_bytes(b"png")
    bash = await guard(
        {"tool_name": "Bash", "tool_input": {"command": f"cat {img}"}}, "tu_1", None
    )
    assert not _is_deny(bash)

    # Missing file_path: fail-safe approve.
    empty = await guard({"tool_name": "Read", "tool_input": {}}, "tu_1", None)
    assert not _is_deny(empty)


@pytest.mark.asyncio
async def test_ac4_two_instances_do_not_share_cache(tmp_path):
    img = tmp_path / "shared.png"
    img.write_bytes(b"png-bytes")

    guard_a = create_image_read_dedup_guard()
    guard_b = create_image_read_dedup_guard()

    # Session A reads it (now cached in A).
    assert not _is_deny(await _read(guard_a, img))
    assert _is_deny(await _read(guard_a, img)), "A's own 2nd read is deduped"

    # Session B's FIRST read of the same path must NOT be denied (no leak).
    assert not _is_deny(await _read(guard_b, img)), (
        "a fresh session's first read must never be denied by another session's cache"
    )


@pytest.mark.asyncio
async def test_ac7_offset_or_limit_param_bypasses_dedup(tmp_path):
    img = tmp_path / "evicted.png"
    img.write_bytes(b"png")
    guard = create_image_read_dedup_guard()

    assert not _is_deny(await _read(guard, img))
    # Without escape param the 2nd read is denied ...
    assert _is_deny(await _read(guard, img))
    # ... but an explicit offset (deliberate re-read) is approved.
    assert not _is_deny(await _read(guard, img, offset=0)), (
        "a Read carrying offset must bypass dedup (compaction-evicted escape valve)"
    )
    # limit works the same way.
    assert not _is_deny(await _read(guard, img, limit=100))


@pytest.mark.asyncio
async def test_ac1_unstatable_path_is_failsafe_approved(tmp_path):
    """A path that cannot be stat'd (does not exist) -> approve, never deny/crash."""
    guard = create_image_read_dedup_guard()
    ghost = tmp_path / "does_not_exist.png"
    assert not _is_deny(await _read(guard, ghost))
    assert not _is_deny(await _read(guard, ghost))


@pytest.mark.asyncio
async def test_embedded_null_byte_path_is_failsafe_not_crash():
    """A path with an embedded null byte has a .png suffix (passes the image
    check) but .resolve()/os.stat raise ValueError, NOT OSError. The guard must
    fail-safe approve, never let the ValueError crash the hook (Gate-2 HIGH)."""
    guard = create_image_read_dedup_guard()
    # No exception must escape; result must be a (non-deny) approve.
    result = await guard(
        {"tool_name": "Read", "tool_input": {"file_path": "/tmp/evil\x00.png"}},
        "tu_1",
        None,
    )
    assert not _is_deny(result)
