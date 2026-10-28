"""The tree-sitter grammars code intel loads — and the only place they are loaded.

tree-sitter-language-pack (1.8+) ships no grammars. On first use of a language it
downloads a ~24 MB platform bundle from GitHub into the user's cache
(``~/Library/Caches/tree-sitter-language-pack/<version>/libs`` on macOS, per HOME) and
extracts the grammar library there. That download runs inside the library's Rust
extension WITHOUT releasing the GIL, so every thread of the process stops while it
runs: a fresh-HOME startup (the build verification probe, a new user's first start)
froze 192 s on it (measured 2026-10-28, the ``binary_startup`` failure). Offline it
fails, and it means loading native code fetched at runtime.

So the packaged app ships the grammars: ``desktop/scripts/build-backend.sh`` runs
``scripts/bundle_tree_sitter_grammars.py`` and packages
``generated/tree_sitter_grammars``, one library per language in
``tree_sitter_languages()``. Before a grammar is loaded, this module points the library
at that directory (``PackConfig(cache_dir=…)``: the library's cache_dir IS its directory
of grammar libraries; with the library already there it loads it, no manifest and no
network, and the directory may be read-only). A language whose grammar is not shipped
is refused HERE — the library is never asked for it, so it never downloads — and code
intel takes its regex path for that language.

- Packaged app: the shipped directory, else nothing is loaded at all (never the network).
- Source mode (dev, tests, Hive): ``backend/generated/tree_sitter_grammars`` when the
  script made one, else the library's own cache (the library may download, as before).

Every caller goes through ``get_language`` (tests/test_tree_sitter_grammars.py keeps
direct calls to the library out of the backend).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

GRAMMAR_DIRNAME = "tree_sitter_grammars"
_BACKEND = Path(__file__).resolve().parents[2]

_lock = threading.Lock()
_configured = False
_shipped_cache: dict[str, bool] = {}


def grammar_dir() -> Path | None:
    """The directory of shipped grammar libraries, or None when there is none."""
    from utils.backend_bin import bundled_path, is_frozen
    d = Path(bundled_path(GRAMMAR_DIRNAME)) if is_frozen() else _BACKEND / "generated" / GRAMMAR_DIRNAME
    return d if d.is_dir() else None


def shipped(language: str) -> bool:
    """Whether code intel may load *language*'s grammar without any download. The
    library itself says which grammars the directory holds (``downloaded_languages``,
    local only): its file names do not follow the language names (csharp is
    ``libtree_sitter_c_sharp``)."""
    from utils.backend_bin import is_frozen
    if language in _shipped_cache:
        return _shipped_cache[language]
    d = grammar_dir()
    if d is None:
        ok = not is_frozen()   # source mode with no shipped set: the library's own cache
    else:
        _configure()
        import tree_sitter_language_pack as tslp
        ok = language in set(tslp.downloaded_languages())
    _shipped_cache[language] = ok
    return ok


def _configure() -> None:
    global _configured
    if _configured:
        return
    with _lock:
        if _configured:
            return
        d = grammar_dir()
        if d is not None:
            import tree_sitter_language_pack as tslp
            tslp.configure(tslp.PackConfig(cache_dir=str(d)))
        _configured = True


def get_language(language: str) -> Any:
    """The tree-sitter Language for *language*, from the shipped grammars. Raises
    LookupError for a language whose grammar is not shipped (never downloads it)."""
    if not shipped(language):
        raise LookupError(f"no shipped tree-sitter grammar for {language!r}")
    _configure()
    import tree_sitter_language_pack as tslp
    return tslp.get_language(language)
