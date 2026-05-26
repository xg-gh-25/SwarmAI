# MCP Server Development Guide

Build MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Quality is measured by how well the MCP enables LLMs to accomplish real-world tasks — not by how many endpoints it exposes.

## Quick Reference

| Language | Naming | Server Init | Guide |
|----------|--------|-------------|-------|
| Python | `{service}_mcp` | `FastMCP("service_mcp")` | [reference/python_mcp_server.md](reference/python_mcp_server.md) |
| TypeScript | `{service}-mcp-server` | `new McpServer({name, version})` | [reference/node_mcp_server.md](reference/node_mcp_server.md) |

---

## Process — 4 Phases

### Phase 1: Deep Research and Planning

#### 1.1 Understand the Target API

- Review the service's API documentation (endpoints, auth, data models)
- Use WebFetch to load API docs if available
- Identify key operations: what would an LLM need to DO with this service?

#### 1.2 Study MCP Protocol

Fetch the MCP spec:
- Sitemap: `https://modelcontextprotocol.io/sitemap.xml`
- Pages: append `.md` for markdown format (e.g., `https://modelcontextprotocol.io/specification/draft.md`)

Key areas: tool definitions, transport mechanisms, resource/prompt patterns.

#### 1.3 Load Framework Docs

- **Best Practices**: Read [reference/mcp_best_practices.md](reference/mcp_best_practices.md) first
- **Python SDK**: `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- **TypeScript SDK**: `https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`

#### 1.4 Plan Tool Set

**Prioritize comprehensive API coverage over workflow shortcuts.** List endpoints to implement starting with the most common operations. Balance:
- **Coverage tools** — map 1:1 to API endpoints (gives agent flexibility)
- **Workflow tools** — combine multiple calls for common tasks (gives agent convenience)

When uncertain, prioritize coverage.

---

### Phase 2: Implementation

#### 2.1 Project Structure

**Python:**
```
{service}_mcp/
├── pyproject.toml
├── README.md
├── src/
│   ├── __init__.py
│   ├── server.py         # FastMCP init + tool registration
│   ├── models.py         # Pydantic input/output models
│   ├── client.py         # API client (httpx, auth)
│   └── constants.py      # URLs, limits
└── tests/
```

**TypeScript:**
```
{service}-mcp-server/
├── package.json
├── tsconfig.json
├── README.md
├── src/
│   ├── index.ts          # McpServer init + transport
│   ├── tools/            # One file per domain
│   ├── services/         # API clients
│   ├── schemas/          # Zod schemas
│   └── constants.ts
└── dist/
```

#### 2.2 Core Infrastructure

Build first:
1. **API client** with auth (OAuth, API key, etc.)
2. **Error handling** — actionable messages that guide the LLM toward solutions
3. **Response formatting** — support both JSON (structured) and Markdown (readable)
4. **Pagination** — always respect `limit`, return `has_more` + `next_offset`

#### 2.3 Tool Implementation

For each tool:

```python
# Python (FastMCP)
@mcp.tool(
    name="service_action_resource",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def service_action_resource(params: InputModel) -> str:
    '''Concise description of what this tool does.'''
    ...
```

```typescript
// TypeScript (MCP SDK)
server.registerTool(
  "service_action_resource",
  {
    title: "Human-Readable Name",
    description: "What this tool does",
    inputSchema: { param: z.string().describe("...") },
    outputSchema: { result: z.string() },
    annotations: { readOnlyHint: true }
  },
  async ({ param }) => ({
    content: [{ type: "text", text: JSON.stringify(result) }],
    structuredContent: result
  })
);
```

**Tool naming rules:**
- snake_case always: `slack_send_message`, `github_create_issue`
- Include service prefix (anticipate multi-MCP environments)
- Action-oriented verbs: get, list, search, create, update, delete

**Tool annotations:**

| Annotation | Type | Default | Use When |
|-----------|------|---------|----------|
| `readOnlyHint` | bool | false | Tool only reads data |
| `destructiveHint` | bool | true | Tool can delete/overwrite |
| `idempotentHint` | bool | false | Repeated calls = same effect |
| `openWorldHint` | bool | true | Interacts with external systems |

---

### Phase 3: Review and Test

#### 3.1 Code Quality Checklist

- [ ] No duplicated code (DRY)
- [ ] Consistent error handling across all tools
- [ ] Full type coverage (Pydantic models / Zod schemas)
- [ ] Clear tool descriptions (concise, unambiguous)
- [ ] Pagination on all list operations
- [ ] Auth credentials from env vars (never hardcoded)
- [ ] DNS rebinding protection if local HTTP server

#### 3.2 Build and Test

**TypeScript:**
```bash
npm run build
npx @modelcontextprotocol/inspector  # Interactive testing
```

**Python:**
```bash
python -m py_compile src/server.py
# Test with MCP Inspector or direct stdio
```

---

### Phase 4: Create Evaluations

> The measure of MCP quality is NOT how well tools are implemented, but how well they enable LLMs with **no other context** and access **only** to these tools to answer realistic complex questions.

#### 4.1 Generate 10 Evaluation Questions

Create questions that:
- Are **independent** (no question depends on another)
- Require **read-only** operations only
- Are **complex** (need multiple tool calls, potentially dozens)
- Are **realistic** (real use cases humans care about)
- Have **single verifiable answers** (string comparison)
- Are **stable** (answer won't change over time)
- Cannot be solved with **straightforward keyword search**

#### 4.2 Write as XML

```xml
<evaluation>
  <qa_pair>
    <question>Which team member who joined in the last 6 months has the most merged PRs in the authentication module?</question>
    <answer>alice_chen</answer>
  </qa_pair>
  <!-- 9 more qa_pairs -->
</evaluation>
```

#### 4.3 Verify Answers

Solve each question yourself using the tools. If YOU can't get a clear answer, the question is too ambiguous or the tools are missing coverage.

---

## Transport Selection

| Criterion | stdio | Streamable HTTP |
|-----------|-------|-----------------|
| **Deployment** | Local (subprocess) | Remote (web service) |
| **Clients** | Single | Multiple |
| **Setup** | Simple (no network) | Medium (HTTP server) |
| **Use when** | Desktop apps, CLI tools | Cloud services, multi-user |

- **Prefer Streamable HTTP** for remote servers (simpler to scale than SSE)
- **SSE is deprecated** — use Streamable HTTP instead
- stdio servers: log to stderr, never stdout

---

## Pagination Pattern

```json
{
  "total": 150,
  "count": 20,
  "offset": 0,
  "items": [...],
  "has_more": true,
  "next_offset": 20
}
```

Default to 20-50 items. Never load all results into memory.

---

## Security

- Store API keys in env vars, validate on startup
- Sanitize file paths (prevent directory traversal)
- Validate URLs and identifiers
- For local HTTP: bind to `127.0.0.1`, validate `Origin` header
- Don't expose internal errors to clients
- Use schema validation (Pydantic/Zod) for ALL inputs

---

## Guardrails

- DO NOT hardcode API keys or secrets in source files
- DO NOT use deprecated `server.tool()` API in TypeScript (use `server.registerTool()`)
- DO NOT skip pagination on list operations (agents will get truncated results)
- DO NOT write vague tool descriptions ("does stuff with data" — be specific)
- DO NOT skip the evaluation phase — an untested MCP is an unusable MCP

---

## Dependencies

- **Python**: `mcp[cli]`, `httpx`, `pydantic`
- **TypeScript**: `@modelcontextprotocol/sdk`, `zod`, `express` (for HTTP transport)
- **Testing**: `npx @modelcontextprotocol/inspector`
