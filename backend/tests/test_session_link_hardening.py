"""Hardening tests for session_link_gate and the shared package-manager shell modes.

What is tested: the adversarial-review findings against create_session_link_gate
(security_hooks.py) and its package-manager shell-mode parser _js_pm_shell_c_command.
Every repro command from the review is in a matrix here.

Methodology: commands are written with ``@@`` for SWARM_APP_SESSION_ID and ``\\n``
for a newline, then driven through the real async gate (deny / approve). A timing test feeds 200 KB adversarial
commands and asserts each classifies in < 0.5 s.

Invariants:
  • package-manager shell modes are parsed: pnpm options before
    the subcommand (`pnpm -c exec`, `-rc`, `--shell-mode dlx`), pnpx / pnx -c,
    npx -c, npm exec -c / --call;
  • a variable NAME built at run time is denied as the operand of unset /
    export -n / env -u / --unset and as a nameref target; `bash -c '…"$1"…' _ VAR`
    is read with its positional arguments substituted;
  • `function NAME { … }` bodies, ksh / csh / tcsh / dash / fish -c strings, csh
    unsetenv / setenv, fish set, and `su [user] -c STRING` are classified;
  • sudo / env -i / exec -c deny only when the wrapped PROGRAM is a link reader
    (python* / uv / pipeline_v1, or a path to that script), so
    `sudo dnf install python3.11` and `env -i make run` pass; the sudo deny text
    advises -E / --preserve-env, not env -u;
  • a stdin shell's script (heredoc body, echo words) is classified, not matched;
  • the classifier is linear: past its word / character / depth bounds it fails
    closed only when the unscanned text names the variable or a tamper form.
"""
import asyncio
import time

import pytest

from core import security_hooks as sh

V = "SWARM_APP_SESSION_ID"


@pytest.fixture(autouse=True)
def _no_force(monkeypatch):
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)


def _v(command):
    return command.replace("@@", V).replace("\\n", "\n")


def _call(command):
    gate = sh.create_session_link_gate({"sdk_session_id": "app-1"})
    return asyncio.run(gate(
        {"tool_name": "Bash", "tool_input": {"command": _v(command)}, "cwd": "/tmp"},
        None, None))


def _is_deny(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ── item 1: package-manager shell modes ──────────────────────────────────────
PM_SHELL_DENIED = [
    "pnpm -c exec 'unset @@; python-backend pipeline-v1 create'",
    "pnpm -rc exec 'unset @@; python a.py'",
    "pnpm --shell-mode dlx 'unset @@; python a.py'",
    "pnpm dlx --shell-mode 'unset @@'",
    "pnpm exec --shell-mode 'unset @@'",
    "pnpm --dir x exec -c 'unset @@'",
    "pnpm dlx -c -- 'unset @@'",
    "pnpm dlx -p x -c 'unset @@'",
    "pnpx -c 'unset @@; python a.py'",
    "pnx -c 'unset @@; python a.py'",
    "npx -c 'unset @@; python a.py'",
    "npm exec -c 'unset @@; python a.py'",
    "npm exec --call 'unset @@; python a.py'",
    "npm exec --call='unset @@'",
    "env -i pnpm dlx -c 'python a.py'",
]
PM_SHELL_ALLOWED = [
    "pnpm dlx -c 'echo $@@'",
    "yarn dlx -c 'echo hi'",
    "pnpm exec -c 'node a.js'",
    "pnpm -c exec 'ls'",
    "npx -c 'echo hi'",
    "pnpm -Fclient exec unset @@",  # glued -F value: no shell mode, `unset` is a binary
]
# ── item 2: names built at run time ──────────────────────────────────────────
INDIRECT_DENIED = [
    "X=@@; unset $X", "X=@@; unset ${X}", "X=@@; unset \"$X\"",
    "X=@@; export -n \"$X\"", "X=@@; eval \"unset $X\"", "X=@@; eval unset \\$X",
    "X=@@; unset $X; python-backend pipeline-v1 create",
    "unset ${!SWARM_APP_*}", "unset ${!SWARM_APP_*}; python-backend pipeline-v1 create",
    "for v in ${!SWARM_*}; do unset $v; done",
    "unset $(compgen -e | grep SWARM_APP)",
    "unset $'\\x53WARM_APP_SESSION_ID'",
    "unset SWARM_APP_SESSION_I?", "unset SWARM_APP_SESSION_I[D]", "unset SWARM_APP_*",
    "unset SWARM_APP_SESSION_{ID,X}",
    "unset @@[0]",
    "env -u \"$(echo @@)\" python a.py", "env -u `echo @@` python a.py",
    "env -u $(env | grep -o '^SWARM_APP[^=]*') python a.py",
    "env --unset=$X python a.py",
    "echo @@ | xargs -I{} env -u {} python-backend pipeline-v1 create",
    "echo @@ | xargs env -u",
    "unset `echo @@`",
    "declare -n R=@@; R=forged; python-backend pipeline-v1 create",
    "X=@@; declare -n R=$X; R=forged; python a.py",
    "declare -n R=@@; unset R; python a.py",
    "typeset -n R=@@; R=x", "local -n R=@@", "nameref R=@@",
    "bash -c 'unset \"$1\"; python a.py' _ @@",
    "bash -c 'unset $0; python a.py' @@",
    "sh -c 'eval \"$1=x\"' _ @@",
    "bash -c 'export -n \"${1}\"' _ @@",
]
INDIRECT_ALLOWED = [
    "unset arr[$i]", "unset FOO BAR", "export $(grep -v '^#' .env | xargs)",
    "export PATH=$PATH:/x", "declare -n R=OTHER", "bash -c 'echo \"$1\"' _ @@",
    "env -u PYTHONHOME python a.py", "unset 'arr[0]'",
]

# ── items 3, 5, 6: function bodies, other shells, su ─────────────────────────
SHELLS_DENIED = [
    "function f { unset @@; }; f; python a.py",
    "function f { unset @@; }; f; python-backend pipeline-v1 create",
    "function f() { unset @@; }",
    "ksh -c 'unset @@; python a.py'", "mksh -c 'unset @@'", "dash -c 'unset @@'",
    "csh -c 'unsetenv @@; python a.py'", "tcsh -c 'unsetenv @@'",
    "csh -c 'setenv @@ forged'", "zsh -c 'unsetenv @@'",
    "fish -c 'set -e @@'", "fish -c 'set -gx @@ forged'", "fish --command 'set -e @@'",
    "su - root -c 'unset @@; python a.py'",
    "su root -c \"env -u @@ python a.py\"",
    "su -c 'unset @@' root",
    "su --command='@@=x python a.py' root",
    "su - -c 'python-backend pipeline-v1 create' root",
    "su -l root -c 'python a.py'",
]
SHELLS_ALLOWED = [
    "su -c 'python-backend pipeline-v1 create' root",
    "su - root -c 'ls -la'",
    "function build { make all; }; build",
    "fish -c 'set -x PATH /usr/bin $PATH'",
    "csh -c 'setenv FOO 1'",
]

# ── items 4, 7: environment resets judged by the PROGRAM word ────────────────
RESET_DENIED = [
    "sudo python-backend pipeline-v1 create",
    "sudo -i python-backend pipeline-v1 create",
    "sudo python3 -m pip install foo",
    "sudo -iu root python3 a.py", "sudo -- python a.py", "sudo -g wheel python a.py",
    "env -i uv run scripts/a.py",
    "env -i ./bin/python-backend pipeline-v1 status",
    "env -i PATH=$PATH python-backend pipeline-v1 create",
    "env -i \"$(which python3)\" pipeline-v1 create",
    "P=python3; env -i $P pipeline-v1 create",
    "env -i bash -l -c 'py pipeline-v1'",
    "env -i PATH=/usr/bin /bin/sh -c 'cd backend && python-backend pipeline-v1'",
    "exec -a foo -c python a.py", "arch -arm64 /usr/bin/env -i python3 a.py",
]
RESET_ALLOWED = [
    "sudo dnf install -y python3.11", "sudo yum install python3-pip",
    "sudo apt-get install python3-venv", "sudo port install python311",
    "sudo ln -sf /opt/homebrew/bin/python3 /usr/local/bin/python",
    "sudo chown -R $USER ~/.swarm-ai/python", "sudo systemctl restart swarmai-python",
    "sudo launchctl list | grep python", "sudo pip3 install x", "sudo -n true",
    "env -i make run", "env -i make run-create", "env -i PATH=/usr/bin git log --oneline",
    "env -i node a.js", "exec -c ls",
]

# ── item 8: a stdin shell's script is classified, not name-matched ───────────
STDIN_DENIED = [
    "bash <<EOF\\nunset @@\\npython a.py\\nEOF",
    "bash <<-'EOF'\\n\tenv -u @@ python a.py\\n\tEOF",
    "bash <<< 'unset @@'",
    "echo 'unset @@' | sh",
    "printf 'unset @@\\n' | bash",
    "cat s.sh | bash  # @@",
    "cat <<EOF | bash\\nunset @@\\nEOF",
    "printf 'unset %s\\n' @@ | sh",
]
STDIN_ALLOWED = [
    "bash <<'EOF'\\necho $@@\\nEOF",
    "echo \"@@\" | bash",
    "bash <<< 'echo $@@'",
    "cat <<EOF > /tmp/x.md\\nunset @@ is blocked\\nEOF",
    "python - <<'EOF'\\nprint(\"@@\")\\nEOF",
]

# ── review false-positive list: still allowed ────────────────────────────────
REVIEW_ALLOWED = [
    "env | sort", "env | grep -i path", "docker run -e @@=x python:3.11 python -c 1",
    "docker run --env @@ img", "conda run -n base python a.py",
    "git commit -m \"fix: unset @@ handling\"", "git commit -m \"env -u @@ is denied\"",
    "grep -rn \"unset @@\" backend/", "rg 'env -u @@' backend", "echo \"export @@=foo\"",
    "cat <<EOF\\nexport @@=x\\nEOF", "python -c \"print('@@=1')\"",
    "cat notes.md | sh -c 'grep @@'", "curl -s localhost:8000/x | python3 -m json.tool",
    "ps aux | grep -v grep | grep exec", "git log --grep=exec",
    "python-backend pipeline-v1 create --project SwarmAI",
    "cd backend && python-backend pipeline-v1 create",
    "exec python a.py", "exec 3>&1", "for f in *.py; do python $f; done",
    "env PYTHONPATH=. python a.py", "git diff | grep '@@='", "sed -n '/@@=/p' file",
    "awk '/unset @@/' f", "gh pr create --body \"unset @@\"",
]

# ── review bypass list (beyond the groups above): all denied ─────────────────
REVIEW_DENIED = [
    "exec env -u @@ python a.py", "command env -u @@ python a.py", "builtin unset @@",
    "\\unset @@", "un''set @@", "'@@'=x python a.py", "@@''=x python a.py",
    "unset -f @@", "export -fn @@", "declare +x @@; python a.py", "declare -gx @@=x",
    "env -C/tmp -u @@ python a.py", "env --argv0=x -u @@ python a.py",
    "sudo -E env -u @@ python a.py", "sudo -E @@=x python a.py",
    "nohup env -u @@ python a.py &", "xargs -a f env -u @@",
    "find . -exec sh -c 'unset @@; python a.py' \\;", "npx env -u @@ python a.py",
    "time env -u @@ python a.py", "coproc env -u @@ python a.py",
    "case x in x) unset @@;; esac", "f(){ unset @@;}; f", "env -S\"-u @@\" python a.py",
    "env -iS 'python a.py'", "exec -lc python a.py", "watch -n1 env -u @@ python a.py",
    "busybox sh -c 'unset @@'", "bash -o pipefail -o errexit -c 'unset @@'",
    "read -a @@ <<< x", "printf -v@@ x", "unset -nv @@", "exec -- env -i python a.py",
]


DENIED = (PM_SHELL_DENIED + INDIRECT_DENIED + SHELLS_DENIED + RESET_DENIED
          + STDIN_DENIED + REVIEW_DENIED)
ALLOWED = (PM_SHELL_ALLOWED + INDIRECT_ALLOWED + SHELLS_ALLOWED + RESET_ALLOWED
           + STDIN_ALLOWED + REVIEW_ALLOWED)


class TestGateMatrix:
    @pytest.mark.parametrize("command", DENIED)
    def test_denied(self, command):
        assert _is_deny(_call(command)), command

    @pytest.mark.parametrize("command", ALLOWED)
    def test_allowed(self, command):
        assert _call(command) == {"decision": "approve"}, command

    def test_sudo_deny_text_advises_preserving_the_environment(self):
        reason = _call("sudo python3 a.py")["hookSpecificOutput"]["permissionDecisionReason"]
        assert "sudo -E" in reason and "--preserve-env=" + V in reason
        assert "env -u NAME" not in reason

    def test_env_reset_deny_text_still_advises_env_u(self):
        reason = _call("env -i python a.py")["hookSpecificOutput"]["permissionDecisionReason"]
        assert "env -u NAME" in reason

    def test_indirect_deny_text_asks_for_literal_names(self):
        reason = _call("unset $X")["hookSpecificOutput"]["permissionDecisionReason"]
        assert "built at run time" in reason and "literally" in reason


class TestShellModeExtraction:
    def test_shell_mode_extraction(self):
        f = sh._js_pm_shell_c_command
        assert f(["pnpm", "-rc", "exec", "a", "b"]) == "a b"
        assert f(["npx", "-p", "x", "--call=y z"]) == "y z"
        assert f(["npm", "--prefix", "d", "x", "-c", "s"]) == "s"
        assert f(["npx", "vitest", "-c", "s"]) is None  # -c after the package is its arg
        assert f(["pnpm", "install", "-c"]) is None
        assert f(["pnpm", "-C", "dir", "exec", "ls"]) is None


N = 200_000
ADVERSARIAL = {
    "sudo chain": ("sudo " * (N // 5)) + "ls",
    "env -i chain": ("env -i " * (N // 7)) + "ls",
    "env chain": ("env " * (N // 4)) + "ls",
    "bash chain": ("bash " * (N // 5)) + "ls",
    "pnpm chain": ("pnpm " * (N // 5)) + "ls",
    "exec chain": ("exec " * (N // 5)) + "ls",
    "eval chain": ("eval " * (N // 5)) + "ls",
    "assignments": ("A=1 " * (N // 4)) + "env ls",
    "segments": "env ls; " * (N // 8),
    "long word": 'env "' + "a" * N + '"',
    "brackets": "env A[" + "[" * N + "=1",
    "positional blowup": "bash -c '" + "$@" * 2000 + "' _ " + (V + " ") * 2000,
    "stdin shells": "env -i bash; " * (N // 13),
    "heredoc doc": "cat > /tmp/d.md <<'EOF'\n" + "sudo env -i python x\n" * 9000 + "EOF",
}
BOUND_WINDOW = [
    ("sudo " * 100) + "env -u " + V + " python a.py",      # tamper past the word window
    "unset " + " ".join("A%d" % k for k in range(100)) + " $X",
    "echo " + "x " * 20000 + V,                             # code over the char bound
]
BOUND_PASS = [
    "python a.py " + " ".join("--opt%d" % k for k in range(200)),
    "cat > /tmp/d.md <<'EOF'\n" + "sudo env -i python x\n" * 9000 + "EOF",
    "echo " + "x " * 20000,
]


class TestLinearBounds:
    @pytest.mark.parametrize("name", sorted(ADVERSARIAL))
    def test_200kb_adversarial_command_classifies_fast(self, name):
        command = ADVERSARIAL[name]
        start = time.perf_counter()
        sh._command_session_link(command)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, (name, len(command), elapsed)

    @pytest.mark.parametrize("command", BOUND_WINDOW)
    def test_unscanned_tamper_fails_closed(self, command):
        assert _is_deny(_call(command))

    @pytest.mark.parametrize("command", BOUND_PASS)
    def test_unscanned_harmless_text_passes(self, command):
        assert _call(command) == {"decision": "approve"}
