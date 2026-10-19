"""A prewarmed unit's hooks see the real session after adoption.

prewarm_channel_session used to build options without a session_context, so
hook_builder bound every hook to a fallback dict holding the agent id ("default")
for the unit's whole life: after a desktop/channel session adopted the unit, every
session-keyed gate (commit, test runner, worktree write) judged the wrong session.
The prewarm now creates the unit's stable hook context and passes it in; the first
send of the adopting session rewrites its sdk_session_id in place.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_router import PREWARM_SESSION_PREFIX, SessionRouter


@pytest.mark.asyncio
async def test_prewarm_binds_hooks_to_the_units_own_context():
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock())
    router = SessionRouter(prompt_builder=pb, config=MagicMock())

    async def spawned(self, *_a, **_k):
        if False:
            yield {}

    import core.session_router as sr
    made = []
    real_cls = sr.SessionUnit

    def capture(*a, **k):
        u = real_cls(*a, **k)
        made.append(u)
        return u

    with patch("core.agent_defaults.build_agent_config", AsyncMock(return_value={"id": "default"})), \
            patch("core.session_unit.SessionUnit._ensure_spawned", spawned), \
            patch.object(sr, "SessionUnit", side_effect=capture):
        await router.prewarm_channel_session("default")
    kwargs = pb.build_options.await_args.kwargs
    ctx = kwargs.get("session_context")
    assert isinstance(ctx, dict) and ctx["sdk_session_id"].startswith(PREWARM_SESSION_PREFIX)
    # the dict the hooks hold IS the unit's context: adoption's in-place rewrite reaches them
    assert made and made[0]._hook_session_context is ctx, \
        "the prewarmed unit must own the context passed to its hooks"


def test_prewarm_id_is_never_the_subprocess_session_id():
    """The env is fixed at spawn and survives adoption: a prewarm id there would own
    every run the adopting session creates."""
    from core.prompt_builder import with_app_session_id
    assert with_app_session_id({"A": "1"}, {"sdk_session_id": PREWARM_SESSION_PREFIX + "x"}) == {"A": "1"}
    assert with_app_session_id(None, {"sdk_session_id": "sess-1"}) == {"SWARM_APP_SESSION_ID": "sess-1"}
    assert with_app_session_id(None, None) is None
