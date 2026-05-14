# C007: Goal Over Tool

> When a tool fails, try 3 alternative paths before telling the user.
> "The tool doesn't work" is not a valid stopping point.

## What Happened

The Outlook MCP server failed to load in a session. The agent immediately told the user: "Open a new tab." This happened **twice** across sessions (April 8 and April 9). The user had to push back ("你不要卡在这不动 想办法解决") before the agent bypassed the MCP layer and called the binary directly via stdio JSON-RPC — which took 5 minutes and worked perfectly.

Later (C012, April 25): WebFetch failed on a URL with anti-scraping. Same pattern — first response was "can you paste the content?" User pushed back again. `curl` with a mobile User-Agent header worked in 30 seconds.

## Why It Happened

**Tool-oriented thinking vs goal-oriented thinking.**

The agent's mental model: "I have Tool X. Tool X failed. Therefore I cannot accomplish this task." 

The correct model: "My goal is Y. Tool X is one path to Y. What other paths exist?"

MCP servers are a convenience layer, not a prerequisite. Any MCP binary can be called directly via stdio JSON-RPC pipe. Any URL that blocks one fetcher can be fetched with different headers. The failure was in framing, not capability.

## Structural Prevention

**3-attempt rule** (added to `backend/context/AGENT.md`):

When ANY tool or operation fails:
1. Try Bash/Python to achieve the same result
2. Try a different tool that can do the same thing  
3. Try a workaround (different approach entirely)

Only after ALL alternatives exhausted → tell the user.

**Blocking rule:** Every failure response must be preceded by at least 2 alternative attempts. Asking the user to compensate for a tool failure ("can you paste...") is treated the same as giving up.

## The Generalizable Insight

**Tool availability is not goal availability.** AI agents have a systematic bias toward equating "my tool failed" with "this is impossible." The bias exists because tools are the agent's primary interface — when a tool disappears, the agent's action space *feels* empty even when alternatives exist.

The fix is architectural: require alternative attempts as a blocking gate before any "I can't" response. The gate doesn't need intelligence — it needs stubbornness.

## Code References

- Rule: `backend/context/AGENT.md` (search "Tool Failure — Exhaust Alternatives")
- Pattern recurrence: C012 (same class, different tool)
- User correction sessions: 2026-04-08, 2026-04-09, 2026-04-25
