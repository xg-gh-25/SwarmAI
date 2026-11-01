"""The per-turn language reminder (R19: reply in the user's language).

The classifier decides by script: Hangul -> ko, Kana -> ja, Han only -> zh,
Latin -> latin, too short or mixed -> None. The reminder names Chinese, Japanese
or Korean in that language; for Latin-script text it asks for the language of
the user's message and never names English.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

from core.runtime_hooks import _classify_message_language, create_enforcement_injector


CASES = [
    ("このコードをレビューしてください", "ja"),
    ("今日は会議の資料を確認してください", "ja"),
    ("이 코드를 검토해 주세요", "ko"),
    ("¿Puedes revisar este código, por favor?", "latin"),
    ("Peux-tu vérifier ce code, s'il te plaît ?", "latin"),
    ("帮我看看这个函数有没有问题", "zh"),
    ("帮我 review 一下这个 CI log 有没有问题", "zh"),
    ("please redesign the layout of this component", "latin"),
    ("ok", None),
]

# Every language name a reminder could mention, in English and in itself.
_LANGUAGE_NAMES = (
    "中文", "Chinese", "日本語", "Japanese", "한국어", "Korean", "English",
    "Spanish", "Español", "French", "Français",
)


def _reminder(message: str) -> str:
    out = asyncio.run(create_enforcement_injector()({"prompt": message}, None, MagicMock()))
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("message, expected", CASES)
def test_classifier(message, expected):
    assert _classify_message_language(message) == expected


@pytest.mark.parametrize("message, expected_name", [
    ("このコードをレビューしてください", "日本語"),
    ("이 코드를 검토해 주세요", "한국어"),
    ("帮我看看这个函数有没有问题", "中文"),
])
def test_cjk_reminder_names_the_language_in_that_language(message, expected_name):
    assert expected_name in _reminder(message)


@pytest.mark.parametrize("message", [
    "¿Puedes revisar este código, por favor?",
    "Peux-tu vérifier ce code, s'il te plaît ?",
    "please redesign the layout of this component",
])
def test_latin_reminder_asks_for_the_users_language_not_english(message):
    text = _reminder(message)
    assert "same language as the user's message" in text
    assert "English" not in text


def test_short_message_is_silent():
    assert _reminder("ok") == ""


def test_mixed_han_and_some_hangul_is_silent():
    # Neither script decides it: not Hangul-dominant, and not Han only.
    assert _classify_message_language("这个问题 문제 帮我看看这个") is None


def _names(text: str) -> list[str]:
    return [name for name in _LANGUAGE_NAMES if name in text]


@pytest.mark.parametrize("message, _", CASES)
def test_reminder_names_at_most_one_language(message, _):
    names = _names(_reminder(message))
    # "中文"/"Chinese" etc. are the same language; at most one language family.
    assert len(names) <= 1, names


_SCRIPTS = st.sampled_from([
    "帮我看看", "这个问题", "レビュー", "してください", "검토해", "주세요",
    "please", "revisar", "código", "vérifier", "review", "CI", " ", ",", "?", "123",
])


@settings(max_examples=200, deadline=None)
@given(st.lists(_SCRIPTS, min_size=0, max_size=12).map(" ".join))
def test_property_at_most_one_language_and_never_english_for_latin(message):
    lang = _classify_message_language(message)
    text = _reminder(message)
    assert len(_names(text)) <= 1
    if lang == "latin":
        assert "English" not in text
    if lang is None:
        assert text == ""
