#!/bin/bash
# Outlook Assistant - Deletion Log Manager

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$SKILL_DIR/data"
LOG_FILE="$DATA_DIR/deletion-log.json"

mkdir -p "$DATA_DIR"

# Migrate legacy data from ~/.config/outlook-assistant/ if present
LEGACY_LOG="$HOME/.config/outlook-assistant/deletion-log.json"
if [[ -f "$LEGACY_LOG" && ! -f "$LOG_FILE" ]]; then
    cp "$LEGACY_LOG" "$LOG_FILE"
elif [[ -f "$LEGACY_LOG" && -f "$LOG_FILE" ]]; then
    # Merge: append legacy entries not already present
    python3 -c "
import json
with open('$LOG_FILE') as f: current = json.load(f)
with open('$LEGACY_LOG') as f: legacy = json.load(f)
existing_ids = {str(e.get('id')) for e in current}
merged = current + [e for e in legacy if str(e.get('id')) not in existing_ids]
if len(merged) > len(current):
    with open('$LOG_FILE','w') as f: json.dump(merged,f,indent=2)
" 2>/dev/null
fi

[[ ! -f "$LOG_FILE" ]] && echo "[]" > "$LOG_FILE"

show_help() {
    cat << EOF
Outlook Assistant Deletion Log Manager

Usage: deletion-log.sh [command] [options]

Commands:
  view [--count N]      Show recent deletions (default: 20)
  add                   Add a deletion entry
  search <query>        Search deletions by subject or sender
  stats                 Show deletion statistics
  clear [--older N]     Clear log entries (optionally older than N days)
  last [N]              Get last N deletion IDs (for restore)

Add Options:
  --id <id>             Email ID (required)
  --subject "<text>"    Email subject (required)
  --sender "<email>"    Sender email (required)
  --folder "<name>"     Original folder name

Examples:
  deletion-log.sh view --count 10
  deletion-log.sh add --id 47951 --subject "Test" --sender "test@example.com"
  deletion-log.sh search "linkedin"
  deletion-log.sh last 5

EOF
}

view_log() {
    local count=20
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --count) count="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    
    [[ "$(cat "$LOG_FILE")" == "[]" ]] && echo "No deletions logged." && return 0
    
    python3 -c "
import json
from datetime import datetime
with open('$LOG_FILE') as f: log = json.load(f)
log.sort(key=lambda x: x.get('deletedAt',''), reverse=True)
for e in log[:$count]:
    dt = e.get('deletedAt','?')
    try: dt = datetime.fromisoformat(dt.replace('Z','+00:00')).strftime('%Y-%m-%d %H:%M')
    except: pass
    print(f\"ID: {e.get('id')} | {e.get('sender','')} | {e.get('subject','')[:50]} | {dt}\")
"
}

add_entry() {
    local id="" subject="" sender="" folder="Inbox"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --id) id="$2"; shift 2 ;;
            --subject) subject="$2"; shift 2 ;;
            --sender) sender="$2"; shift 2 ;;
            --folder) folder="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    
    [[ -z "$id" || -z "$subject" || -z "$sender" ]] && echo "Error: --id, --subject, --sender required" && return 1
    
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")
    python3 -c "
import json
with open('$LOG_FILE') as f: log = json.load(f)
log.append({'deletedAt':'$ts','id':$id,'subject':'''$subject''','sender':'$sender','folder':'$folder'})
with open('$LOG_FILE','w') as f: json.dump(log,f,indent=2)
print('Logged deletion: $id')
"
}

search_log() {
    local query="$1"
    [[ -z "$query" ]] && echo "Usage: deletion-log.sh search <query>" && return 1
    python3 -c "
import json
with open('$LOG_FILE') as f: log = json.load(f)
q = '$query'.lower()
for e in log:
    if q in e.get('subject','').lower() or q in e.get('sender','').lower():
        print(f\"ID: {e.get('id')} | {e.get('sender')} | {e.get('subject')[:50]}\")
"
}

show_stats() {
    python3 -c "
import json
from collections import Counter
from datetime import datetime, timedelta
with open('$LOG_FILE') as f: log = json.load(f)
print(f'Total deletions logged: {len(log)}')
if log:
    senders = Counter(e.get('sender','').split('@')[-1] for e in log)
    print('\\nTop domains deleted from:')
    for domain,cnt in senders.most_common(10):
        print(f'  {domain}: {cnt}')
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    recent = [e for e in log if e.get('deletedAt','') > week_ago]
    print(f'\\nLast 7 days: {len(recent)} deletions')
"
}

clear_log() {
    local older_days=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --older) older_days="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    
    if [[ -z "$older_days" ]]; then
        echo "[]" > "$LOG_FILE"
        echo "Cleared all deletion logs."
    else
        python3 -c "
import json
from datetime import datetime, timedelta
with open('$LOG_FILE') as f: log = json.load(f)
cutoff = (datetime.utcnow() - timedelta(days=$older_days)).isoformat()
kept = [e for e in log if e.get('deletedAt','') > cutoff]
removed = len(log) - len(kept)
with open('$LOG_FILE','w') as f: json.dump(kept,f,indent=2)
print(f'Removed {removed} entries older than $older_days days')
"
    fi
}

get_last() {
    local count="${1:-5}"
    python3 -c "
import json
with open('$LOG_FILE') as f: log = json.load(f)
log.sort(key=lambda x: x.get('deletedAt',''), reverse=True)
ids = [str(e.get('id')) for e in log[:$count]]
print(' '.join(ids))
"
}

case "${1:-}" in
    view) shift; view_log "$@" ;;
    add) shift; add_entry "$@" ;;
    search) shift; search_log "$@" ;;
    stats) show_stats ;;
    clear) shift; clear_log "$@" ;;
    last) shift; get_last "$@" ;;
    help|--help|-h|"") show_help ;;
    *) echo "Unknown: $1"; show_help; exit 1 ;;
esac
