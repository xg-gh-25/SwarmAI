"""A dispatched task runs on the model this host runs on.

A Hive keeps its own ``config.json``: its ``default_model`` and ``available_models`` were
written at its first boot and stay as they were (the product never moves a stored
default to a new flagship; model_registry), so a Hive set up months ago keeps running
the model that was the default then, and a later model is not even in its list. A Hive
is this host's runner (the host provisions and updates it), and a task sent from here
is the person's work: it should run on the model they use here. So before a dispatch the
host reads the Hive's settings and, when its default is not this host's:

1. asks the Hive to call that model once (``POST /api/system/verify-auth`` with the
   model as an unsaved override: one token, nothing stored), so a Hive whose account,
   region or code cannot run it is found before anything changes;
2. sets it as the Hive's default (``PUT /api/settings``): this host's model first in the
   Hive's list, the Hive's own models after it, none dropped.

The change is the Hive's default, so every new session there runs on it, not only this
task's. A Hive that cannot be read, cannot call the model or refuses the setting stops
the dispatch with the reason: a task silently run on another model is the failure this
module exists to prevent. The host cannot see inside the Hive: the model each run used
is read back from the usage ledger (core/swarm_out_results.spend_of) and shown on its
card.

Every message here is built from the host's own Hive name, a registry model name, an
exception class name or an HTTP status; no text the Hive sent gets in.

- ``host_model()`` — this host's default model (its config)
- ``align(client, hive_name, model=None)`` — make the Hive's default this host's
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

#: the longest model name taken from a Hive's list (a registry name is ~20 characters)
MAX_NAME_CHARS = 64


class ModelAlignError(Exception):
    """The Hive's model could not be read, checked or set; the message is safe to show."""


def host_model() -> Optional[str]:
    from core.app_config_manager import AppConfigManager
    from model_registry import MODEL_NAMES
    model = AppConfigManager.instance().get("default_model")
    return model if isinstance(model, str) and model in MODEL_NAMES else None


def _known(name: Any) -> str:
    """A Hive-sent model name as the log may show it: a registry name, else "other"."""
    from model_registry import MODEL_NAMES
    return name if isinstance(name, str) and name in MODEL_NAMES else "other"


def _json(r: httpx.Response) -> Optional[dict]:
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def align(client: httpx.AsyncClient, hive_name: str, model: Optional[str] = None) -> Optional[str]:
    """Set the Hive's default model to ``model`` (this host's when None) unless it is
    already. Returns the model the Hive will run new sessions on; None when this host
    has no model of the registry to give (nothing is changed then). Raises
    ``ModelAlignError`` when the Hive's settings cannot be read, the Hive cannot call
    the model, or it does not take the setting."""
    model = model or host_model()
    if model is None:
        return None
    try:
        r = await client.get("/api/settings")
    except httpx.HTTPError as e:
        raise ModelAlignError(f"Hive {hive_name}'s settings could not be read ({type(e).__name__})") from None
    cur = _json(r) if r.status_code == 200 else None
    if cur is None:
        raise ModelAlignError(f"Hive {hive_name}'s settings could not be read (HTTP {r.status_code})")
    if cur.get("default_model") == model:
        return model
    # 1. the Hive calls the model once, nothing saved
    try:
        v = await client.post("/api/system/verify-auth", json={"default_model": model})
    except httpx.HTTPError as e:
        raise ModelAlignError(f"Hive {hive_name} could not check {model} ({type(e).__name__})") from None
    ok = _json(v) if v.status_code == 200 else None
    if not ok or ok.get("success") is not True or ok.get("model") != model:
        raise ModelAlignError(f"Hive {hive_name} cannot call {model} (HTTP {v.status_code}); "
                              "its model was not changed")
    # 2. the setting: this host's model first, the Hive's own list after it, whole
    listed = cur.get("available_models")
    others = [m for m in (listed if isinstance(listed, list) else [])
              if isinstance(m, str) and 0 < len(m) <= MAX_NAME_CHARS and m != model]
    body = {"available_models": [model, *others], "default_model": model}
    try:
        w = await client.put("/api/settings", json=body)
    except httpx.HTTPError as e:
        raise ModelAlignError(f"Hive {hive_name} did not confirm {model} as its model "
                              f"({type(e).__name__}); its model may have been changed") from None
    got = (_json(w) or {}).get("default_model") if w.status_code == 200 else None
    if got != model:
        raise ModelAlignError(f"Hive {hive_name} did not take {model} as its model (HTTP {w.status_code})")
    logger.info("swarm_out_model: %s now runs %s (was %s)", hive_name, model, _known(cur.get("default_model")))
    return model
