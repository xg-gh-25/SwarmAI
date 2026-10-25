#!/usr/bin/env python3
"""The effective Pollinate brand: the shipped defaults with the user's own brand on top.

Pollinate ships ``brand/identity.yaml`` as a neutral default (palette, typography, voice,
domain themes; no name, no link, no watermark). A user who wants their own brand on every
output writes ``~/.swarm-ai/pollinate-brand.yaml`` with any subset of the same keys; it is
deep-merged over the defaults (mappings merge, every other value replaces). The env var
``POLLINATE_BRAND_FILE`` points at a different user file.

``signature`` is the part every output carries and the delivery gates check. Every field
is optional; an unset field is simply not drawn and not required:

    signature:
      watermark: "Made with ..."          # small text, last element of a poster
      link: "https://example.com/me"      # shown as text in the footer / CTA
      link_text: "example.com/me"         # defaults to the link without its scheme
      qr:                                 # QR images for the link (user-provided)
        dark_on_light: "qr-dark.png"      # light backgrounds; relative to the brand file
        light_on_dark: "qr-light.png"     # dark backgrounds
      footer: {mark: "", name: "", tagline: ""}

Usage:
    "$SWARM_BACKEND_BIN" py scripts/brand_config.py              # effective brand as JSON
    "$SWARM_BACKEND_BIN" py scripts/brand_config.py --signature  # resolved signature only
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1]
SHIPPED_BRAND = SKILL_DIR / "brand" / "identity.yaml"
DEFAULT_USER_BRAND = Path.home() / ".swarm-ai" / "pollinate-brand.yaml"


def user_brand_path() -> Path:
    env = os.environ.get("POLLINATE_BRAND_FILE")
    return Path(env).expanduser() if env else DEFAULT_USER_BRAND


class BrandConfigError(ValueError):
    """The user's brand file is unreadable or has the wrong shape."""


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BrandConfigError(f"{path}: not valid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise BrandConfigError(f"{path}: top level must be a mapping")
    return data


def _mapping(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BrandConfigError(f"brand file: {where} must be a mapping, got {type(value).__name__}")
    return value


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_brand(user_file: Path | str | None = None) -> dict:
    """Shipped defaults deep-merged with the user's brand file (if it exists)."""
    path = Path(user_file).expanduser() if user_file else user_brand_path()
    return _merge(_read_yaml(SHIPPED_BRAND), _read_yaml(path))


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def signature(brand: dict | None = None, user_file: Path | str | None = None) -> dict:
    """The resolved signature: unset fields are None, QR paths are absolute.

    Raises BrandConfigError when the user's brand file is broken; the gates report it
    as a failure instead of crashing.
    """
    if brand is None:
        brand = load_brand(user_file)
    base_dir = (Path(user_file).expanduser() if user_file else user_brand_path()).parent
    sig = _mapping(brand.get("signature"), "signature")
    link = _text(sig.get("link"))
    link_text = _text(sig.get("link_text"))
    if link and not link_text:
        link_text = link.split("://", 1)[-1].rstrip("/")
    qr = {}
    for variant in ("dark_on_light", "light_on_dark"):
        raw = _text(_mapping(sig.get("qr"), "signature.qr").get(variant))
        if raw:
            p = Path(raw).expanduser()
            qr[variant] = str(p if p.is_absolute() else base_dir / p)
        else:
            qr[variant] = None
    footer = _mapping(sig.get("footer"), "signature.footer")
    return {
        "watermark": _text(sig.get("watermark")),
        "link": link,
        "link_text": link_text,
        "qr": qr,
        "footer": {k: _text(footer.get(k)) for k in ("mark", "name", "tagline")},
    }


def qr_basenames(sig: dict) -> list[str]:
    """File names of the configured QR images (how an output references them)."""
    return [Path(p).name for p in sig["qr"].values() if p]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--signature", action="store_true", help="print the resolved signature only")
    args = ap.parse_args()
    out = signature() if args.signature else load_brand()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
