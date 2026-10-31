"""content_origin — a read-only file origin for the file viewer's "Open in browser".

Why: an HTML file opened in the system browser from the daemon's own
``/api/workspace/file/raw`` runs its scripts in the daemon's origin, so a page an agent
or a Hive wrote could call any API route like the App does
(docs/design/2026-10-31-swarm-out-ops-registry.md §2.4 ⑤, §5.5). This module serves
those files from a second listener in the same process that has no API at all: a
different port is a different origin, and nothing but ``GET|HEAD /f/<token>/<path>``
answers there (everything else is 404; no CORS headers).

Scope of a URL: ``url_for(target)`` mints one token per DIRECTORY (the file's parent,
resolved) and the listener serves only files under that directory, so the report's
relative links (``./data.json``, ``img/a.png``) work while ``fetch('/f/<token>/../..')``
reaches nothing outside it (resolved, symlinks included). Never served: a path with a
segment starting with ``.`` (``.env``, ``.git/config``). No URL for a file whose
directory is too wide to hand to a page: the home folder or above, a folder directly in
it (``~/Desktop``, ``~/Downloads``, ``~/.swarm-ai``), or a directory the caller names
(the workspace root); the viewer then opens the raw URL, sandboxed. Tokens live in
memory only: a daemon restart ends every URL it minted, and the viewer asks again.

Every response carries ``connect-src 'self'; form-action 'none'``: a page can fetch the
files next to it and nothing on other sites (scripts and images from a CDN still load,
so an image URL can still carry data out; a residual, design §5.5).

Started only by a desktop launcher (``SWARMAI_MODE`` daemon / subprocess, set
explicitly): dev and Hive keep the old raw URL. The port is the daemon's + 1, or any
free one when that is taken (a second instance, or the old daemon still holding it
during a restart). Bound to 127.0.0.1.

Public: ``start``, ``stop``, ``url_for``, ``base``, ``asgi_app``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import secrets
import socket
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

MAX_SIZE = 50 * 1024 * 1024        # the raw route's limit (workspace_api.MAX_PREVIEW_SIZE)
_HOST = "127.0.0.1"
_ROOTS: "dict[str, Path]" = {}       # token -> directory it may serve
_TOKEN_OF: "dict[str, str]" = {}     # directory -> its token (one per directory)
_STATE: dict = {"base": None, "server": None, "task": None}
_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "connect-src 'self'; form-action 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def base() -> "str | None":
    """``http://127.0.0.1:<port>`` while the listener runs, else None."""
    return _STATE["base"]


def _too_wide(root: Path, refuse: "tuple[Path, ...]") -> bool:
    home = Path.home().resolve()
    return (root == home or root in home.parents or root.parent == home
            or any(root == r.resolve() for r in refuse))


def url_for(target: Path, refuse: "tuple[Path, ...]" = ()) -> "str | None":
    """The content-origin URL of an existing file *target* (already resolved and
    checked by the caller, like the raw route), or None when the listener is not
    running (dev, Hive, or it failed to start) or its directory is too wide to serve
    (module docstring; *refuse* = more directories the caller will not hand out)."""
    b = _STATE["base"]
    if not b:
        return None
    real = target.resolve()
    root = real.parent
    if _too_wide(root, refuse) or real.name.startswith("."):
        return None
    token = _TOKEN_OF.get(str(root))
    if token is None:
        token = secrets.token_urlsafe(16)
        _TOKEN_OF[str(root)] = token
        _ROOTS[token] = root
    return f"{b}/f/{token}/{quote(real.name)}"


def _file_for(path: str) -> "Path | None":
    """The file ``/f/<token>/<rel>`` names, or None (unknown token, outside the token's
    directory after resolving, a dot segment, not a file, too large)."""
    if not path.startswith("/f/"):
        return None
    token, _, rel = path[3:].partition("/")
    root = _ROOTS.get(token)
    if root is None or not rel or "\0" in rel:
        return None
    try:
        cand = (root / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if not cand.is_relative_to(root) or not cand.is_file():
        return None
    if any(part.startswith(".") for part in cand.relative_to(root).parts):
        return None
    try:
        if cand.stat().st_size > MAX_SIZE:
            return None
    except OSError:
        return None
    return cand


async def _plain(send, status: int) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"0")]
                + [(k.lower().encode(), v.encode()) for k, v in _HEADERS.items()]})
    await send({"type": "http.response.body", "body": b""})


async def asgi_app(scope, receive, send) -> None:
    """The listener's whole application: GET/HEAD of a file under a minted directory."""
    if scope["type"] != "http":
        return
    if scope.get("method") not in ("GET", "HEAD"):
        await _plain(send, 405)
        return
    target = await asyncio.to_thread(_file_for, scope.get("path") or "")
    if target is None:
        await _plain(send, 404)
        return
    from starlette.responses import FileResponse
    mime, _ = mimetypes.guess_type(target.name)
    await FileResponse(target, media_type=mime or "application/octet-stream",
                       headers=_HEADERS)(scope, receive, send)


def _bind(port: int) -> socket.socket:
    # no SO_REUSEADDR: on macOS it lets this bind succeed beside another program's
    # 0.0.0.0 listener on the same port and take its local traffic; a port still in
    # TIME_WAIT just makes start() take a free one
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((_HOST, port))
    except OSError:
        s.close()
        raise
    s.listen(64)
    s.setblocking(False)
    return s


def _server_class():
    import uvicorn

    class _Quiet(uvicorn.Server):
        # The daemon's own uvicorn.Server owns SIGTERM / SIGINT; a second server that
        # captured them would swallow the signal meant for the daemon.
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    return _Quiet


async def start(daemon_port: int) -> "str | None":
    """Start the listener next to the daemon; returns its base URL, or None when it
    could not start (the viewer then keeps the raw URL)."""
    if _STATE["task"] is not None:
        return _STATE["base"]
    try:
        try:
            sock = _bind(daemon_port + 1)
        except OSError:
            sock = _bind(0)
        port = sock.getsockname()[1]
        import uvicorn
        config = uvicorn.Config(asgi_app, lifespan="off", log_level="warning",
                                access_log=False, loop="asyncio")
        server = _server_class()(config)
        task = asyncio.create_task(server.serve(sockets=[sock]), name="content-origin")
        # hand out URLs only once it listens (a startup that fails ends the task)
        for _ in range(200):
            if server.started or task.done():
                break
            await asyncio.sleep(0.025)
        if not server.started:
            server.should_exit = True
            task.cancel()
            raise RuntimeError("the content origin's server did not start")
    except Exception:
        logger.warning("content origin did not start; Open in browser keeps the raw URL",
                       exc_info=True)
        return None
    _STATE.update(base=f"http://{_HOST}:{port}", server=server, task=task)
    logger.info("content origin listening on %s:%d", _HOST, port)
    return _STATE["base"]


async def stop() -> None:
    """Stop the listener (daemon shutdown); every minted URL ends with it."""
    server, task = _STATE["server"], _STATE["task"]
    _STATE.update(base=None, server=None, task=None)
    _ROOTS.clear()
    _TOKEN_OF.clear()
    if server is None or task is None:
        return
    server.should_exit = True
    server.force_exit = True
    try:
        await task
    except (asyncio.CancelledError, Exception):
        logger.debug("content origin stopped with an error", exc_info=True)
