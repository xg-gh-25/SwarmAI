"""Command predicates judge each simple command on its own (2026-10-22).

The rm and store predicates used to shlex the WHOLE line as one command, so the
words of later commands became operands: a scratch cleanup chained with mkdir
was a dangerous rm, and a store read was flagged by what followed it. The other
way round, a store destroy after ``cd x &&`` was missed (tokens[0] was cd).
They now share ``_gate_shell_segments``. A copy into a store is a destroy only
when it overwrites an existing file.
"""

from core.security_hooks import _is_dangerous_rm, _targets_irreplaceable_store

STORE = "~/.swarm-ai/SwarmWS"


def test_scratch_cleanup_chained_with_other_commands_is_not_dangerous():
    for cmd in (
        "rm -rf /tmp/revcheck && mkdir -p /tmp/revcheck/new && cd /tmp/revcheck",
        "rm -rf /tmp/v1audit.Jy0d && echo removed",
        "cd /tmp && rm -rf /tmp/build; ls",
    ):
        assert _is_dangerous_rm(cmd) is False, cmd


def test_dangerous_rm_anywhere_in_the_chain_is_still_caught():
    for cmd in (
        "echo hi && rm -rf ~/Documents",
        "ls; rm -rf build",
        "cd /tmp && sudo rm -rf /etc/x",
        "(rm -rf ~)",
        'rm -rf "$HOME/x"',
    ):
        assert _is_dangerous_rm(cmd) is True, cmd


def test_store_destroy_after_another_command_is_caught():
    for cmd in (
        f"cd {STORE} && rm .context/MEMORY.md",
        f"echo x; mv {STORE}/Projects/CMHK /tmp/cmhk",
        f"ls && find {STORE}/Projects -name '*.md' -delete",
    ):
        assert _targets_irreplaceable_store(cmd) is True, cmd


def test_store_reads_and_new_files_are_not_destroys(tmp_path):
    lib = tmp_path / "Knowledge" / "Library"
    lib.mkdir(parents=True)
    (lib / "existing.html").write_text("x")
    for cmd in (
        f"cp {STORE}/Projects/SwarmAI/2-understanding/TECH.md /tmp/replay/ && ls /tmp/replay",
        f"rm -f ~/.swarm-ai/cmsg.txt; git status --porcelain backend/jobs/",
        f"cp /tmp/aidlc.html {lib}/new-file.html && ls -la {lib}",
        f"cp /tmp/existing.html /tmp/elsewhere.html; grep -r foo {STORE}/Projects",
    ):
        assert _targets_irreplaceable_store(cmd) is False, cmd


def test_copy_that_overwrites_a_store_file_is_a_destroy(tmp_path):
    lib = tmp_path / "Knowledge" / "Library"
    lib.mkdir(parents=True)
    (lib / "existing.html").write_text("x")
    for cmd in (
        f"cp /dev/null {lib}/existing.html",
        f"cp /tmp/existing.html {lib}",
        f"cp /tmp/a.html {lib}/$NAME",
    ):
        assert _targets_irreplaceable_store(cmd) is True, cmd
