# Security Architecture

This document describes the comprehensive security model implemented in the Agent Skill Platform to ensure safe, isolated execution of AI agents with fine-grained access control.

## Table of Contents

- [Overview](#overview)
- [Security Layers](#security-layers)
- [Agent Workspace Isolation](#agent-workspace-isolation)
- [File Access Control](#file-access-control)
- [Skill Access Control](#skill-access-control)
- [Bash Command Protection](#bash-command-protection)
- [Security Configuration](#security-configuration)
- [Testing and Validation](#testing-and-validation)
- [Known Limitations](#known-limitations)
- [Best Practices](#best-practices)

---

## Overview

The platform implements a **defense-in-depth** security model with multiple layers of protection to ensure agents:
- ✅ Can only access their authorized workspace
- ✅ Can only use their permitted skills
- ✅ Cannot access other agents' data
- ✅ Cannot access system files or main workspace
- ✅ Cannot bypass restrictions via bash commands

### Security Principles

1. **Isolation by Default**: Each agent operates in an isolated workspace
2. **Least Privilege**: Agents only access what they explicitly need
3. **Defense in Depth**: Multiple overlapping security layers
4. **Fail Secure**: Access denied unless explicitly allowed

---

## Security Layers

The platform implements **four security layers** that work together:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 4: Bash Command Protection (NEW)                 │
│ └─ Parse & validate file paths in bash commands        │
├─────────────────────────────────────────────────────────┤
│ Layer 3: File Tool Access Control                      │
│ └─ Validate Read/Write/Edit/Glob/Grep operations       │
├─────────────────────────────────────────────────────────┤
│ Layer 2: Skill Access Control                          │
│ └─ PreToolUse hook validates Skill tool invocations    │
├─────────────────────────────────────────────────────────┤
│ Layer 1: Workspace Isolation                           │
│ └─ Per-agent workspace with symlinked skills           │
└─────────────────────────────────────────────────────────┘
```

### Layer 1: Workspace Isolation

**Location**: `backend/core/workspace_manager.py`

Each agent with skills enabled gets an isolated workspace **outside the project tree**:

```
Project Tree:
/home/ubuntu/.../workspace/          ← Main workspace (skill storage)
└── .claude/skills/                  ← All available skills
    ├── skill-1/
    ├── skill-2/
    └── skill-3/

Isolated Agent Workspaces:
<app_data_dir>/workspaces/           ← Per-agent workspaces (persistent)
└── {agent_id}/
    └── .claude/skills/              ← Symlinks to authorized skills only
        ├── skill-1 -> /path/to/workspace/.claude/skills/skill-1
        └── skill-2 -> /path/to/workspace/.claude/skills/skill-2
```

**Key Features**:
- Workspaces created outside project tree prevent parent directory traversal
- Absolute symlinks ensure correct skill resolution
- `allow_all_skills=True`: All skills are symlinked
- `allow_all_skills=False`: Only specified skills are symlinked

**Implementation** (`agent_manager.py:326-338`):
```python
if enable_skills and agent_id:
    # Use per-agent workspace with symlinked skills
    working_directory = str(workspace_manager.get_agent_workspace(agent_id))
    logger.info(f"Using per-agent workspace: {working_directory}")
else:
    # Use default workspace (no skills or no agent_id)
    working_directory = agent_config.get("working_directory") or settings.agent_workspace_dir
```

### Layer 2: Skill Access Control

**Location**: `backend/core/agent_manager.py:163-207`, `316-324`

PreToolUse hook validates that agents only invoke authorized skills.

**When Active**: Only when `allow_all_skills=False`

**Implementation**:
```python
async def skill_access_checker(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    if input_data.get('tool_name') == 'Skill':
        requested_skill = input_data.get('tool_input', {}).get('skill', '')

        if requested_skill not in allowed_skill_names:
            return {
                'hookSpecificOutput': {
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f'Skill "{requested_skill}" not authorized'
                }
            }
    return {}
```

**Protection**: Blocks unauthorized skill invocations at tool execution time.

### Layer 3: File Tool Access Control

**Location**: `backend/core/agent_manager.py:111-157`, `340-351`

The `can_use_tool` permission handler validates file paths for file operation tools.

**Protected Tools**:
- `Read` → validates `file_path` parameter
- `Write` → validates `file_path` parameter
- `Edit` → validates `file_path` parameter
- `Glob` → validates `path` parameter
- `Grep` → validates `path` parameter

**Implementation**:
```python
async def file_access_permission_handler(tool_name: str, input_data: dict, context: dict):
    if tool_name in file_tools:
        file_path = input_data.get(path_param, '')
        normalized_path = os.path.normpath(file_path)

        is_allowed = any(
            normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
            for allowed_dir in normalized_dirs
        )

        if not is_allowed:
            return {
                "behavior": "deny",
                "message": f"File access denied: {file_path} is outside allowed directories"
            }
    return {"behavior": "allow"}
```

**Configuration** (`agent_manager.py:340-351`):
```python
allowed_directories = [working_directory]
# Add any additional allowed directories from config
extra_dirs = agent_config.get("allowed_directories", [])
if extra_dirs:
    allowed_directories.extend(extra_dirs)

file_access_handler = create_file_access_permission_handler(allowed_directories)
```

### Layer 4: Bash Command Protection

**Location**: `backend/core/agent_manager.py:159-202`

Parses and validates file paths in bash commands to prevent bypassing file access controls.

**Problem Solved**: Without this layer, agents could bypass file restrictions:
```bash
# These would bypass Layers 1-3:
bash cat /etc/passwd
bash echo "data" > /tmp/other-agent/stolen.txt
bash rm /home/ubuntu/workspace/important.txt
```

**Implementation**:
```python
if tool_name == 'Bash':
    command = input_data.get('command', '')

    # Extract file paths using regex patterns
    suspicious_patterns = [
        r'\s+(/[^\s]+)',  # Absolute paths
        r'(?:cat|head|tail|less|more|nano|vi|vim|emacs)\s+([^\s|>&]+)',  # Read
        r'(?:echo|printf|tee)\s+.*?>\s*([^\s|>&]+)',  # Write
        r'(?:cp|mv|rm|mkdir|rmdir|touch)\s+.*?([^\s|>&]+)',  # File ops
    ]

    # Validate each extracted absolute path
    for file_path in potential_paths:
        if not file_path.startswith('/'):
            continue  # Relative paths are safe (use cwd)

        normalized_path = os.path.normpath(file_path)
        is_allowed = any(
            normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
            for allowed_dir in normalized_dirs
        )

        if not is_allowed:
            return {
                "behavior": "deny",
                "message": f"Bash file access denied: {file_path} outside allowed directories"
            }
```

**Protected Commands**:
- ✅ File reading: `cat`, `head`, `tail`, `less`, `more`, `nano`, `vi`, `vim`, `emacs`
- ✅ File writing: `echo >`, `printf >`, `tee`
- ✅ File operations: `cp`, `mv`, `rm`, `mkdir`, `rmdir`, `touch`
- ✅ Any absolute path references

**Allowed Commands**:
- ✅ Relative paths (protected by `cwd`)
- ✅ Commands without file access (`ls`, `pwd`, `echo "text"`)
- ✅ Operations within agent's workspace

---

## Agent Workspace Isolation

### Directory Structure

```
Main Workspace (Skill Storage):
/home/ubuntu/workspace/awesome-skills-claude-agents/workspace/
└── .claude/skills/
    ├── skill-creator/
    │   ├── SKILL.md
    │   └── scripts/
    ├── docx/
    ├── pdf/
    └── ... (all other skills)

Isolated Agent Workspaces (platform-specific app data dir):
<app_data_dir>/workspaces/
├── agent-123/
│   └── .claude/skills/
│       ├── skill-creator -> /workspace/.claude/skills/skill-creator
│       └── docx -> /workspace/.claude/skills/docx
│
└── agent-456/
    └── .claude/skills/
        └── pdf -> /workspace/.claude/skills/pdf
```

### Workspace Creation

**Automatic Creation**: Workspaces are created/rebuilt when:
1. Agent starts a conversation with `enable_skills=true`
2. Agent's skill configuration changes

**Code** (`workspace_manager.py:88-149`):
```python
async def rebuild_agent_workspace(
    agent_id: str,
    skill_ids: list[str],
    allow_all_skills: bool = False
) -> Path:
    """Rebuild an agent's workspace with symlinks to allowed skills."""

    agent_workspace = self.get_agent_workspace(agent_id)
    agent_skills_dir = self.get_agent_skills_dir(agent_id)

    # Remove existing skills directory and recreate
    if agent_skills_dir.exists():
        shutil.rmtree(agent_skills_dir)
    agent_skills_dir.mkdir(parents=True, exist_ok=True)

    # Determine which skills to link
    if allow_all_skills:
        skill_names = await self.get_all_skill_names()
    else:
        skill_names = [await self.get_skill_name_by_id(sid) for sid in skill_ids]

    # Create absolute symlinks
    for skill_name in skill_names:
        source_path = self.main_skills_dir / skill_name
        if source_path.exists():
            target_path = agent_skills_dir / skill_name
            target_path.symlink_to(source_path.resolve())

    return agent_workspace
```

### Benefits

1. **Physical Isolation**: Each agent's files are truly separate
2. **No Parent Directory Leaks**: Skills can't discover unauthorized skills via `../`
3. **Granular Control**: Different agents see different skills
4. **Easy Cleanup**: Delete workspace to remove all agent data

---

## File Access Control

### Configuration

File access control is enabled by default and configured in `agent_manager.py:340-351`:

```python
file_access_enabled = agent_config.get("enable_file_access_control", True)
file_access_handler = None

if file_access_enabled:
    allowed_directories = [working_directory]

    # Add any additional allowed directories from config
    extra_dirs = agent_config.get("allowed_directories", [])
    if extra_dirs:
        allowed_directories.extend(extra_dirs)

    file_access_handler = create_file_access_permission_handler(allowed_directories)
    logger.info(f"File access control enabled, allowed directories: {allowed_directories}")
```

### Path Validation

The handler uses `os.path.normpath()` to normalize paths and prevent traversal attacks:

```python
normalized_path = os.path.normpath(file_path)
# /tmp/agent-123/../agent-456/file.txt → /tmp/agent-456/file.txt (DENIED)
```

### Logging

All file access attempts are logged for security auditing:

```
[FILE ACCESS ALLOWED] Tool: Read, Path: /tmp/agent-123/test.txt
[FILE ACCESS DENIED] Tool: Write, Path: /etc/passwd, Allowed: ['/tmp/agent-123']
[BASH FILE ACCESS DENIED] Command: cat /etc/passwd, Path: /etc/passwd
```

---

## Skill Access Control

### Hook-Based Validation

When `allow_all_skills=False`, a PreToolUse hook validates skill invocations:

```python
if enable_skills and not allow_all_skills:
    skill_checker = create_skill_access_checker(allowed_skill_names)
    hooks["PreToolUse"].append(
        HookMatcher(matcher="Skill", hooks=[skill_checker])
    )
    logger.info(f"Skill access checker hook added for skills: {allowed_skill_names}")
```

### Dual Protection

Skill access is protected by **two mechanisms**:

1. **Symlink Filtering** (Layer 1): Unauthorized skills are not symlinked to agent workspace
2. **Hook Validation** (Layer 2): Even if symlinked, invocation is blocked

This provides redundant protection against configuration errors.

---

## Bash Command Protection

### Regex Patterns

The bash command parser uses these patterns to extract file paths:

```python
suspicious_patterns = [
    r'\s+(/[^\s]+)',  # Match absolute paths anywhere
    r'(?:cat|head|tail|less|more|nano|vi|vim|emacs)\s+([^\s|>&]+)',  # Read commands
    r'(?:echo|printf|tee)\s+.*?>\s*([^\s|>&]+)',  # Write redirections
    r'(?:cp|mv|rm|mkdir|rmdir|touch)\s+.*?([^\s|>&]+)',  # File operations
]
```

### Test Cases

All these dangerous commands are **blocked**:

```bash
cat /etc/passwd                      # Read system file
head /var/log/syslog                 # Read system logs
cat /home/ubuntu/workspace/secret.txt # Read main workspace
cat /tmp/other-agent/data.txt        # Read other agent's data
echo "malicious" > /tmp/evil.txt     # Write outside workspace
rm /etc/hosts                        # Delete system file
cp /etc/shadow /tmp/stolen.txt       # Copy system file
```

All these safe commands are **allowed**:

```bash
ls -la                               # List current directory
pwd                                  # Print working directory
echo "Hello World"                   # Echo without file access
cat relative/path.txt                # Relative path (uses cwd)
cat /tmp/agent-123/file.txt          # Read own workspace
echo "data" > output.txt             # Write to relative path
```

---

## Security Configuration

### Agent-Level Settings

Configure security for each agent in their configuration:

```python
{
    "id": "agent-123",
    "enable_file_access_control": True,  # Enable file access validation (default: true)
    "allowed_directories": [],           # Additional allowed directories (optional)
    "allow_all_skills": False,           # Allow all skills vs specific skills
    "skill_ids": ["skill-1", "skill-2"], # Specific skills when allow_all_skills=false
    "sandbox": {
        "enabled": True,                 # Enable Claude SDK sandbox (default: true)
        "auto_allow_bash_if_sandboxed": True,
        "excluded_commands": [],         # Commands to bypass sandbox
        "allow_unsandboxed_commands": False
    }
}
```

### Platform-Level Settings

Configure default security settings in `backend/config.py`:

```python
class Settings(BaseSettings):
    # Agent workspace directory (main skill storage)
    agent_workspace_dir: str = str(_PROJECT_ROOT / "workspace")

    # Isolated per-agent workspaces (outside project tree)
    agent_workspaces_dir: str = str(get_app_data_dir() / "workspaces")

    # Built-in Sandbox Configuration
    sandbox_enabled_default: bool = True
    sandbox_auto_allow_bash: bool = True
    sandbox_excluded_commands: str = ""
    sandbox_allow_unsandboxed: bool = False
```

---

## Testing and Validation

### File Access Control Test

Test the file access permission handler:

```python
# Test cases
test_cases = [
    (f"{agent_workspace}/test.txt", True, "File in agent workspace"),
    ("/tmp/other-file.txt", False, "File outside workspace"),
    ("/etc/passwd", False, "System file"),
    (f"{agent_workspace}/../other-agent/file.txt", False, "Path traversal"),
]

for file_path, expected, description in test_cases:
    result = check_file_access(file_path, [agent_workspace])
    assert result == expected, f"Failed: {description}"
```

### Bash Protection Test

Test bash command validation:

```python
test_cases = [
    ("cat /etc/passwd", False, "Read system file"),
    ("cat relative/path.txt", True, "Read relative path"),
    ("echo 'data' > /tmp/bad.txt", False, "Write outside workspace"),
    (f"cat {agent_workspace}/file.txt", True, "Read own workspace"),
]

for command, expected_allow, description in test_cases:
    allowed, message = check_bash_command(command, [agent_workspace])
    assert allowed == expected_allow, f"Failed: {description}"
```

### Monitoring Logs

Monitor security events in `logs/backend.log`:

```bash
# File access denials
tail -f logs/backend.log | grep "FILE ACCESS DENIED"

# Bash command denials
tail -f logs/backend.log | grep "BASH FILE ACCESS DENIED"

# Skill access denials
tail -f logs/backend.log | grep "BLOCKED.*Skill"
```

---

## Known Limitations

### 1. Regex-Based Bash Parsing

**Issue**: Current bash command parser uses regex, which can't handle all edge cases.

**Examples of potential bypasses**:
```bash
# Base64 encoding
bash -c "$(echo Y2F0IC9ldGMvcGFzc3dk | base64 -d)"

# Eval execution
eval "cat /etc/passwd"

# Alternative languages
python -c "open('/etc/passwd').read()"
perl -e "print `cat /etc/passwd`"

# Subshells and complex quoting
$(cat /etc/passwd)
```

**Mitigation**: Consider implementing:
- Command whitelist mode (only allow specific commands)
- AST-based bash parsing
- Container-based isolation (Docker/Podman)

### 2. Symlink Following

**Issue**: If an agent creates a symlink within their workspace pointing outside, file tools might follow it.

**Mitigation**:
- Add symlink detection to permission handler
- Use OS-level restrictions (AppArmor, SELinux)

### 3. Network-Based Exfiltration

**Issue**: Agents with network access could exfiltrate data via HTTP requests.

**Current Protection**: Claude SDK sandbox provides network restrictions.

**Enhanced Protection**:
- Implement network egress filtering
- Monitor outbound connections
- Use air-gapped environments for sensitive workloads

---

## Best Practices

### For Platform Administrators

1. **Keep Workspaces Isolated**: Use the platform-specific app data directory (default) or a custom isolated location
2. **Enable All Layers**: Don't disable file access control unless absolutely necessary
3. **Monitor Logs**: Regularly review security logs for suspicious activity
4. **Limit Sandbox Exclusions**: Minimize `excluded_commands` to essential tools only
5. **Use Least Privilege**: Only give agents the skills they actually need

### For Agent Configuration

1. **Default to `allow_all_skills=False`**: Only enable specific skills needed
2. **Avoid Additional Directories**: Don't add extra `allowed_directories` unless required
3. **Enable Sandbox**: Keep `sandbox.enabled=True` for bash isolation
4. **Review Tool Lists**: Only enable necessary tools (`allowed_tools`)

### For Skill Developers

1. **Use Relative Paths**: Skills should use relative paths when possible
2. **Validate Inputs**: Don't trust user-provided paths in skill code
3. **Document Requirements**: Clearly state if skill needs special access
4. **Test in Isolation**: Test skills in restricted environment before deployment

---

## Security Incident Response

### If an Agent Attempts Unauthorized Access

1. **Check Logs**: Review `logs/backend.log` for the denial message
2. **Verify Configuration**: Ensure agent's `allowed_directories` is correct
3. **Review Code**: Check if agent's code is attempting invalid operations
4. **Update Skills**: If skill needs access, update agent configuration properly

### If a Security Bypass is Discovered

1. **Immediate Action**: Disable affected agents
2. **Report Issue**: Document the bypass mechanism
3. **Implement Fix**: Add detection/prevention for the bypass
4. **Test Thoroughly**: Validate fix doesn't break legitimate functionality
5. **Update Documentation**: Add bypass to Known Limitations if not fully mitigated

---

## Future Security Enhancements

### Short Term

- [ ] Add symlink detection to file access handler
- [ ] Implement command whitelist mode for bash
- [ ] Add rate limiting for file operations
- [ ] Enhance logging with security event categories

### Medium Term

- [ ] AST-based bash command parsing
- [ ] Network egress filtering and monitoring
- [ ] Filesystem quota enforcement per agent
- [ ] Audit trail with tamper-evident logging

### Long Term

- [ ] Container-based agent isolation (Docker/Podman)
- [ ] Kernel-level isolation (seccomp, AppArmor, SELinux)
- [ ] Hardware-based isolation (VM per agent)
- [ ] Formal verification of security properties

---

## References

### Code Locations

- **Workspace Manager**: `backend/core/workspace_manager.py`
- **Agent Manager**: `backend/core/agent_manager.py`
- **Security Configuration**: `backend/config.py`
- **Skill Access Control**: `agent_manager.py:163-207`
- **File Access Control**: `agent_manager.py:99-207`
- **Bash Protection**: `agent_manager.py:159-202`

### Related Documentation

- [Architecture Overview](./ARCHITECTURE.md)
- [Claude Agent SDK Documentation](https://github.com/anthropics/claude-agent-sdk)
- [Skills Guide](./SKILLS_GUIDE.md)

---

## Conclusion

The Agent Skill Platform implements a comprehensive, multi-layered security model that provides strong isolation between agents while maintaining flexibility and usability. The defense-in-depth approach ensures that even if one layer is bypassed, other layers provide continued protection.

For questions or security concerns, please review the code in `backend/core/agent_manager.py` and `backend/core/workspace_manager.py` or consult the development team.
