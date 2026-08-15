"""Regression: GET /mcp responses carry a SCHEMA-VALID tier (run_3989a574).

The Connections registry panel shows each MCP's load-timing `tier` (always/channel/
ondemand). `_entry_to_response` surfaces it — but the response schema field is a strict
`Literal["always","channel","ondemand"]`. A config file with a MISSING *or INVALID* tier
(e.g. a "lazy" typo borrowed from the skills always/lazy vocabulary) must NOT pass the raw
value into the response — that would 500 the whole /mcp endpoint (ResponseValidationError)
and blank the panel, EVEN THOUGH the runtime loader (`_get_tier`) tolerates the same bad
value and the session loads that config fine. `_entry_to_response` must be exactly as
lenient as the loader: route through `_get_tier` so invalid/missing → "always".

Adversarial finding (MED, run_3989a574): the first cut used `entry.get("tier","always")`,
which handled missing but NOT invalid tier.
"""
from __future__ import annotations

import pytest

from routers.mcp import _entry_to_response
from schemas.mcp import ConfigEntryResponse


def _full(entry_extra: dict, layer: str = "dev") -> dict:
    base = {"id": "x", "name": "x", "connection_type": "stdio", "config": {}, "enabled": True}
    base.update(entry_extra)
    return _entry_to_response(base, layer)


@pytest.mark.parametrize("raw,expected", [
    ("always", "always"),
    ("channel", "channel"),
    ("ondemand", "ondemand"),
    ("lazy", "always"),        # skills-vocab typo → must normalize, not 500
    ("bogus", "always"),       # any unknown → always
    (None, "always"),          # missing tier → always
])
def test_entry_to_response_tier_normalized(raw, expected):
    entry_extra = {} if raw is None else {"tier": raw}
    resp = _full(entry_extra)
    assert resp["tier"] == expected, f"tier {raw!r} → {resp['tier']!r}, expected {expected!r}"


@pytest.mark.parametrize("raw", ["always", "channel", "ondemand", "lazy", "bogus", None])
def test_response_is_schema_valid_for_any_config_tier(raw):
    """The whole point: a bad tier in config must NOT raise on the response model
    (which is what 500s the /mcp endpoint + blanks the Connections panel)."""
    entry_extra = {} if raw is None else {"tier": raw}
    # Must not raise ValidationError — _get_tier normalized before the model sees it.
    model = ConfigEntryResponse(**_full(entry_extra))
    assert model.tier in ("always", "channel", "ondemand")
