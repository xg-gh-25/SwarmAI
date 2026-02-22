import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    options = ClaudeAgentOptions(
        system_prompt="You are an office assistant",
        permission_mode='acceptEdits',
        cwd=None,
    )

    async for message in query(
        prompt="hi",
        options=options
    ):
        print(message)


asyncio.run(main())