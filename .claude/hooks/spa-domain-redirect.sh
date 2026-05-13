#!/usr/bin/env bash
# PreToolUse hook: SPA domain redirect
#
# Problem: WebFetch on known SPA domains returns only meta tags / empty content.
# C012 pattern (5+ occurrences): agent tries WebFetch, gets partial content,
# treats it as "success" (200 + some content), never tries curl alternative.
# Text rules in EVOLUTION.md don't work because partial success ≠ "failure"
# in the agent's inference — the trigger never fires.
#
# Fix: Block WebFetch on SPA domains, provide the exact curl command instead.
# Structural prevention > behavioral correction.
#
# To add new domains: append to SPA_DOMAINS array below.

INPUT=$(cat)
URL=$(echo "$INPUT" | jq -r '.tool_input.url // ""')

# No URL? Pass through.
if [ -z "$URL" ] || [ "$URL" = "null" ]; then
  exit 0
fi

# Known SPA domains where WebFetch returns degraded/empty content.
# Each entry: "domain" — matches domain and all subdomains.
SPA_DOMAINS=(
  "xiaohongshu.com"
  "xhslink.com"
  "mp.weixin.qq.com"
  "douyin.com"
  "weibo.com"
  "zhihu.com"
  "bilibili.com"
  "toutiao.com"
)

# Extract domain from URL (strip protocol + auth + port + path + www prefix)
DOMAIN=$(echo "$URL" | sed -E 's|^https?://||' | sed -E 's|^[^@]*@||' | sed -E 's|([^:/?#]+).*|\1|' | sed 's/^www\.//')

# Check if domain matches any SPA domain (exact or subdomain)
MATCHED=""
for spa in "${SPA_DOMAINS[@]}"; do
  # Escape dots for regex safety (. → \.)
  escaped=$(echo "$spa" | sed 's/\./\\./g')
  if [ "$DOMAIN" = "$spa" ] || echo "$DOMAIN" | grep -qE "\.${escaped}$"; then
    MATCHED="$spa"
    break
  fi
done

if [ -z "$MATCHED" ]; then
  exit 0
fi

# Build the curl command with mobile UA (bypasses most SPA anti-scraping)
MOBILE_UA="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# Use jq for safe JSON construction (handles URL escaping)
jq -n \
  --arg domain "$MATCHED" \
  --arg url "$URL" \
  --arg ua "$MOBILE_UA" \
  '{
    "decision": "block",
    "reason": ("SPA domain detected: " + $domain + ". WebFetch returns degraded content (meta tags only) on this site.\n\nTry in order:\n1. curl -sL -H \"User-Agent: " + $ua + "\" \"" + $url + "\" | head -500\n2. If curl returns anti-scraping page or <200 chars of text, use browser-agent skill (Playwright with full JS rendering).\n\nExtract text content from the HTML response.")
  }' >&2

exit 2
