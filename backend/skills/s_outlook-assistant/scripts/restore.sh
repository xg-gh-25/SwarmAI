#!/bin/bash
# Outlook Assistant - Restore Helper
# Location: the skill scripts directoryrestore.sh
#
# This script helps find deleted emails and provides instructions
# for restoring them via Outlook MCP's move_email tool.

CONFIG_DIR="$HOME/.config/outlook-assistant"
LOG_FILE="$CONFIG_DIR/deletion-log.json"

show_help() {
    cat << EOF
Outlook Assistant Restore Helper

This script helps identify emails to restore from Deleted Items.
Since Outlook MCP's delete_email moves to Deleted Items (not permanent delete),
emails can be restored by moving them back to their original folder.

Usage: restore.sh [command] [options]

Commands:
  last [N]              Show last N deleted emails with restore info
  find <query>          Search deletion log for emails to restore
  instructions <id>     Show MCP command to restore specific email

Examples:
  restore.sh last 5
  restore.sh find "linkedin"
  restore.sh instructions 47951

Restore Process:
1. Find the email ID from deletion log
2. Use Outlook MCP move_email tool:
   - message_id: <email_id>
   - destination_folder_name: "Inbox" (or original folder)

EOF
}

show_last() {
    local count="${1:-5}"
    
    if [[ ! -f "$LOG_FILE" ]] || [[ "$(cat "$LOG_FILE")" == "[]" ]]; then
        echo "No deletions logged yet."
        return 0
    fi
    
    echo "Last $count deleted emails:"
    echo "============================"
    
    python3 -c "
import json
from datetime import datetime

with open('$LOG_FILE') as f:
    log = json.load(f)

log.sort(key=lambda x: x.get('deletedAt', ''), reverse=True)

for e in log[:$count]:
    dt = e.get('deletedAt', '?')
    try:
        dt = datetime.fromisoformat(dt.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
    except:
        pass
    
    print(f\"\"\"
ID: {e.get('id')}
  Subject: {e.get('subject', '')[:60]}
  From: {e.get('sender', '')}
  Original Folder: {e.get('folder', 'Inbox')}
  Deleted: {dt}
  
  To restore, use MCP move_email:
    message_id: {e.get('id')}
    destination_folder_name: \"{e.get('folder', 'Inbox')}\"
\"\"\")
"
}

find_email() {
    local query="$1"
    
    if [[ -z "$query" ]]; then
        echo "Usage: restore.sh find <query>"
        return 1
    fi
    
    if [[ ! -f "$LOG_FILE" ]] || [[ "$(cat "$LOG_FILE")" == "[]" ]]; then
        echo "No deletions logged yet."
        return 0
    fi
    
    echo "Searching for: $query"
    echo "========================"
    
    python3 -c "
import json

with open('$LOG_FILE') as f:
    log = json.load(f)

q = '$query'.lower()
matches = [e for e in log if q in e.get('subject', '').lower() or q in e.get('sender', '').lower()]

if not matches:
    print('No matches found in deletion log.')
else:
    print(f'Found {len(matches)} match(es):')
    for e in matches:
        print(f\"\"\"
ID: {e.get('id')}
  Subject: {e.get('subject', '')[:60]}
  From: {e.get('sender', '')}
  Original Folder: {e.get('folder', 'Inbox')}
\"\"\")
"
}

show_instructions() {
    local email_id="$1"
    
    if [[ -z "$email_id" ]]; then
        echo "Usage: restore.sh instructions <email_id>"
        return 1
    fi
    
    if [[ ! -f "$LOG_FILE" ]]; then
        echo "No deletion log found."
        return 1
    fi
    
    python3 -c "
import json

with open('$LOG_FILE') as f:
    log = json.load(f)

email = next((e for e in log if str(e.get('id')) == '$email_id'), None)

if not email:
    print(f'Email ID $email_id not found in deletion log.')
    print('Note: The email might still be in Deleted Items - search there directly.')
else:
    folder = email.get('folder', 'Inbox')
    print(f'''
Found in deletion log:
  Subject: {email.get('subject', '')[:60]}
  From: {email.get('sender', '')}
  Original Folder: {folder}

To restore via Outlook MCP, use move_email with:
{{
  \"message_id\": \"$email_id\",
  \"destination_folder_name\": \"{folder}\"
}}

Or ask the AI assistant:
  \"Restore email $email_id to {folder}\"
''')
"
}

case "${1:-}" in
    last)
        shift
        show_last "$@"
        ;;
    find)
        shift
        find_email "$@"
        ;;
    instructions)
        shift
        show_instructions "$@"
        ;;
    help|--help|-h|"")
        show_help
        ;;
    *)
        echo "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
