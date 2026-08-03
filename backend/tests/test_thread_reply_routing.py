"""Thread-reply routing (run_45187d49).

Bug: the Slack bot replied to a group-channel message in the channel MAIN STREAM
instead of a thread — because human-mode reply sites passed raw external_thread_id
(None for a top-level @mention) straight to chat_postMessage(thread_ts=None).

Fix: a single pure rule, _resolve_reply_thread_ts, used for BOTH the reply target
AND session identity / thread_follow lookup so they can't drift:
  * explicit inbound thread → reply there
  * group + no thread → root a thread under the user's message (external_message_id)
  * DM (not group) → unchanged (None → top-level)

These tests drive the REAL helper (no mock of the code under change).
"""

from channels.gateway import _resolve_reply_thread_ts


class TestResolveReplyThreadTs:
    def test_group_no_thread_roots_under_user_message(self):
        """AC1: a top-level group @mention (no thread_ts) → reply threads under the
        user's own message ts, NOT the main stream (this is the reported bug)."""
        assert _resolve_reply_thread_ts(None, "1720000000.001", True) == "1720000000.001"

    def test_group_existing_thread_preserved(self):
        """AC2: a reply inside an existing thread → same thread kept."""
        assert _resolve_reply_thread_ts("1719999999.900", "1720000000.001", True) == "1719999999.900"

    def test_dm_no_thread_stays_top_level(self):
        """AC4: a DM (not group) with no thread → None → top-level, unchanged."""
        assert _resolve_reply_thread_ts(None, "1720000000.001", False) is None

    def test_dm_explicit_thread_still_honored(self):
        """A DM that IS already threaded still replies in that thread."""
        assert _resolve_reply_thread_ts("1719999999.900", "1720000000.001", False) == "1719999999.900"

    def test_group_no_thread_no_msg_id_is_none(self):
        """Degenerate: group but somehow no message id → None (nothing to root on),
        never crash."""
        assert _resolve_reply_thread_ts(None, None, True) is None

    def test_explicit_thread_wins_regardless_of_group(self):
        """The explicit inbound thread always wins — independent of is_group."""
        assert _resolve_reply_thread_ts("T", "M", True) == "T"
        assert _resolve_reply_thread_ts("T", "M", False) == "T"


class TestReplyTargetEqualsSessionKey:
    """The reply target and the session/thread_follow key MUST be the SAME value
    (Gate-1: else the user's next in-thread message keys a different session and
    thread_follow never re-engages). Both derive from the one helper."""

    def test_group_reply_target_and_session_key_agree(self):
        thr = _resolve_reply_thread_ts(None, "1720000000.001", True)   # reply target
        # the user's NEXT message in that thread arrives with thread_ts == thr;
        # session identity must key on the SAME value so find_by_external matches.
        session_key = _resolve_reply_thread_ts(thr, "1720000000.999", True)
        assert session_key == thr, "next in-thread msg must resolve to the same thread key"


class TestSingleComputeNoDrift:
    """Gate-2 BUG#1: reply_thread_ts is computed ONCE in handle_inbound_message and
    PASSED to _handle_conversation — never recomputed — so a chat_type-default
    mismatch between two call sites cannot make the reply target and the session
    key drift. These tests pin that structural guarantee against the real code."""

    def test_handle_conversation_accepts_reply_thread_ts_param(self):
        """_handle_conversation must accept reply_thread_ts as a parameter (proof it
        does not recompute it internally)."""
        import inspect
        from channels.gateway import ChannelGateway
        sig = inspect.signature(ChannelGateway._handle_conversation)
        assert "reply_thread_ts" in sig.parameters, \
            "single-source: _handle_conversation must receive reply_thread_ts, not recompute"

    def test_handle_conversation_does_not_recompute(self):
        """The helper is called exactly ONCE in gateway.py's reply path
        (handle_inbound_message) — _handle_conversation body must not call it again."""
        import inspect
        from channels.gateway import ChannelGateway
        src = inspect.getsource(ChannelGateway._handle_conversation)
        assert "_resolve_reply_thread_ts(" not in src, \
            "_handle_conversation must NOT recompute reply_thread_ts (drift-trap)"

    def test_missing_chat_type_group_still_threads_via_caller_default(self):
        """If chat_type were missing, the caller's classification decides. This pins
        the helper's contract: given is_group True (however the caller derived it),
        a no-thread message roots under the user msg — the caller owns the single
        chat_type read so the two-default divergence (Gate-2 BUG#1) can't happen."""
        assert _resolve_reply_thread_ts(None, "A", True) == "A"
        assert _resolve_reply_thread_ts(None, "A", False) is None
