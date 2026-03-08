---
name: API Test
description: >
  Test, debug, and explore REST/GraphQL APIs. Send requests with any method,
  headers, auth, and body. Validate responses, chain requests, and mock endpoints.
  TRIGGER: "test API", "API request", "curl", "POST request", "REST API",
  "GraphQL", "API endpoint", "send request", "webhook", "API debug",
  "HTTP request", "auth token", "Bearer", "API key".
  DO NOT USE: for simple URL fetching of web pages (use WebFetch), browser
  interactions (use browser-agent), or downloading files (use Bash curl directly).
---

# API Test — HTTP API Testing & Debugging

Send HTTP requests with full control over method, headers, auth, body,
and response handling. Built on `curl` + `jq`.

## Quick Start

```
"Test the /users endpoint on my API"
"Send a POST request with this JSON body"
"Debug why this API returns 403"
"Test the GraphQL query against the endpoint"
"Chain these 3 API calls together"
```

---

## Core Request Patterns

### GET

```bash
# Simple GET
curl -s https://api.example.com/users | jq .

# With headers
curl -s -H "Accept: application/json" https://api.example.com/users | jq .

# With query parameters
curl -s "https://api.example.com/users?page=1&limit=10" | jq .

# Show response headers + body
curl -sI https://api.example.com/users
curl -sv https://api.example.com/users 2>&1 | head -30
```

### POST

```bash
# JSON body
curl -s -X POST https://api.example.com/users \
  -H "Content-Type: application/json" \
  -d '{"name": "John", "email": "john@example.com"}' | jq .

# Form data
curl -s -X POST https://api.example.com/login \
  -d "username=admin&password=secret"

# File upload
curl -s -X POST https://api.example.com/upload \
  -F "file=@/path/to/file.pdf" \
  -F "description=My document"

# JSON from file
curl -s -X POST https://api.example.com/data \
  -H "Content-Type: application/json" \
  -d @/path/to/payload.json | jq .
```

### PUT / PATCH / DELETE

```bash
# PUT (full replace)
curl -s -X PUT https://api.example.com/users/123 \
  -H "Content-Type: application/json" \
  -d '{"name": "Jane", "email": "jane@example.com"}' | jq .

# PATCH (partial update)
curl -s -X PATCH https://api.example.com/users/123 \
  -H "Content-Type: application/json" \
  -d '{"name": "Jane Updated"}' | jq .

# DELETE
curl -s -X DELETE https://api.example.com/users/123 -w "\nHTTP %{http_code}\n"
```

---

## Authentication

### Bearer Token

```bash
curl -s -H "Authorization: Bearer YOUR_TOKEN" \
  https://api.example.com/protected | jq .
```

### API Key

```bash
# In header
curl -s -H "X-API-Key: YOUR_KEY" https://api.example.com/data | jq .

# In query parameter
curl -s "https://api.example.com/data?api_key=YOUR_KEY" | jq .
```

### Basic Auth

```bash
curl -s -u "username:password" https://api.example.com/data | jq .
```

### OAuth2 Token Flow

```bash
# Step 1: Get token
TOKEN=$(curl -s -X POST https://auth.example.com/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=ID&client_secret=SECRET" \
  | jq -r '.access_token')

# Step 2: Use token
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.example.com/resource | jq .
```

---

## Response Analysis

### Full Response Inspection

```bash
# Status code only
curl -s -o /dev/null -w "%{http_code}" https://api.example.com/health

# Status + timing
curl -s -o /dev/null -w "HTTP %{http_code} | Time: %{time_total}s | Size: %{size_download} bytes\n" \
  https://api.example.com/health

# Headers + body
curl -s -D- https://api.example.com/users | head -20

# Full verbose (for debugging)
curl -sv https://api.example.com/users 2>&1
```

### JSON Processing with jq

```bash
# Pretty print
curl -s URL | jq .

# Extract field
curl -s URL | jq '.data.users'

# Filter array
curl -s URL | jq '.users[] | select(.active == true)'

# Extract specific fields
curl -s URL | jq '.users[] | {name, email}'

# Count results
curl -s URL | jq '.users | length'

# First/last
curl -s URL | jq '.users[0]'
curl -s URL | jq '.users[-1]'

# Flatten nested
curl -s URL | jq '[.data[] | {id, name: .profile.name}]'
```

---

## GraphQL

```bash
# Query
curl -s -X POST https://api.example.com/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ users { id name email } }"}' | jq .

# With variables
curl -s -X POST https://api.example.com/graphql \
  -H "Content-Type: application/json" \
  -d '{
    "query": "query GetUser($id: ID!) { user(id: $id) { name email } }",
    "variables": {"id": "123"}
  }' | jq .

# Mutation
curl -s -X POST https://api.example.com/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN" \
  -d '{
    "query": "mutation { createUser(input: {name: \"John\"}) { id } }"
  }' | jq .

# Introspection (discover schema)
curl -s -X POST https://api.example.com/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ __schema { types { name fields { name type { name } } } } }"}' \
  | jq '.data.__schema.types[] | select(.fields != null) | {name, fields: [.fields[].name]}'
```

---

## Request Chaining

### Sequential Dependent Requests

```bash
# 1. Login → get token
TOKEN=$(curl -s -X POST https://api.example.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "pass"}' \
  | jq -r '.token')

# 2. Use token to get user profile
USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.example.com/me | jq -r '.id')

# 3. Get user's orders
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.example.com/users/$USER_ID/orders" | jq .
```

### Batch Testing

```bash
# Test multiple endpoints
for endpoint in /health /users /products /orders; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://api.example.com$endpoint")
  echo "$endpoint → HTTP $STATUS"
done
```

---

## Webhook Testing

### Receive Webhooks Locally

```bash
# Simple webhook receiver (Python one-liner)
python3 -c "
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        print(f'\n--- Webhook received ---')
        print(f'Headers: {dict(self.headers)}')
        print(f'Body: {json.dumps(json.loads(body), indent=2)}')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *args): pass
print('Listening on :8888...')
HTTPServer(('', 8888), H).serve_forever()
" &

# Then point your webhook to http://localhost:8888
```

### Send Test Webhook

```bash
# Simulate a webhook payload
curl -s -X POST http://localhost:8888 \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: test123" \
  -d '{"event": "user.created", "data": {"id": 1, "name": "John"}}'
```

---

## Debugging Patterns

### Common HTTP Status Codes

| Code | Meaning | Debug Action |
|------|---------|-------------|
| 400 | Bad Request | Check request body format, required fields |
| 401 | Unauthorized | Check auth token/key, expiry |
| 403 | Forbidden | Check permissions, CORS, IP whitelist |
| 404 | Not Found | Check URL path, resource ID |
| 405 | Method Not Allowed | Check HTTP method (GET vs POST) |
| 422 | Unprocessable | Check validation rules, field types |
| 429 | Rate Limited | Check `Retry-After` header, add delay |
| 500 | Server Error | Check request is valid; server-side issue |
| 502/503 | Gateway/Unavail | Server down, retry later |

### Debug Checklist

1. **Check URL** — correct host, path, trailing slash?
2. **Check method** — GET vs POST vs PUT?
3. **Check headers** — Content-Type set? Auth header present?
4. **Check body** — valid JSON? Required fields?
5. **Check auth** — token expired? Correct scope?
6. **Verbose mode** — `curl -sv` to see full request/response

---

## Environment & Security

### Using Environment Variables

```bash
# Set in session
export API_BASE="https://api.example.com"
export API_TOKEN="your-token-here"

# Use in requests
curl -s -H "Authorization: Bearer $API_TOKEN" "$API_BASE/users" | jq .
```

### Security Rules

- **Never hardcode secrets** in commands — use variables or prompt user
- **Don't log auth tokens** — mask them in output
- **Use HTTPS** — curl auto-upgrades, but verify
- **Sensitive responses** — don't dump full response if it contains PII

---

## Rules

1. **Always pretty-print JSON** — pipe through `jq .` for readability
2. **Show status codes** — include HTTP status in output for debugging
3. **Use `-s` (silent)** — suppress curl progress bars
4. **Protect credentials** — use env vars, don't echo tokens
5. **Explain before mutating** — warn user before POST/PUT/DELETE to
   production endpoints
6. **Timeout long requests** — use `--max-time 30` for safety
7. **Chain with variables** — capture IDs/tokens in variables for multi-step flows
