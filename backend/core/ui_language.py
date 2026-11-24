"""The app's language for fixed text the backend shows the person.

The desktop mirrors its UI language into the ``ui_language`` setting
(core/app_config_manager; desktop i18n/syncUiLanguage.ts): ``"zh"``, ``"en"`` or empty
when the app has not said. Long-lived text the person reads (Inbox cards and channel
settings, the chat's stop notice, the OS Eval report) comes from two tables with the
same keys, one English and one Chinese; this module picks one. Anything but ``"zh"`` is
English, and so is a setting that cannot be read.

- ``ui_language()`` — ``"zh"`` or ``"en"``. Zero IO once the config is loaded (the
  manager serves reads from its in-memory cache), so it is cheap on any path.
- ``text(en, zh, key, **fmt)`` — the entry *key* of the table for that language,
  formatted with *fmt* when given (``str.format``).

Text stored when it is made (a channel's fix and read-only reason on its source row, a
draft's note, the stop notice saved with a chat turn) keeps the language it was made in;
a source row's text is made again on its next pull. Text built when it is read (channel
labels, where an item came from, a channel's watch counts) follows the language at once.

The tables are checked by tests/test_backend_fixed_text_english.py: both have the same
keys and the same placeholders, and besides the named exceptions there (regexes, legacy
markers) only a table assigned to a name ending in ``_ZH`` holds Chinese.
"""
from __future__ import annotations

import logging
from typing import Mapping

logger = logging.getLogger(__name__)


def ui_language() -> str:
    """``"zh"`` when the app runs in Chinese, else ``"en"``."""
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get("ui_language")
    except Exception as exc:  # noqa: BLE001 — no setting: English
        logger.debug("ui_language unreadable (%s)", type(exc).__name__)
        return "en"
    return "zh" if str(value or "").strip().lower() == "zh" else "en"


def text(en: Mapping[str, str], zh: Mapping[str, str], key: str, /,
         lang: str | None = None, **fmt: object) -> str:
    """The *key* entry for *lang* (default: ``ui_language()``), formatted with *fmt*."""
    table = zh if (lang or ui_language()) == "zh" else en
    value = table[key]
    return value.format(**fmt) if fmt else value
