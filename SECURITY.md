# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of SwarmAI seriously. If you discover a security
vulnerability, please report it responsibly.

### How to Report

1. **Do NOT open a public GitHub issue** for security vulnerabilities
2. Email your findings to the maintainers via GitHub private vulnerability
   reporting: go to the [Security tab](https://github.com/xg-gh-25/SwarmAI/security/advisories/new)
   on our repository and click "Report a vulnerability"
3. Include as much detail as possible:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt within 48 hours
- **Assessment**: We will assess the vulnerability and determine its severity
  within 7 days
- **Fix**: Critical vulnerabilities will be patched as soon as possible
- **Disclosure**: We will coordinate disclosure timing with you

### Scope

The following are in scope for security reports:

- Authentication and authorization bypasses
- Remote code execution
- Data leakage (API keys, credentials, personal data)
- Cross-site scripting (XSS) in the desktop app
- Privilege escalation
- Sandbox escapes in bash execution

### Out of Scope

- Issues in third-party dependencies (report to the upstream project)
- Social engineering attacks
- Denial of service attacks against local-only services
- Issues requiring physical access to the user's machine

## Security Architecture

SwarmAI implements defense-in-depth security:

- **No cloud storage**: All data stays on your local machine (`~/.swarm-ai/`)
- **Credential delegation**: AWS credential chain only — the app never stores
  API keys in its database
- **4-layer PreToolUse defense**: tool logger → command blocker → human approval
  → skill access control
- **Bash sandboxing**: Dangerous command patterns blocked (13 regex patterns)
- **Human-in-the-loop**: Destructive operations require explicit user approval
- **Workspace isolation**: Per-agent directory boundaries
- **Error sanitization**: Stack traces stripped in production mode
