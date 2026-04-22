# tmux Session Control

**Why?** Run and monitor long-running processes, orchestrate multiple agent sessions in parallel, and interact with interactive CLIs -- all without blocking your main session.

**Platform:** macOS, Linux. Requires `tmux` (installed via `brew install tmux`).

---

## Quick Start

```
"Start a background build and let me know when it's done"
"Check what's running in my tmux sessions"
"Send Ctrl+C to the stuck process in session worker-2"
```

---

## When to Use vs Not Use

| Use tmux skill | Use Bash tool directly |
|---|---|
| Monitor a long-running process | Run a quick command |
| Send keystrokes to interactive TUI | Execute a script |
| Capture output from a running session | One-off shell command |
| Orchestrate parallel worker sessions | Non-interactive pipeline |
| Approve prompts in agent sessions | Read a file |

---

## Core Commands

### Session Management

```bash
# List all sessions
tmux list-sessions

# Create a new named session (detached)
tmux new-session -d -s "worker-1"

# Create session and run a command in it
tmux new-session -d -s "build" "npm run build"

# Kill a session
tmux kill-session -t "worker-1"

# Rename a session
tmux rename-session -t "old-name" "new-name"
```

### Capturing Output

```bash
# Capture visible pane content
tmux capture-pane -t "session-name" -p

# Capture with scrollback history (last 100 lines)
tmux capture-pane -t "session-name" -p -S -100

# Capture specific pane in a session
tmux capture-pane -t "session-name:0.0" -p

# Capture and save to file
tmux capture-pane -t "session-name" -p > /tmp/session-output.txt
```

**Target format:** `session:window.pane`
- `worker-1` -- default window and pane
- `worker-1:0` -- window 0, default pane
- `worker-1:0.1` -- window 0, pane 1

### Sending Input

```bash
# Send text (without pressing Enter)
tmux send-keys -t "session-name" -l "echo hello"

# Send Enter key
tmux send-keys -t "session-name" Enter

# Send text + Enter (two steps for reliability)
tmux send-keys -t "session-name" -l "npm test" && sleep 0.3 && tmux send-keys -t "session-name" Enter

# Send Ctrl+C (interrupt)
tmux send-keys -t "session-name" C-c

# Send Ctrl+D (EOF)
tmux send-keys -t "session-name" C-d

# Send Escape
tmux send-keys -t "session-name" Escape

# Send arrow keys
tmux send-keys -t "session-name" Up
tmux send-keys -t "session-name" Down

# Send Tab (autocomplete)
tmux send-keys -t "session-name" Tab

# Type 'y' to confirm a prompt
tmux send-keys -t "session-name" -l "y" && sleep 0.1 && tmux send-keys -t "session-name" Enter
```

**Important:** Always use `-l` (literal) flag when sending text to avoid tmux interpreting characters as special keys. Separate text from Enter with a short sleep for reliability.

### Window and Pane Navigation

```bash
# List windows in a session
tmux list-windows -t "session-name"

# List panes in a window
tmux list-panes -t "session-name:0"

# Select a window
tmux select-window -t "session-name:2"

# Split pane horizontally
tmux split-window -h -t "session-name"

# Split pane vertically
tmux split-window -v -t "session-name"
```

---

## Workflow

### Step 1: Assess What's Needed

| User Request | Action |
|---|---|
| "Run X in background" | Create session, run command |
| "Check on my build" | Capture output from session |
| "What sessions are running?" | List sessions |
| "Kill the stuck process" | Send Ctrl+C or kill session |
| "Send Y to that prompt" | Send keys to session |
| "Run 3 tasks in parallel" | Create multiple sessions |

### Step 2: Execute

For new sessions, always use descriptive names:
```bash
tmux new-session -d -s "build-frontend" "cd /project && npm run build"
```

For monitoring, capture and summarize:
```bash
tmux capture-pane -t "build-frontend" -p -S -50
```

### Step 3: Report Back

When monitoring a process, summarize the output:
```
Session "build-frontend" status:
- Running: npm run build
- Last output: "Compiled successfully in 4.2s"
- Status: Complete, no errors
```

---

## Common Patterns

### Long-Running Build

```bash
# Start build in background
tmux new-session -d -s "build" "cd /project && make all 2>&1 | tee /tmp/build.log"

# Check progress periodically
tmux capture-pane -t "build" -p -S -20

# When done, report and cleanup
tmux kill-session -t "build"
```

### Parallel Agent Workers

```bash
# Spin up multiple workers
for i in 1 2 3; do
  tmux new-session -d -s "worker-$i"
done

# Send task to each worker
tmux send-keys -t "worker-1" -l "cd /repo && run-task task1.json" && sleep 0.1 && tmux send-keys -t "worker-1" Enter
tmux send-keys -t "worker-2" -l "cd /repo && run-task task2.json" && sleep 0.1 && tmux send-keys -t "worker-2" Enter
tmux send-keys -t "worker-3" -l "cd /repo && run-task task3.json" && sleep 0.1 && tmux send-keys -t "worker-3" Enter

# Monitor all workers
for i in 1 2 3; do
  echo "=== Worker $i ===" && tmux capture-pane -t "worker-$i" -p -S -5
done
```

### Approve Interactive Prompts

```bash
# Check if a prompt is waiting
OUTPUT=$(tmux capture-pane -t "session" -p -S -5)
echo "$OUTPUT"

# If prompt detected, approve
tmux send-keys -t "session" -l "y" && sleep 0.1 && tmux send-keys -t "session" Enter
```

### Dev Server in Background

```bash
# Start dev server
tmux new-session -d -s "devserver" "cd /project && npm run dev"

# Check it's running
sleep 3 && tmux capture-pane -t "devserver" -p -S -10

# Stop later
tmux send-keys -t "devserver" C-c
tmux kill-session -t "devserver"
```

---

## Session Naming Conventions

| Pattern | Use |
|---|---|
| `build-*` | Build processes |
| `worker-N` | Parallel task workers |
| `dev-*` | Development servers |
| `agent-*` | AI agent sessions |
| `test-*` | Test runners |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "no server running" | No tmux sessions exist. Create one first |
| "session not found" | Check name with `tmux list-sessions` |
| Send-keys not working | Use `-l` flag for literal text. Separate text from Enter |
| Can't capture output | Ensure session exists and pane index is correct |
| Session dies immediately | Command may have exited. Use `bash -c "cmd; read"` to keep alive |
| Garbled output | Terminal encoding issue. Try `tmux capture-pane -t sess -p -e` to strip escapes |
| Process stuck | Send `C-c` first. If unresponsive, `tmux kill-session -t sess` |

---

## Quality Rules

- Always use descriptive session names, not defaults
- Use `-l` flag with send-keys for all text input
- Separate text input from Enter keypress with a short sleep (0.1-0.3s)
- When monitoring, show only relevant recent output (last 10-20 lines), not full history
- Summarize output for the user -- don't dump raw terminal content
- Clean up sessions when tasks complete (`kill-session`)
- For parallel tasks, report status of all workers together
- Never leave orphaned sessions running -- track what was created

