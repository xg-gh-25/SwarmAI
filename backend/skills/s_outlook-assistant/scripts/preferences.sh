#!/bin/bash
# Outlook Assistant - Preferences Manager
# Location: the skill scripts directorypreferences.sh

PREFS_DIR="$HOME/.config/outlook-assistant"
PREFS_FILE="$PREFS_DIR/user-preferences.md"

# Ensure directory exists
mkdir -p "$PREFS_DIR"

show_help() {
    cat << EOF
Outlook Assistant Preferences Manager

Usage: preferences.sh [command] [options]

Commands:
  view              Show current preferences
  init              Create preferences file with template
  edit              Open preferences in default editor
  set               Add a preference entry
  remove            Remove a preference entry
  validate          Check preferences file format
  json              Output preferences as JSON (for programmatic use)

Set/Remove Options:
  --section <name>  Section to modify (sender, important, category, behavior, about, folder)
  --entry "<text>"  The preference text to add/remove

Examples:
  preferences.sh view
  preferences.sh init
  preferences.sh set --section sender --entry "quip@ - Summarize and archive"
  preferences.sh set --section important --entry "manager@example.com - Direct manager"
  preferences.sh remove --section sender --entry "quip@"

EOF
}

init_preferences() {
    if [[ -f "$PREFS_FILE" ]]; then
        echo "Preferences file already exists at: $PREFS_FILE"
        echo "Use 'view' to see current preferences or 'edit' to modify."
        return 1
    fi
    
    cat > "$PREFS_FILE" << 'EOF'
# Outlook Assistant - User Preferences

This file stores your email management preferences. The AI assistant reads this
to personalize triage, cleanup, and organization recommendations.

## About Me
<!-- Your role, working hours, and context that helps the assistant understand your needs -->
- Role: 
- Working hours: 
- Primary focus areas: 

## Important People (Never Auto-Delete)
<!-- Emails from these people should never be suggested for deletion -->
<!-- Format: email@domain.com - Relationship/reason -->

## Sender Behaviors
<!-- Rules for specific senders -->
<!-- Format: sender@domain.com or sender-pattern@ - Action/rule -->
<!-- Actions: Always suggest cleanup, Summarize first, Never delete, Archive after N days -->

## Folder Rules
<!-- Special handling for specific folders -->
<!-- Format: "Folder Name" - Rule -->
- "Deleted Items" - Exclude from triage results by default

## Category Rules
<!-- How to handle Outlook categories -->
<!-- Format: "Category Name" - Rule -->

## Behavioral Preferences
<!-- General preferences for how the assistant behaves -->
- Prefer brief summaries over detailed lists
- Always show folder name in email listings
- Group by sender for batches >10

<!-- Internal: Onboarding completed -->
EOF
    
    echo "Created preferences template at: $PREFS_FILE"
    echo "Edit with: preferences.sh edit"
}

view_preferences() {
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "No preferences file found."
        echo "Run 'preferences.sh init' to create one."
        return 1
    fi
    cat "$PREFS_FILE"
}

edit_preferences() {
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "No preferences file found. Creating template first..."
        init_preferences
    fi
    ${EDITOR:-nano} "$PREFS_FILE"
}

map_section_alias() {
    local alias="$1"
    case "$alias" in
        sender|senders)
            echo "## Sender Behaviors"
            ;;
        important|vip|"never delete")
            echo "## Important People (Never Auto-Delete)"
            ;;
        category|categories)
            echo "## Category Rules"
            ;;
        behavior|behaviors|behavioural)
            echo "## Behavioral Preferences"
            ;;
        about|me)
            echo "## About Me"
            ;;
        folder|folders)
            echo "## Folder Rules"
            ;;
        *)
            echo "## $alias"
            ;;
    esac
}

set_preference() {
    local section=""
    local entry=""
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --section)
                section="$2"
                shift 2
                ;;
            --entry)
                entry="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    
    if [[ -z "$section" || -z "$entry" ]]; then
        echo "Error: Both --section and --entry are required"
        echo "Example: preferences.sh set --section sender --entry \"quip@ - Summarize\""
        return 1
    fi
    
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "No preferences file found. Creating template first..."
        init_preferences
    fi
    
    local section_header
    section_header=$(map_section_alias "$section")
    
    # Check if entry already exists (idempotent)
    if grep -qF "$entry" "$PREFS_FILE"; then
        echo "Entry already exists in preferences."
        return 0
    fi
    
    # Find section and append entry
    if grep -q "^$section_header" "$PREFS_FILE"; then
        # Use sed to append after the section header
        # Find the section, then append after any existing entries (lines starting with -)
        local temp_file
        temp_file=$(mktemp)
        awk -v section="$section_header" -v entry="- $entry" '
            BEGIN { in_section = 0; added = 0 }
            $0 == section { in_section = 1; print; next }
            in_section && /^##/ { 
                if (!added) { print entry; added = 1 }
                in_section = 0
                print
                next
            }
            in_section && /^$/ && !added {
                print entry
                added = 1
                print
                next
            }
            { print }
            END { if (in_section && !added) print entry }
        ' "$PREFS_FILE" > "$temp_file"
        mv "$temp_file" "$PREFS_FILE"
        echo "Added to $section_header: $entry"
    else
        echo "Section not found: $section_header"
        return 1
    fi
}

remove_preference() {
    local section=""
    local entry=""
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --section)
                section="$2"
                shift 2
                ;;
            --entry)
                entry="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done
    
    if [[ -z "$entry" ]]; then
        echo "Error: --entry is required"
        return 1
    fi
    
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "No preferences file found."
        return 1
    fi
    
    # Remove lines containing the entry
    local temp_file
    temp_file=$(mktemp)
    grep -vF "$entry" "$PREFS_FILE" > "$temp_file"
    mv "$temp_file" "$PREFS_FILE"
    echo "Removed entries matching: $entry"
}

validate_preferences() {
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "No preferences file found."
        return 1
    fi
    
    echo "Validating preferences file..."
    
    local errors=0
    
    # Check for required sections
    for section in "## About Me" "## Important People" "## Sender Behaviors" "## Behavioral Preferences"; do
        if ! grep -q "^$section" "$PREFS_FILE"; then
            echo "Warning: Missing section: $section"
            ((errors++))
        fi
    done
    
    # Check file size (should be under 500 lines for context window)
    local lines
    lines=$(wc -l < "$PREFS_FILE")
    if [[ $lines -gt 500 ]]; then
        echo "Warning: File has $lines lines (recommended: <500)"
        ((errors++))
    fi
    
    if [[ $errors -eq 0 ]]; then
        echo "Preferences file is valid."
    else
        echo "Found $errors warnings."
    fi
}

prefs_to_json() {
    if [[ ! -f "$PREFS_FILE" ]]; then
        echo "{}"
        return
    fi
    
    # Simple extraction of bullet points by section
    python3 << 'PYEOF' "$PREFS_FILE"
import sys
import json
import re

prefs_file = sys.argv[1]
result = {
    "about": [],
    "important_people": [],
    "sender_behaviors": [],
    "folder_rules": [],
    "category_rules": [],
    "behavioral_preferences": []
}

current_section = None
section_map = {
    "## About Me": "about",
    "## Important People (Never Auto-Delete)": "important_people",
    "## Sender Behaviors": "sender_behaviors",
    "## Folder Rules": "folder_rules",
    "## Category Rules": "category_rules",
    "## Behavioral Preferences": "behavioral_preferences"
}

with open(prefs_file, 'r') as f:
    for line in f:
        line = line.strip()
        for header, key in section_map.items():
            if line.startswith(header.split()[0] + " " + header.split()[1] if len(header.split()) > 1 else header):
                if header in line or header.split("(")[0].strip() in line:
                    current_section = key
                    break
        if current_section and line.startswith("- ") and not line.startswith("<!-- "):
            entry = line[2:].strip()
            if entry and not entry.startswith("Role:") or ":" in entry:
                result[current_section].append(entry)

print(json.dumps(result, indent=2))
PYEOF
}

# Main command handler
case "${1:-}" in
    view)
        view_preferences
        ;;
    init)
        init_preferences
        ;;
    edit)
        edit_preferences
        ;;
    set)
        shift
        set_preference "$@"
        ;;
    remove)
        shift
        remove_preference "$@"
        ;;
    validate)
        validate_preferences
        ;;
    json)
        prefs_to_json
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
