"""Adding, configuring and removing the Inbox's conversation sources.

Design: ``docs/design/2026-11-20-inbox.md`` §4 ("加一个渠道"). The routes are in
``routers/inbox.py``; this module does the work, so it can be tested without HTTP.

- **What can be connected without typing anything** (``available``): every enabled stdio
  MCP server of the person's MCP configuration whose ``tools/list`` offers all the read
  tools of a tool mapping a source module ships (its ``MCP_FAMILIES``: the slack-mcp and
  aws-outlook-mcp families today), and ``gh`` when it is logged in. A server is matched by
  the tools it really offers, never by its name. Each server is started once to list its
  tools and closed again: at most ``PROBE_CONCURRENCY`` at a time in the whole process,
  each bounded by ``PROBE_TIMEOUT_S``, and one probe per server configuration at a time
  (concurrent requests share it). The answer is kept ``CACHE_TTL_S``, failures too, so
  opening the settings page does not start every server each time. Entries already added
  are marked with their source id. The route needs ``X-Swarm-User-Action``: it starts
  processes, so a web page must not be able to trigger it.
- **Adding a source** (``add``) stores a row only after the source's own ``whoami``
  succeeded. Secrets given with the request (the Slack user token, the IMAP password) are
  stored in the OS credential store under the new source id first, because ``whoami``
  needs them, and deleted again when ``whoami`` fails. A failure answers with the source's
  message and its ``fix``; nothing is stored. Options never carry a secret (a key that
  names one, or a value that looks like a credential, is refused; and ``source_out`` shows
  only an allowlist of options). The same identity on the same kind and backend is added
  once.
- **Changing a source** (``update``): ``enabled``, ``interval_s``, and the options a person
  edits (Slack ``watched_channels``, mail ``extra_addresses``).
- **Removing a source** (``remove``): the row with its items, drafts, sends and feedback
  (``runner.remove_source``, between pulls), then its secrets. The one feed row is
  managed from config.yaml and cannot be removed or disabled here.
- **A secret for an existing source** (``set_secret``), e.g. a new token after the old
  one expired. A secret value is never returned, never logged, and scrubbed from any error
  text that would carry it.

Key public symbols: ``ConnectRefused``, ``available``, ``add``, ``update``, ``remove``,
``set_secret``, ``PROBE_TIMEOUT_S``, ``CACHE_TTL_S``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from core.inbox import runner, store
from core.inbox.models import Identity, SourceCategory, SourceHealth
from core.inbox.sources import base as sources_base

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 20.0        # starting one MCP server and listing its tools
PROBE_CONCURRENCY = 4
CACHE_TTL_S = 300.0
GH_TIMEOUT_S = 30.0
WHOAMI_TIMEOUT_S = 90.0       # an MCP server's first start can take a while
MAX_OPTIONS_BYTES = 8_000
MAX_LIST_ITEM_CHARS = 200

# option keys that would hold a secret: secrets go to the credential store only
_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential|private[_-]?key", re.I)
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# which source kind each editable option belongs to (the fields of SourcePatchRequest)
_EDITABLE_OPTIONS = {"watched_channels": "slack", "extra_addresses": "mail"}

_cache: dict[tuple, tuple[float, Optional[frozenset[str]]]] = {}
_gh_cache: dict[str, tuple[float, Optional[dict]]] = {}
_add_lock: dict[int, asyncio.Lock] = {}
# per event loop: the process-wide probe limit and the probe running for each server config
_probe_gate: dict[int, asyncio.Semaphore] = {}
_probing: dict[tuple, asyncio.Future] = {}


class ConnectRefused(Exception):
    """A source write that was not made. ``status`` is the HTTP status; ``code`` one of
    ``invalid_source``, ``invalid_secret``, ``whoami_failed``, ``already_added``,
    ``feed_source``, ``send_in_flight``, ``not_found``, ``secret_store_unavailable``."""

    def __init__(self, status: int, code: str, message: str = "", fix: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.fix = status, code, message or code, fix

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "fix": self.fix}


# ── what can be connected ─────────────────────────────────────────────────────

def mcp_families() -> list[tuple[str, str, type]]:
    """``(kind, family, source class)`` for every MCP tool mapping a source module ships
    (a module with ``KIND`` and ``MCP_FAMILIES``)."""
    store.ensure_sources_loaded()
    out: list[tuple[str, str, type]] = []
    for name in sorted(sys.modules):
        if not name.startswith("core.inbox.sources."):
            continue
        module = sys.modules[name]
        kind, families = getattr(module, "KIND", None), getattr(module, "MCP_FAMILIES", None)
        if isinstance(kind, str) and isinstance(families, dict):
            out.extend((kind, family, cls) for family, cls in sorted(families.items()))
    return out


def _configured_servers() -> list[str]:
    """Ids of the enabled stdio MCP servers of the workspace's MCP configuration
    (blocking: two file reads)."""
    from config import get_swarmws
    from core import mcp_config_loader as loader
    catalog, dev = loader.get_mcp_file_paths(Path(get_swarmws()))
    entries = loader.merge_layers(loader.read_layer(catalog, default_enabled=False),
                                  loader.read_layer(dev, default_enabled=True))
    return [str(e["id"]) for e in entries
            if isinstance(e, dict) and e.get("id") and e.get("connection_type", "stdio") == "stdio"]


def _spec_key(spec: Any) -> tuple:
    env = hashlib.sha256(json.dumps(sorted((spec.env or {}).items())).encode()).hexdigest()
    return (spec.id, spec.command, tuple(spec.args), env, tuple(sorted(spec.rejected_tools)))


async def _list_tools(spec: Any) -> list[dict]:
    """Start the server, list its tools, stop it."""
    from core.mcp_stdio_client import McpStdioClient
    client = McpStdioClient.from_spec(spec, init_timeout=PROBE_TIMEOUT_S)
    try:
        return await asyncio.wait_for(client.list_tools(timeout=PROBE_TIMEOUT_S), PROBE_TIMEOUT_S)
    finally:
        await client.close()


def _gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    return _probe_gate.setdefault(id(loop), asyncio.Semaphore(PROBE_CONCURRENCY))


async def _probe(spec: Any) -> Optional[frozenset[str]]:
    async with _gate():
        try:
            tools = await _list_tools(spec)
        except Exception as exc:  # noqa: BLE001 — a server that does not start is simply not offered
            logger.info("inbox: could not list the tools of MCP server %s: %s", spec.id, type(exc).__name__)
            return None
    return frozenset(str(t.get("name")) for t in tools
                     if isinstance(t, dict) and t.get("name")) - spec.rejected_tools


async def server_tools(server_id: str, *, refresh: bool = False) -> Optional[frozenset[str]]:
    """The tool names a configured server offers (minus the ones the person turned off),
    or ``None`` when it cannot be started or listed. Kept ``CACHE_TTL_S`` per server
    configuration; requests that miss the cache together share one probe."""
    from core.mcp_stdio_client import McpUnavailable, resolve_server
    try:
        spec = await store.run_blocking(resolve_server, server_id)
    except McpUnavailable as exc:
        logger.info("inbox: MCP server %s not usable: %s", server_id, type(exc).__name__)
        return None
    key = (id(asyncio.get_running_loop()), *_spec_key(spec))
    hit = _cache.get(key)
    if hit is not None and not refresh and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    running = _probing.get(key)
    if running is None:
        running = asyncio.ensure_future(_probe(spec))
        _probing[key] = running

        def _done(fut: asyncio.Future, key: tuple = key) -> None:
            _probing.pop(key, None)
            if not fut.cancelled() and fut.exception() is None:
                _cache[key] = (time.monotonic(), fut.result())

        running.add_done_callback(_done)
    return await asyncio.shield(running)


async def _gh_identity(*, refresh: bool = False) -> Optional[dict]:
    """``{"identity", "detail"}`` when the GitHub CLI is logged in, else ``None``."""
    hit = _gh_cache.get("gh")
    if hit is not None and not refresh and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    found: Optional[dict] = None
    source = None
    try:
        store.ensure_sources_loaded()
        source = sources_base.create_source(sources_base.SourceConfig(
            id="available-probe", kind="github", backend="gh"))
        me = await asyncio.wait_for(source.whoami(), GH_TIMEOUT_S)
        login = str(getattr(source, "login", "") or "")
        found = {"identity": f"@{login}" if login else me.display,
                 "detail": store.status_detail_of(source)}
    except Exception as exc:  # noqa: BLE001 — no gh, not logged in, no network: not offered
        logger.info("inbox: gh is not available (%s)", type(exc).__name__)
    finally:
        if source is not None:
            try:
                await source.close()
            except Exception:  # noqa: BLE001
                logger.debug("inbox: probe close failed", exc_info=True)
    _gh_cache["gh"] = (time.monotonic(), found)
    return found


async def available(*, refresh: bool = False) -> list[dict]:
    """``schemas.inbox.AvailableSourceOut`` fields for everything this machine can connect
    without typing anything."""
    from database import db
    servers = await store.run_blocking(_configured_servers)
    families = mcp_families()
    tools, gh = await asyncio.gather(asyncio.gather(*(server_tools(s, refresh=refresh) for s in servers)),
                                     _gh_identity(refresh=refresh))
    rows = await db.inbox_list_sources()
    added = {(r["kind"], r["backend"]): r["id"] for r in rows}
    out: list[dict] = []
    for server_id, names in zip(servers, tools):
        if not names:
            continue
        for kind, family, cls in families:
            if not frozenset(getattr(cls, "READ_TOOLS", frozenset())) <= names:
                continue
            backend = f"mcp:{server_id}"
            out.append({"kind": kind, "backend": backend, "channel_label": store.channel_label(kind),
                        "label": server_id, "options": {"tool_family": family}, "identity": "",
                        "detail": family, "added": (kind, backend) in added,
                        "source_id": added.get((kind, backend))})
    if gh is not None:
        out.append({"kind": "github", "backend": "gh", "channel_label": store.channel_label("github"),
                    "label": "GitHub CLI (gh)", "options": {}, "identity": gh["identity"],
                    "detail": gh["detail"], "added": ("github", "gh") in added,
                    "source_id": added.get(("github", "gh"))})
    return out


# ── add ────────────────────────────────────────────────────────────────────────

def _secret_keys(value: Any, path: str = "") -> list[str]:
    """Where options would hold a secret: a key that names one, or a value the egress
    redactor would mask (a token, a key, a password in a URL)."""
    from channels.egress_redactor import redact_text
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)):
                found.append(f"{path}{k}")
            found += _secret_keys(v, f"{path}{k}.")
    elif isinstance(value, list):
        for v in value:
            found += _secret_keys(v, path)
    elif isinstance(value, str) and redact_text(value) != value:
        found.append(path.rstrip(".") or "a value")
    return found


def _check_options(options: Any) -> dict:
    if not isinstance(options, dict):
        raise ConnectRefused(422, "invalid_source", "options must be an object")
    try:
        encoded = json.dumps(options, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ConnectRefused(422, "invalid_source", "options must be plain JSON")
    if len(encoded.encode("utf-8")) > MAX_OPTIONS_BYTES:
        raise ConnectRefused(422, "invalid_source", "options are too large")
    secretish = _secret_keys(options)
    if secretish:
        raise ConnectRefused(422, "invalid_source",
                             f"a secret goes in secrets, never in options: {', '.join(sorted(secretish))}")
    return json.loads(encoded)


def _scrub(text: str, secrets: dict[str, str]) -> str:
    from channels.egress_redactor import redact_text
    for value in secrets.values():
        if value:
            text = text.replace(value, "[REDACTED]")
    return redact_text(text)[:500]


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    return _add_lock.setdefault(id(loop), asyncio.Lock())


async def _delete_secrets(source_id: str, names) -> None:
    from core.inbox.sources.credentials import delete_secret
    for name in sorted(names):
        try:
            await store.run_blocking(delete_secret, source_id, name)
        except Exception as exc:  # noqa: BLE001 — a key of a removed id is never read again
            logger.warning("inbox: could not delete secret %s of source %s (%s)",
                           name, source_id, type(exc).__name__)


def _identity_overlaps(a: Optional[Identity], b: Identity) -> bool:
    if a is None:
        return False
    return bool({i.strip().lower() for i in a.ids} & {i.strip().lower() for i in b.ids})


async def add(kind: str, backend: str, options: Any, secrets: dict[str, str]) -> dict:
    """Store a new conversation source after its ``whoami`` succeeded. Returns its row.
    Raises ``ConnectRefused``; nothing is stored then."""
    from database import db
    store.ensure_sources_loaded()
    kind, backend = str(kind or "").strip(), str(backend or "").strip()
    if kind not in sources_base.registered_kinds():
        raise ConnectRefused(422, "invalid_source", f"no source of kind {kind!r}")
    options = _check_options(options)
    source_id = f"{re.sub(r'[^a-z0-9]+', '-', kind.lower()).strip('-') or 'source'}-{uuid4().hex[:10]}"
    try:
        source = sources_base.create_source(sources_base.SourceConfig(
            id=source_id, kind=kind, backend=backend, options=options))
    except (ValueError, TypeError, sources_base.SourceNotFound) as exc:
        raise ConnectRefused(422, "invalid_source", _scrub(str(exc), secrets))
    stored: list[str] = []
    committed = False
    try:
        if getattr(source, "category", None) is not SourceCategory.CONVERSATION:
            raise ConnectRefused(422, "invalid_source",
                                 "only a conversation source is added here; feeds are configured under Feeds")
        allowed = frozenset(getattr(source, "SECRET_NAMES", frozenset()))
        unknown = sorted(set(secrets) - allowed)
        if unknown:
            raise ConnectRefused(422, "invalid_secret",
                                 f"this backend takes no secret named {', '.join(unknown)}")
        for name, value in secrets.items():
            if not value or _CONTROL.search(value):
                raise ConnectRefused(422, "invalid_secret", f"the secret {name} is empty or has a line break")
        from core.inbox.sources.credentials import store_secret
        for name, value in sorted(secrets.items()):
            try:
                await store.run_blocking(store_secret, source_id, name, value)
            except Exception as exc:  # noqa: BLE001
                raise ConnectRefused(503, "secret_store_unavailable",
                                     f"could not store the secret {name}: {type(exc).__name__}")
            stored.append(name)
        try:
            me = await asyncio.wait_for(source.whoami(), WHOAMI_TIMEOUT_S)
            can_send = bool(await asyncio.wait_for(source.can_send(), WHOAMI_TIMEOUT_S))
        except sources_base.SourceAuthError as exc:
            raise ConnectRefused(400, "whoami_failed", _scrub(str(exc), secrets), fix=str(exc.fix or ""))
        except asyncio.TimeoutError:
            raise ConnectRefused(400, "whoami_failed",
                                 f"the source did not say who you are within {int(WHOAMI_TIMEOUT_S)} s")
        except Exception as exc:  # noqa: BLE001 — any other failure: the source's own message
            raise ConnectRefused(400, "whoami_failed", _scrub(f"{type(exc).__name__}: {exc}", secrets))
        detail = "" if can_send else store.status_detail_of(source)
        async with _lock():
            for row in await db.inbox_list_sources():
                if row["kind"] == kind and row["backend"] == backend and _identity_overlaps(
                        store.identity_from_row(row), me):
                    raise ConnectRefused(409, "already_added",
                                         f"this account is already connected as {row['id']}")
            stamp = store.ts(store.now())
            row = {
                "id": source_id, "kind": kind, "backend": backend,
                "category": SourceCategory.CONVERSATION.value,
                "config": json.dumps(options, ensure_ascii=False), "enabled": 1,
                "interval_s": store.DEFAULT_INTERVAL_S[SourceCategory.CONVERSATION.value],
                "cursor": None, "me": store.identity_to_json(me), "can_send": 1 if can_send else 0,
                "health": (SourceHealth.OK if can_send else SourceHealth.READ_ONLY).value,
                "fix": detail, "last_pull_at": None, "last_attempt_at": None, "last_error": "",
                "created_at": stamp, "updated_at": stamp}
            await db.inbox_put_source(row)
            committed = True
    finally:
        if not committed and stored:
            await _delete_secrets(source_id, stored)
        try:
            await source.close()
        except Exception:  # noqa: BLE001
            logger.debug("inbox: source close failed", exc_info=True)
    logger.info("inbox: source %s added (%s, %s)", source_id, kind, backend)
    runner.wake()
    return await db.inbox_get_source(source_id) or row


# ── change, remove, secrets ───────────────────────────────────────────────────

def _clean_list(name: str, values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        v = str(value).strip()
        if not v:
            continue
        if len(v) > MAX_LIST_ITEM_CHARS or _CONTROL.search(v):
            raise ConnectRefused(422, "invalid_source", f"{name}: {v[:40]!r} is not valid")
        if name == "extra_addresses":
            v = v.lower()
            if not _ADDRESS.match(v):
                raise ConnectRefused(422, "invalid_source", f"extra_addresses: {v[:60]!r} is not an address")
        if v not in out:
            out.append(v)
    return out


async def _row(source_id: str) -> dict:
    from database import db
    row = await db.inbox_get_source(source_id)
    if row is None:
        raise ConnectRefused(404, "not_found", "no such source")
    return row


async def update(source_id: str, *, enabled: Optional[bool] = None, interval_s: Optional[int] = None,
                 lists: Optional[dict[str, Optional[list[str]]]] = None) -> dict:
    """Change a source's ``enabled``, ``interval_s`` and editable options. Returns its row."""
    from database import db
    row = await _row(source_id)
    lists = {k: v for k, v in (lists or {}).items() if v is not None}
    if source_id == store.FEED_SOURCE_ID and (enabled is not None or lists):
        raise ConnectRefused(422, "feed_source",
                             "the feed source follows config.yaml: change the feeds under Feeds")
    fields: dict[str, Any] = {}
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
    if interval_s is not None:
        fields["interval_s"] = int(interval_s)
    if lists:
        options = json.loads(row.get("config") or "{}")
        for name, values in lists.items():
            owner = _EDITABLE_OPTIONS.get(name)
            if owner is None or row["kind"] != owner:
                raise ConnectRefused(422, "invalid_source", f"a {row['kind']} source has no {name}")
            options[name] = _clean_list(name, values)
        fields["config"] = json.dumps(options, ensure_ascii=False)
    if fields:
        await db.inbox_update_source(source_id, store.ts(store.now()), **fields)
        runner.wake()
    return await _row(source_id)


def _all_secret_names() -> frozenset[str]:
    """Every secret name a shipped source backend reads."""
    store.ensure_sources_loaded()
    names: set[str] = set()
    for mod_name in list(sys.modules):
        if not mod_name.startswith("core.inbox.sources."):
            continue
        for value in vars(sys.modules[mod_name]).values():
            secret_names = getattr(value, "SECRET_NAMES", None) if isinstance(value, type) else None
            if isinstance(secret_names, frozenset):
                names |= {str(n) for n in secret_names}
    return frozenset(names)


async def remove(source_id: str) -> dict:
    """Remove a conversation source, everything it pulled, and its secrets."""
    await _row(source_id)
    if source_id == store.FEED_SOURCE_ID:
        raise ConnectRefused(422, "feed_source",
                             "the feed source follows config.yaml: remove the feeds under Feeds")
    try:
        counts = await runner.remove_source(source_id)
    except store.SendInFlight:
        raise ConnectRefused(409, "send_in_flight", "a reply of this channel is being sent; try again in a moment")
    except runner.PullRunning:
        raise ConnectRefused(409, "pull_running", "the Inbox is pulling a channel now; try again in a moment")
    if counts is None:
        raise ConnectRefused(404, "not_found", "no such source")
    await _delete_secrets(source_id, _all_secret_names())
    logger.info("inbox: source %s removed (%d items, %d drafts, %d sends)", source_id,
                counts["items"], counts["drafts"], counts["sends"])
    runner.wake()
    return {"id": source_id, **counts}


async def set_secret(source_id: str, name: str, value: str) -> dict:
    """Store a secret for an existing source (a new token after the old one expired).
    Returns the source's row; the value is never returned or logged."""
    row = await _row(source_id)
    try:
        source = sources_base.create_source(sources_base.SourceConfig(
            id=row["id"], kind=row["kind"], backend=row["backend"],
            options=json.loads(row.get("config") or "{}")))
    except Exception as exc:  # noqa: BLE001 — no connector of this kind any more
        raise ConnectRefused(422, "invalid_source", type(exc).__name__)
    try:
        allowed = frozenset(getattr(source, "SECRET_NAMES", frozenset()))
    finally:
        try:
            await source.close()
        except Exception:  # noqa: BLE001
            logger.debug("inbox: source close failed", exc_info=True)
    if name not in allowed:
        raise ConnectRefused(422, "invalid_secret", f"this source takes no secret named {name!r}")
    if not value or _CONTROL.search(value):
        raise ConnectRefused(422, "invalid_secret", "the secret is empty or has a line break")
    from core.inbox.sources.credentials import store_secret
    try:
        await store.run_blocking(store_secret, source_id, name, value)
    except Exception as exc:  # noqa: BLE001
        raise ConnectRefused(503, "secret_store_unavailable",
                             f"could not store the secret {name}: {type(exc).__name__}")
    logger.info("inbox: secret %s of source %s stored", name, source_id)
    return row
