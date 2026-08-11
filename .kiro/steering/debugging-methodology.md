# Debugging Methodology

Rules for diagnosing issues in this project. Learned from real incidents.

## Shell Variable Issues

1. **Empty/undefined variable → git blame FIRST, then fix**
   - `grep -rn "VAR_NAME=" **/*.sh` to find if it's defined anywhere
   - `git log --all -p -- <file> | grep VAR_NAME` to find when it was removed/renamed
   - Understand WHY it's missing before adding a definition
   - Never duplicate path definitions — find the single source of truth

2. **Single source of truth for paths**
   - `scripts/daemon-lib.sh` owns: BACKEND_BUNDLE_DIR, BACKEND_BINARY, DAEMON_DIR, DAEMON_BINARY, DAEMON_PORT
   - `dev.sh` and `prod.sh` source it — never redefine these variables in them

## Daemon / launchd Issues

3. **"Backend failed to start" diagnosis order:**
   1. `nc -z 127.0.0.1 18321` — is port responding?
   2. `launchctl list | grep swarmai` — is the agent loaded? (PID column = running)
   3. `tail -20 ~/.swarm-ai/logs/backend-daemon.log` — startup errors?
   4. `tail -20 ~/.swarm-ai/logs/backend-stderr.log` — crash output?
   5. If agent not loaded: `launchctl load ~/Library/LaunchAgents/com.swarmai.backend.plist`

## General Debugging Discipline

4. **Don't say "transient" without evidence**
   - If the user can reproduce it, it's not transient
   - Reproduce it yourself or trace the exact code path

5. **Trace errors to their source, don't patch symptoms**
   - `PermissionError: ''` → who passed the empty string? → which variable? → where should it be defined?
   - Follow the call chain backwards, don't guess forward

6. **Refactoring verification**
   - When a variable is renamed/moved: grep ALL consumers, not just the definition
   - `set -u` in scripts catches undefined variables — recommend adding it
