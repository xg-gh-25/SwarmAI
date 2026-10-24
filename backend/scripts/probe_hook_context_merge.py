"""probe_hook_context_merge — manual probe (needs model credentials, costs one small call): how the real CLI combines additionalContext from two DIFFERENT PostToolUse
matcher entries that both match one Read call (repo_guidance on Read|Grep|Glob|Bash,
another hook on Read). Each returns a unique token; the model is asked to quote every
token it was given. Also records what hooks the CLI reports back in the stream."""
import asyncio
import os
import sys

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

WORK = sys.argv[1]
MODEL = sys.argv[2] if len(sys.argv) > 2 else "us.anthropic.claude-haiku-4-5-20251001-v1:0"
fired = []


def hook(token, event):
    async def _h(input_data, tool_use_id, context):
        fired.append((token, input_data.get("tool_name")))
        return {"hookSpecificOutput": {"hookEventName": event,
                                       "additionalContext": f"PROBE TOKEN: {token}"}}
    return _h


async def main():
    target = os.path.join(WORK, "note.txt")
    with open(target, "w") as fh:
        fh.write("hello\n")
    opts = ClaudeAgentOptions(
        model=MODEL, cwd=WORK, allowed_tools=["Read"], permission_mode="bypassPermissions",
        max_turns=3, setting_sources=[],
        hooks={"PostToolUse": [
            HookMatcher(matcher="Read|Grep|Glob|Bash", hooks=[hook("ALPHA-7731", "PostToolUse")]),
            HookMatcher(matcher="Read", hooks=[hook("BRAVO-4419", "PostToolUse")]),
        ]})
    prompt = (f"Use the Read tool once on {target}. After it returns, list EVERY line that "
              "starts with 'PROBE TOKEN:' that you were given anywhere in the tool result or "
              "in any additional context, verbatim, one per line. If you saw none, say NONE. "
              "Nothing else.")
    text = []
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            kind = type(msg).__name__
            for block in getattr(msg, "content", []) or []:
                t = getattr(block, "text", None)
                if t:
                    text.append(t)
            if kind not in ("AssistantMessage", "UserMessage", "ResultMessage"):
                print("STREAM", kind, str(getattr(msg, "data", ""))[:300])
            if kind == "UserMessage":
                print("USER", str(getattr(msg, "content", ""))[:600])
    print("FIRED", fired)
    print("ANSWER", "\n".join(text)[-800:])


asyncio.run(main())
