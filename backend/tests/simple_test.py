import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage,SystemMessage

async def main():
    # Agentic loop: streams messages as Claude works
    async for message in query(
        prompt="hello. what is your model?",
        options=ClaudeAgentOptions(
            # model="claude-sonnet-4-5-20250929",
            # model="qwen.qwen3-coder-480b-a35b-v1:0",
            model="qwen.qwen3-next-80b-a3b",
            allowed_tools=["Read"],  # Tools Claude can use
            permission_mode="default",            # Auto-approve file edits
        #     env={"ANTHROPIC_DEFAULT_SONNET_MODEL":"qwen.qwen3-coder-480b-a35b-v1:0",
        #          "ANTHROPIC_MODEL":"qwen.qwen3-coder-480b-a35b-v1:0",
        #          "ANTHROPIC_DEFAULT_OPUS_MODEL":"qwen.qwen3-coder-480b-a35b-v1:0",
        #          "ANTHROPIC_DEFAULT_HAIKU_MODEL":"qwen.qwen3-coder-480b-a35b-v1:0"}
        )

    ):
        # Print human-readable output
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)              # Claude's reasoning
                elif hasattr(block, "name"):
                    print(f"Tool: {block.name}")   # Tool being called
        elif isinstance(message, SystemMessage):
            print(f"{message}")
        elif isinstance(message, ResultMessage):
            print(f"Done: {message.subtype}")      # Final result


asyncio.run(main())