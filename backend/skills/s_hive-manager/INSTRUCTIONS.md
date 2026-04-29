# Hive Manager

Manage SwarmAI Hive cloud instances via the backend REST API. All operations
go through `curl` to the local backend — no direct AWS SDK calls needed.

## API Base

```bash
# Port is in ~/.swarm-ai/backend.json (always available, sandbox-safe)
PORT=$(python3 -c "import json; print(json.load(open('$HOME/.swarm-ai/backend.json'))['port'])")
BASE="http://127.0.0.1:$PORT/api/hive"
```

**Run this at the start of every Hive operation.** Port changes on each backend restart.

## Operations

### 1. List Hive Instances

```bash
curl -s "$BASE/instances" | python3 -m json.tool
```

Returns all Hives with status, URL, region, version. Credentials excluded from list.

Key fields: `id` (UUID — use this for all API calls), `name`, `status`, `cloudfront_domain`, `ec2_public_ip`, `version`, `owner_name`, `hive_type`.

### 2. Deploy a New Hive

**Prerequisites:**
- At least one AWS account configured (see §7)
- Account must be verified (see §8)

**Steps:**

```bash
# 1. List accounts to get the account ref (use the 'id' field, NOT 'account_id')
curl -s "$BASE/accounts" | python3 -c "
import sys, json
for a in json.load(sys.stdin):
    print(f\"  id={a['id']}  aws={a['account_id']}  label={a['label']}  region={a['default_region']}\")
"

# 2. Deploy
curl -s -X POST "$BASE/instances" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "titus-hive",
    "account_ref": "<id-from-step-1>",
    "region": "us-east-1",
    "instance_type": "m7g.xlarge",
    "owner_name": "Titus Tian",
    "hive_type": "shared"
  }'
```

**Field reference:**

| Field | Required | Rules |
|-------|----------|-------|
| `name` | Yes | 1-63 chars, lowercase `[a-z][a-z0-9-]*`, start with letter |
| `account_ref` | Yes | UUID `id` from `/accounts` (NOT the 12-digit AWS account number) |
| `region` | No | Default `us-east-1`. Allowed: us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1, eu-west-2, eu-central-1, ap-northeast-1, ap-southeast-1, ap-southeast-2, ap-south-1 |
| `instance_type` | No | Default `m7g.xlarge` |
| `owner_name` | No | Set for shared Hives (e.g., "Titus Tian"). Omit for personal Hives. |
| `hive_type` | No | `"my"` (personal) or `"shared"` (for others). Default `"shared"`. |

**Instance sizes:**

| Size | RAM | Cost | Use when |
|------|-----|------|----------|
| m7g.large | 8 GB | ~$60/mo | Light usage, single user |
| m7g.xlarge | 16 GB | ~$119/mo | Recommended default |
| m7g.2xlarge | 32 GB | ~$238/mo | Heavy usage, many skills — **ask user first** |

**Error responses:**
- `422`: Invalid name format or region
- `409`: Hive name already exists
- `404`: Account not found

**After deploy:** Status transitions `pending → provisioning → installing → running`.
Poll every 10s until `running`:

```bash
curl -s "$BASE/instances/<id>" | python3 -c "
import sys, json; d = json.load(sys.stdin)
print(f\"Status: {d['status']}  URL: https://{d.get('cloudfront_domain', 'pending...')}\")"
```

Provisioning: 5-10 min. CloudFront HTTPS: additional 5-15 min.

### 3. Update a Hive to New Version

```bash
# Check current versions
curl -s "$BASE/instances" | python3 -c "
import sys, json
for h in json.load(sys.stdin):
    print(f\"{h['name']:20s} v{h.get('version','?'):10s} {h['status']}\")
"

# Update one instance (must be running)
curl -s -X POST "$BASE/instances/<id>/update" \
  -H "Content-Type: application/json" \
  -d '{"version": "1.9.0"}'
```

Update uses SSM Run Command (no SSH). Instance must be `running`. Takes 2-5 min.

**Batch update ALL running Hives:**

```bash
# Step 1: Get latest release tag
LATEST=$(curl -s https://api.github.com/repos/xg-gh-25/SwarmAI/releases/latest \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")
echo "Latest version: $LATEST"

# Step 2: Update each running instance
PORT=$(python3 -c "import json; print(json.load(open('$HOME/.swarm-ai/backend.json'))['port'])")
curl -s "http://127.0.0.1:$PORT/api/hive/instances" | python3 -c "
import sys, json, subprocess
for h in json.load(sys.stdin):
    if h['status'] == 'running':
        ver = h.get('version', '?')
        if ver == '$LATEST':
            print(f\"  {h['name']:20s} already on v$LATEST — skip\")
            continue
        print(f\"  {h['name']:20s} v{ver} → v$LATEST ...\", end=' ', flush=True)
        r = subprocess.run(['curl', '-s', '-X', 'POST',
            f\"http://127.0.0.1:$PORT/api/hive/instances/{h['id']}/update\",
            '-H', 'Content-Type: application/json',
            '-d', '{\"version\": \"$LATEST\"}'], capture_output=True, text=True)
        print('✓' if '\"status\"' in r.stdout else f'✗ {r.stdout}')
    else:
        print(f\"  {h['name']:20s} {h['status']} — skip (not running)\")
"
```

### 4. Start / Stop

```bash
# Stop (EC2 stops, EIP retained, no compute cost)
curl -s -X POST "$BASE/instances/<id>/stop"

# Start (resumes from stopped)
curl -s -X POST "$BASE/instances/<id>/start"
```

### 5. Get Credentials

```bash
curl -s "$BASE/instances/<id>/credentials"
# → {"auth_user": "admin", "auth_password": "xxxxxx"}
```

### 6. Reset Password

```bash
# ⚠️ Confirm with user — invalidates previous password
curl -s -X POST "$BASE/instances/<id>/reset-password"
# → {"auth_user": "admin", "auth_password": "<new>"}
```

### 7. Add AWS Account

```bash
# Access keys method
curl -s -X POST "$BASE/accounts" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "123456789012",
    "label": "personal",
    "auth_method": "access_keys",
    "auth_config": {
      "access_key_id": "AKIA...",
      "secret_access_key": "..."
    },
    "default_region": "us-east-1"
  }'

# SSO profile method
curl -s -X POST "$BASE/accounts" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "123456789012",
    "label": "work-sso",
    "auth_method": "sso",
    "auth_config": {"profile": "my-sso-profile"},
    "default_region": "us-east-1"
  }'
```

### 8. Verify Account

```bash
curl -s -X POST "$BASE/accounts/<id>/verify"
# → {"success": true/false, "account_id": "...", "checks": {...}}
```

**Always verify before first deploy.** A failed verify saves 10 min of failed provisioning.

### 9. Health Check (Remote Hive)

```bash
curl -s "$BASE/instances/<id>/health"
# Proxies to the remote Hive's /health endpoint
```

### 10. Delete

```bash
# ⚠️ DESTRUCTIVE — confirm with user. Cleans up: EC2, EIP, SG, IAM, S3, CloudFront.
curl -s -X DELETE "$BASE/instances/<id>"
```

### 11. Retry Failed Deploy

```bash
# Only works on instances with status "error"
curl -s -X POST "$BASE/instances/<id>/retry"
```

## Common Workflows

### "Deploy a Hive for [person]"

1. `curl $BASE/accounts` → pick account
2. POST `/instances` with `name`, `owner_name`, `hive_type: "shared"`
3. Poll status every 10s until `running` (~5-10 min)
4. GET `/instances/<id>/credentials` → get password
5. Report to user: URL + user + password
6. Suggest: "share these credentials with [person]"

### "Update all Hives to latest"

1. GET GitHub latest release tag
2. GET `/instances` → filter `status == "running"` + `version != latest`
3. POST `/instances/<id>/update` for each
4. Poll versions until all match

### "Check Hive costs / shut down idle ones"

1. GET `/instances` → list all with status
2. For each running: GET `/instances/<id>/health` → check if actively used
3. Suggest stopping idle ones, show estimated savings

## Boundaries

### Always
- Confirm with user before destructive operations (delete, reset-password)
- Show cost when deploying (instance size → monthly cost)
- After deploy, poll and report until `running` with URL + credentials
- Verify AWS account before first deploy attempt

### Ask First
- Deleting a Hive (irreversible — all AWS resources destroyed)
- Resetting a password (previous password invalidated immediately)
- Deploying m7g.2xlarge or larger (~$238/mo)
- Stopping a Hive (services go offline until restarted)

### Never
- Deploy without a verified AWS account
- Expose credentials longer than necessary (show once, suggest copying)
- Send credentials to Slack or external channels without explicit approval

## Anti-Rationalization

| Agent Shortcut | Required Response |
|---|---|
| "Deploy started, user can check later" | Poll until running. Report URL + credentials. The user asked you to deploy, not to start a deploy. |
| "Update takes a while, I'll move on" | Poll until version matches. An unconfirmed update might have failed. |
| "I'll skip verification, the account probably works" | Always verify before first deploy. A bad account = 10 min wasted on failed provisioning. |
| "I'll just show the instance ID" | Show the name, URL, status, and version. Instance IDs are opaque UUIDs — useless to humans. |

## Verification

After any operation, confirm:
- [ ] Instance status is as expected (`running` / `stopped` / `pending`)
- [ ] URL is accessible (for running instances — show the actual URL)
- [ ] Credentials provided (for new deploys or password resets)
- [ ] Version matches expected (for updates)
