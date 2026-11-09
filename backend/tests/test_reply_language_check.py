"""The Stop check that asks for the final answer in the user's language (R19).

What is tested:
- a turn whose user message the enforcement injector classified as Chinese (or Japanese,
  Korean), ending in an English answer, is held once with a reason in that language; the
  continuation stop (stop_hook_active) passes;
- every other stop passes: a Chinese answer that names code, paths and URLs, a Latin-script
  or unclassified user message, no message, an API error, a CJK answer in another CJK
  script, and a session with no recorded turn language;
- the injector records the turn language, and resets it for a turn it cannot classify;
- an error inside the check lets the stop through.
Methodology: the real factories with a plain dict as session_context.
"""
import asyncio

import pytest

from core.runtime_hooks import create_enforcement_injector, create_reply_language_check

EN = ("Done. I changed the hook so the check runs once per turn, added two tests, and the "
      "suite is green. The remaining risk is small and documented in the commit message.")
# short Chinese prose around a long command: Latin as a whole, Chinese once code is removed
ZH_WITH_CODE = ("已修复，测试通过。\n```bash\ncd backend && python -m pytest "
                "tests/test_reply_language_check.py -q --maxfail=1 && git commit -m \"add the "
                "reply language check to the stop hook chain\"\n```\n见 `core/runtime_hooks.py` "
                "与 /Users/x/repo/backend/core/runtime_hooks.py 和 https://example.com/a/b/c")


def _stop(ctx, message, active=False):
    data = {"hook_event_name": "Stop", "stop_hook_active": active}
    if message is not None:
        data["last_assistant_message"] = message
    return asyncio.run(create_reply_language_check(ctx)(data, None, None))


def _turn(ctx, prompt):
    asyncio.run(create_enforcement_injector(ctx)({"prompt": prompt}, None, None))


@pytest.mark.parametrize("prompt,word", [("帮我看看这个钩子为什么没有生效，然后修一下", "中文"),
                                         ("このフックがなぜ動かないのか調べて直してください", "日本語"),
                                         ("이 훅이 왜 동작하지 않는지 확인하고 고쳐 주세요", "한국어")])
def test_a_cjk_turn_answered_in_english_is_blocked_once(prompt, word):
    ctx = {}
    _turn(ctx, prompt)
    out = _stop(ctx, EN)
    assert out["decision"] == "block" and word in out["reason"]
    assert _stop(ctx, EN, active=True) == {}


def test_every_other_stop_passes():
    ctx = {}
    _turn(ctx, "帮我看看这个钩子为什么没有生效，然后修一下")
    assert ctx["_turn_user_language"] == "zh"
    assert _stop(ctx, ZH_WITH_CODE) == {}                 # Chinese prose around code and paths
    # a slash inside Chinese prose is not a path
    assert _stop(ctx, "Done: updated the config and restarted the daemon. 读/写分离方案已上线并验证通过没有回归") == {}
    assert _stop(ctx, None) == {}                         # no message
    assert _stop(ctx, "   ") == {}
    assert _stop(ctx, "API Error: 529 overloaded, the request could not be completed in time") == {}
    assert _stop(ctx, "このフックは修正しました。テストも通りました。") == {}  # another CJK script
    _turn(ctx, "Please look at why this hook does not fire and fix it")
    assert ctx["_turn_user_language"] == "latin" and _stop(ctx, EN) == {}
    _turn(ctx, "")                                        # an empty turn resets the language
    assert ctx["_turn_user_language"] is None and _stop(ctx, EN) == {}
    _turn(ctx, "ok")                                      # unclassified
    assert ctx["_turn_user_language"] is None and _stop(ctx, EN) == {}
    assert _stop({}, EN) == {} and _stop(None, EN) == {}  # no recorded language


def test_an_error_passes_the_stop():
    class Broken(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    assert _stop(Broken(_turn_user_language="zh"), EN) == {}
