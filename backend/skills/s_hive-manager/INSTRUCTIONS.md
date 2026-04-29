# Hive Manager

Manage SwarmAI Hive cloud instances via the backend REST API. All operations
go through `curl` to the local backend — no direct AWS SDK calls needed.

## API Base

```bash
# Discover backend port (random each launch)
PORT=$(python -c "
import psutil
for p in psutil.process_iter(['name']):
    if 'python-backend' in (p.info['name'] or ''):
        for c in p.net_connections():
            if c.status == 'LISTEN':
                print(c.laddr.port); break
        break
" 2>/dev/null || echo 8000)
BASE="http://127.0.0.1:$PORT/api/hive"
```

In practice, use the Bash tool with `curl` directly. The backend is always on localhost.

## Operations

### 1. List Hive Instances

```bash
curl -s "$BASE/instances" | python -m json.tool
```

Shows all Hives with status, URL, region, version. No credentials in list response.

### 2. Deploy a New Hive

**Prerequisites:**
- At least one AWS account configured (see §7 below)
- Account must be verified (see §8)

**Steps:**

```bash
# 1. List accounts to get the account_ref
curl -s "$BASE/accounts"

# 2. Deploy
curl -s -X POST "$BASE/instances" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<lowercase-hyphenated-name>",
    "account_ref": "<account-id-from-step-1>",
    "region": "us-east-1",
    "instance_type": "m7g.xlarge",
    "owner_name": "<optional: for shared hives>",
    "hive_type": "<my|shared>"
  }'
```

**Name rules:** 2-63 chars, lowercase letters/numbers/hyphens, must start with a letter.

**Instance sizes:**
| Size | RAM | Cost |
|------|-----|------|
| m7g.large | 8 GB | ~$60/mo |
| m7g.xlarge | 16 GB (recommended) | ~$119/mo |
| m7g.2xlarge | 32 GB | ~$238/mo |

**After deploy:** Status goes through `pending → provisioning → installing → running`.
Poll status every 5-10s until `running`:

```bash
curl -s "$BASE/instances/<id>" | python -c "import sys,json; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]}  URL: {d.get(\"cloudfront_domain\",\"pending\")}')"
```

Provisioning takes 5-10 minutes. CloudFront HTTPS takes an additional 5-15 minutes.

### 3. Update a Hive to New Version

```bash
# Get current version
curl -s "$BASE/instances" | python -c "
import sys,json
for h in json.load(sys.stdin):
    print(f'{h[\"name\"]:20s} v{h.get(\"version\",\"?\")} ({h[\"status\"]})')
"

# Update to specific version (must be running)
curl -s -X POST "$BASE/instances/<id>/update" \
  -H "Content-Type: application/json" \
  -d '{"version": "1.9.0"}'
```

**Update uses SSM Run Command** — runs on the EC2 instance without SSH. The
instance must be `running`. Update takes 2-5 minutes.

To update ALL running Hives to the latest version:

```bash
# Get latest release tag from GitHub
LATEST=$(curl -s https://api.github.com/repos/xg-gh-25/SwarmAI/releases/latest | python -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")
echo "Latest: $LATEST"

# Update all running instances
curl -s "$BASE/instances" | python -c "
import sys,json,subprocess
for h in json.load(sys.stdin):
    if h['status'] == 'running':
        print(f'Updating {h[\"name\"]} from v{h.get(\"version\",\"?\")} to v$LATEST...')
        subprocess.run(['curl', '-s', '-X', 'POST',
            f'$BASE/instances/{h[\"id\"]}/update',
            '-H', 'Content-Type: application/json',
            '-d', json.dumps({'version': '$LATEST'})], check=True)
        print('  ✓ Update initiated')
"
```

### 4. Start / Stop an Instance

```bash
# Stop (saves cost — EC2 stops, EIP retained)
curl -s -X POST "$BASE/instances/<id>/stop"

# Start (resumes from stopped state)
curl -s -X POST "$BASE/instances/<id>/start"
```

### 5. Get Credentials

```bash
curl -s "$BASE/instances/<id>/credentials"
# Returns: {"auth_user": "admin", "auth_password": "..."}
```

### 6. Reset Password

```bash
curl -s -X POST "$BASE/instances/<id>/reset-password"
# Returns: {"auth_user": "admin", "auth_password": "<new-password>"}
```

### 7. Add AWS Account

```bash
# With access keys
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

# With SSO profile
curl -s -X POST "$BASE/accounts" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "123456789012",
    "label": "work",
    "auth_method": "sso",
    "auth_config": {"profile": "my-sso-profile"},
    "default_region": "us-east-1"
  }'
```

### 8. Verify AWS Account

```bash
curl -s -X POST "$BASE/accounts/<id>/verify"
# Returns: {"success": true/false, "checks": {...}}
```

### 9. Health Check (Remote Hive)

```bash
curl -s "$BASE/instances/<id>/health"
# Proxies health check to remote Hive instance
```

### 10. Delete a Hive

```bash
# ⚠️ DESTRUCTIVE — always confirm with user first
curl -s -X DELETE "$BASE/instances/<id>"
# Cleans up: EC2, EIP, SG, IAM Role, S3, CloudFront
```

### 11. Retry Failed Deploy

```bash
curl -s -X POST "$BASE/instances/<id>/retry"
```

## Common Workflows

### "Deploy a Hive for Titus"

```
1. List accounts → pick the right one
2. Deploy: name="titus-hive", owner_name="Titus Tian", hive_type="shared"
3. Poll until running (~5-10 min)
4. Get credentials
5. Share: URL + user + password
```

### "Update all Hives to latest"

```
1. Get latest release from GitHub
2. List instances → filter running
3. POST /update for each with the version
4. Poll until all versions match
```

### "Shut down unused Hives to save cost"

```
1. List instances
2. For each running instance: check last activity (health proxy)
3. Stop idle ones
```

## Boundaries

### Always
- Confirm with user before destructive operations (delete, reset-password)
- Show the cost implication when deploying (instance size → monthly cost)
- After deploy, wait and report until status is `running` with URL

### Ask First
- Deleting a Hive (irreversible — AWS resources destroyed)
- Resetting a password (previous password invalidated)
- Deploying m7g.2xlarge or larger (expensive)

### Never
- Deploy without an AWS account configured and verified
- Stop a Hive without confirming the user wants to (data on EBS persists, but services stop)
- Expose credentials in chat history beyond the immediate response (suggest copying)

## Anti-Rationalization

| Agent Shortcut | Required Response |
|---|---|
| "Deploy is started, user can check later" | Poll until running. Report URL + credentials. Don't leave the user guessing. |
| "Update takes a while, I'll move on" | Poll until version matches. Confirm the update landed. |
| "I'll skip verification, the account probably works" | Always verify before first deploy. A failed deploy wastes 10 min. |

## Verification

After any operation, confirm:
- [ ] Instance status is as expected (running/stopped/pending)
- [ ] URL is accessible (for running instances)
- [ ] Credentials are provided (for new deploys or password resets)
- [ ] Version matches expected (for updates)
