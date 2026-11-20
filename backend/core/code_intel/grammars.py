"""The tree-sitter grammars code intel loads — and the only place they are loaded.

tree-sitter-language-pack (1.8+) ships no grammars. On first use of a language it
downloads a ~24 MB platform bundle from GitHub into the user's cache
(``~/.cache/tree-sitter-language-pack/<version>/libs`` on Linux,
``~/Library/Caches/...`` on macOS, per HOME) and extracts the grammar library there.
That download runs inside the library's Rust extension WITHOUT releasing the GIL, so
every thread of the process stops while it runs, and for ever when it stalls: a
fresh-HOME startup froze 192 s on it (2026-10-28), and a source backend on a fresh HOME
did not answer /health within 100 s behind a stalled download (2026-11-20). Offline it
fails, and it means loading native code fetched at runtime.

So the backend process never downloads a grammar, in any mode. The grammars are put on
disk outside the server by ``scripts/bundle_tree_sitter_grammars.py``, which writes one
library per language in ``tree_sitter_languages()`` into
``generated/tree_sitter_grammars``. It is run by ``desktop/scripts/build-backend.sh``
(the packaged app ships the directory), by the developer start scripts (``dev.sh``,
``desktop/scripts/dev-with-backend.sh``) and by a Hive's first boot and every update
(``hive/user_data.py``, the provisioner's update script, ``hive/setup.sh``,
``hive/update-hive.sh``).

Before a grammar is loaded from that directory, this module points the library at it
(``PackConfig(cache_dir=…)``: the library's cache_dir IS its directory of grammar
libraries; with the library already there it loads it, no manifest and no network, and
the directory may be read-only). Which languages may load is asked of the library
(``downloaded_languages``, a local directory listing that never fetches). A language
that is not on disk is refused HERE — the library is never asked to load it, so it
never downloads — and code intel takes its regex path for that language.

- Packaged app: the shipped directory, else nothing is loaded at all.
- Source mode (dev, tests, Hive, a throwaway instance on a temp HOME):
  ``backend/generated/tree_sitter_grammars`` when the script made one, else only the
  grammars the library's own cache already holds for the installed version (a cache an
  earlier version of this module filled). An empty cache loads nothing.

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
    """Whether *language*'s grammar is on disk, so code intel may load it without any
    download. The library itself says which grammars a directory holds
    (``downloaded_languages``, a local listing that never fetches): its file names do
    not follow the language names (csharp is ``libtree_sitter_c_sharp``). With no
    shipped directory, the packaged app has none, and source mode has only what the
    library's own cache already holds (the library is not configured, so it lists its
    default cache)."""
    from utils.backend_bin import is_frozen
    if language in _shipped_cache:
        return _shipped_cache[language]
    if grammar_dir() is None and is_frozen():
        ok = False
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
    """The tree-sitter Language for *language*, from the grammars on disk. Raises
    LookupError for a language whose grammar is not there (never downloads it)."""
    if not shipped(language):
        raise LookupError(f"no tree-sitter grammar on disk for {language!r} "
                          "(scripts/bundle_tree_sitter_grammars.py puts them there)")
    _configure()
    import tree_sitter_language_pack as tslp
    return tslp.get_language(language)
