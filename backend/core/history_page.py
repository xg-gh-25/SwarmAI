"""Chat history pages measured in user turns, not DB rows.

The backend stores every agentic step as its own ``messages`` row (crash safety),
and ``GET /chat/sessions/{id}/messages`` merges consecutive assistant rows into one
bubble. A page of N *rows* therefore held anything from N/2 to 1-2 bubbles: one
long turn can be 2,000 rows. The frontend compared the merged count to the row
limit to decide "more older history?", got it wrong, and stopped loading older
history. A row limit could also cut a turn in half across two pages.

A page here is the last ``turns`` user messages (before an optional cursor) and
everything after each of them, so every turn arrives whole, up to MAX_PAGE_ROWS
rows (only a newest turn longer than that is cut). ``has_more`` is whether any
row is older than the page's first row.

Pure over a ``fetch(limit, before_id) -> rows (chronological)`` callable, so it is
tested without a database.

Public: ``page_by_turns``, ``ROW_BATCH``, ``MAX_PAGE_ROWS``.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

#: Rows read per DB query while collecting a page's turns.
ROW_BATCH = 400
#: Most rows one page carries (~2.4 MB at the ~1.6 KB/row average). Whole turns are
#: added newest first until the next would pass this; a newest turn that alone is
#: longer is cut to its newest MAX_PAGE_ROWS rows (the older part arrives with the
#: next page, and the frontend's seam merge joins the two halves into one bubble).
MAX_PAGE_ROWS = 1500

Fetch = Callable[[int, Optional[str]], Awaitable[list[dict[str, Any]]]]


async def page_by_turns(
    fetch: Fetch, turns: int, before_id: Optional[str] = None, *,
    batch: int = ROW_BATCH, max_rows: int = MAX_PAGE_ROWS,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (rows, has_more): up to the last ``turns`` user turns before
    ``before_id``, at most ``max_rows`` rows.

    rows are chronological. They start at a user row, except when the newest turn
    alone exceeds ``max_rows`` (then its newest ``max_rows`` rows) or at the very
    start of a session. has_more is True when older rows remain.
    """
    collected: list[dict[str, Any]] = []  # chronological
    cursor = before_id
    exhausted = False
    while True:
        rows = await fetch(batch, cursor)
        collected = rows + collected
        if len(rows) < batch:
            exhausted = True
        if (exhausted or len(collected) > max_rows
                or sum(1 for r in collected if r.get("role") == "user") >= turns):
            break
        cursor = rows[0]["id"]

    n = len(collected)
    start: Optional[int] = None
    seen = 0
    for i in range(n - 1, -1, -1):
        if collected[i].get("role") != "user":
            continue
        if n - i > max_rows:
            if start is None:  # the newest turn alone is too long: cut it
                start = n - max_rows
            break
        start = i
        seen += 1
        if seen == turns:
            break
    else:
        # No stop inside the loop: every user turn collected fits. At the start of
        # the session, take the rows before the first user row too.
        if exhausted and n <= max_rows:
            start = 0
        elif start is None:  # no user row at all
            start = max(0, n - max_rows)
    start = start or 0

    page = collected[start:]
    has_more = start > 0 or not exhausted
    if has_more and start == 0 and not exhausted:
        # The batch ended exactly on the page start: check one older row.
        has_more = bool(await fetch(1, page[0]["id"])) if page else False
    return page, has_more
