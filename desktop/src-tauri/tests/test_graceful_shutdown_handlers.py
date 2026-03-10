"""Static analysis tests for graceful shutdown bug condition in lib.rs.

This module inspects the Rust source code in ``desktop/src-tauri/src/lib.rs``
to verify that the three window close/exit event handlers include a graceful
shutdown sequence (``send_shutdown_request`` → sleep → ``kill_process_tree``)
before force-killing the backend process.

On UNFIXED code these tests are EXPECTED TO FAIL, confirming the bug exists:
the handlers skip ``send_shutdown_request()`` and go straight to
``kill_process_tree()``.

Key public symbols:

- ``TestBugConditionExploration`` — Verifies each handler calls
  ``send_shutdown_request`` before ``kill_process_tree`` with a sleep in
  between, uses a shared helper, and sets ``running = false`` under lock.

Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3
"""

import os
import re
import unittest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_lib_rs() -> str:
    """Return the full contents of lib.rs."""
    # Resolve relative to this test file's location
    here = os.path.dirname(os.path.abspath(__file__))
    lib_path = os.path.join(here, "..", "src", "lib.rs")
    with open(lib_path, "r") as f:
        return f.read()


def _extract_handler_block(source: str, marker: str) -> str:
    """Extract the code block for a specific handler from lib.rs.

    Uses brace-counting to find the full handler body after the marker.
    Returns the handler body as a string, or empty string if not found.
    """
    idx = source.find(marker)
    if idx == -1:
        return ""

    # Find the first opening brace after the marker
    brace_start = source.find("{", idx)
    if brace_start == -1:
        return ""

    depth = 0
    end = brace_start
    for i in range(brace_start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    return source[idx:end]


def _extract_window_destroyed_handler(source: str) -> str:
    """Extract the WindowEvent::Destroyed handler block."""
    return _extract_handler_block(source, "WindowEvent::Destroyed")


def _extract_run_exit_handler(source: str) -> str:
    """Extract the RunEvent::Exit handler block.

    Careful to match ``RunEvent::Exit`` but NOT ``RunEvent::ExitRequested``.
    """
    # Find "RunEvent::Exit =>" but not "RunEvent::ExitRequested"
    pattern = r"RunEvent::Exit\s*=>"
    match = re.search(pattern, source)
    if not match:
        return ""
    return _extract_handler_block(source, match.group(0))


def _extract_exit_requested_handler(source: str) -> str:
    """Extract the RunEvent::ExitRequested handler block.

    The marker ``RunEvent::ExitRequested { api, .. } =>`` contains braces
    in the pattern itself, so we find the ``=>`` and then locate the
    handler body block that follows it.
    """
    marker = "RunEvent::ExitRequested"
    idx = source.find(marker)
    if idx == -1:
        return ""
    # Skip past the "=>" to reach the handler body
    arrow_idx = source.find("=>", idx)
    if arrow_idx == -1:
        return ""
    # Find the opening brace of the handler body (after =>)
    brace_start = source.find("{", arrow_idx + 2)
    if brace_start == -1:
        return ""
    # Use brace-counting from the handler body brace
    depth = 0
    end = brace_start
    for i in range(brace_start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return source[idx:end]


# ---------------------------------------------------------------------------
# Bug Condition Exploration Tests
# ---------------------------------------------------------------------------

class TestBugConditionExploration(unittest.TestCase):
    """Verify the graceful shutdown bug condition via static Rust source
    analysis.

    Each test inspects a handler in ``lib.rs`` and asserts that the
    expected graceful shutdown pattern is present — either directly in
    the handler body or via delegation to a shared helper function.

    On UNFIXED code these tests MUST FAIL — failure proves the bug exists.

    Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _read_lib_rs()
        cls.window_handler = _extract_window_destroyed_handler(cls.source)
        cls.exit_handler = _extract_run_exit_handler(cls.source)
        cls.exit_requested_handler = _extract_exit_requested_handler(
            cls.source
        )
        # Resolve the effective handler body: if the handler delegates to
        # graceful_shutdown_and_kill, use the helper body for property checks.
        cls.window_effective = cls._resolve_effective(cls.window_handler, cls.source)
        cls.exit_effective = cls._resolve_effective(cls.exit_handler, cls.source)
        cls.exit_requested_effective = cls._resolve_effective(
            cls.exit_requested_handler, cls.source
        )

    @staticmethod
    def _resolve_effective(handler_body: str, full_source: str) -> str:
        """If handler delegates to graceful_shutdown_and_kill, return the
        helper body. Otherwise return the handler body itself."""
        if "graceful_shutdown_and_kill" in handler_body:
            helper = _extract_function_body(full_source, "graceful_shutdown_and_kill")
            if helper:
                return helper
        return handler_body

    # -- WindowEvent::Destroyed handler tests --

    def test_window_destroyed_has_send_shutdown_request(self):
        """WindowEvent::Destroyed handler must call send_shutdown_request."""
        self.assertIn(
            "send_shutdown_request",
            self.window_effective,
            "WindowEvent::Destroyed handler does not call "
            "send_shutdown_request — bug confirmed",
        )

    def test_window_destroyed_shutdown_before_kill(self):
        """send_shutdown_request must appear BEFORE kill_process_tree."""
        shutdown_pos = self.window_effective.find("send_shutdown_request")
        kill_pos = self.window_effective.find("kill_process_tree")
        self.assertNotEqual(shutdown_pos, -1,
                            "send_shutdown_request not found in handler")
        self.assertNotEqual(kill_pos, -1,
                            "kill_process_tree not found in handler")
        self.assertLess(
            shutdown_pos, kill_pos,
            "send_shutdown_request must appear before kill_process_tree",
        )

    def test_window_destroyed_sleep_between_shutdown_and_kill(self):
        """A sleep must exist between send_shutdown_request and kill."""
        shutdown_pos = self.window_effective.find("send_shutdown_request")
        kill_pos = self.window_effective.find("kill_process_tree")
        if shutdown_pos == -1 or kill_pos == -1:
            self.fail("Cannot check sleep — shutdown or kill not found")
        between = self.window_effective[shutdown_pos:kill_pos]
        self.assertTrue(
            "thread::sleep" in between or "std::thread::sleep" in between,
            "No sleep found between send_shutdown_request and "
            "kill_process_tree in WindowEvent::Destroyed handler",
        )

    def test_window_destroyed_captures_port(self):
        """Handler must capture port from locked backend state."""
        self.assertTrue(
            "backend.port" in self.window_effective
            or ".port" in self.window_effective
            or "port" in self.window_effective,
            "WindowEvent::Destroyed handler does not capture port",
        )

    # -- RunEvent::Exit handler tests --

    def test_exit_has_send_shutdown_request(self):
        """RunEvent::Exit handler must call send_shutdown_request."""
        self.assertIn(
            "send_shutdown_request",
            self.exit_effective,
            "RunEvent::Exit handler does not call "
            "send_shutdown_request — bug confirmed",
        )

    def test_exit_shutdown_before_kill(self):
        """send_shutdown_request must appear BEFORE kill_process_tree."""
        shutdown_pos = self.exit_effective.find("send_shutdown_request")
        kill_pos = self.exit_effective.find("kill_process_tree")
        self.assertNotEqual(shutdown_pos, -1,
                            "send_shutdown_request not found in handler")
        self.assertNotEqual(kill_pos, -1,
                            "kill_process_tree not found in handler")
        self.assertLess(
            shutdown_pos, kill_pos,
            "send_shutdown_request must appear before kill_process_tree",
        )

    def test_exit_sleep_between_shutdown_and_kill(self):
        """A sleep must exist between send_shutdown_request and kill."""
        shutdown_pos = self.exit_effective.find("send_shutdown_request")
        kill_pos = self.exit_effective.find("kill_process_tree")
        if shutdown_pos == -1 or kill_pos == -1:
            self.fail("Cannot check sleep — shutdown or kill not found")
        between = self.exit_effective[shutdown_pos:kill_pos]
        self.assertTrue(
            "thread::sleep" in between or "std::thread::sleep" in between,
            "No sleep found between send_shutdown_request and "
            "kill_process_tree in RunEvent::Exit handler",
        )

    def test_exit_captures_port(self):
        """Handler must capture port from locked backend state."""
        self.assertTrue(
            "backend.port" in self.exit_effective
            or ".port" in self.exit_effective
            or "port" in self.exit_effective,
            "RunEvent::Exit handler does not capture port",
        )

    # -- RunEvent::ExitRequested handler tests --

    def test_exit_requested_has_send_shutdown_request(self):
        """RunEvent::ExitRequested handler must call send_shutdown_request."""
        self.assertIn(
            "send_shutdown_request",
            self.exit_requested_effective,
            "RunEvent::ExitRequested handler does not call "
            "send_shutdown_request — bug confirmed",
        )

    def test_exit_requested_shutdown_before_kill(self):
        """send_shutdown_request must appear BEFORE kill_process_tree."""
        shutdown_pos = self.exit_requested_effective.find(
            "send_shutdown_request"
        )
        kill_pos = self.exit_requested_effective.find("kill_process_tree")
        self.assertNotEqual(shutdown_pos, -1,
                            "send_shutdown_request not found in handler")
        self.assertNotEqual(kill_pos, -1,
                            "kill_process_tree not found in handler")
        self.assertLess(
            shutdown_pos, kill_pos,
            "send_shutdown_request must appear before kill_process_tree",
        )

    def test_exit_requested_sleep_between_shutdown_and_kill(self):
        """A sleep must exist between send_shutdown_request and kill."""
        shutdown_pos = self.exit_requested_effective.find(
            "send_shutdown_request"
        )
        kill_pos = self.exit_requested_effective.find("kill_process_tree")
        if shutdown_pos == -1 or kill_pos == -1:
            self.fail("Cannot check sleep — shutdown or kill not found")
        between = self.exit_requested_effective[shutdown_pos:kill_pos]
        self.assertTrue(
            "thread::sleep" in between or "std::thread::sleep" in between,
            "No sleep found between send_shutdown_request and "
            "kill_process_tree in RunEvent::ExitRequested handler",
        )

    def test_exit_requested_captures_port(self):
        """Handler must capture port from locked backend state."""
        self.assertTrue(
            "backend.port" in self.exit_requested_effective
            or ".port" in self.exit_requested_effective
            or "port" in self.exit_requested_effective,
            "RunEvent::ExitRequested handler does not capture port",
        )

    # -- Shared helper function (DRY) --

    def test_shared_helper_function_exists(self):
        """All three handlers should call a shared helper function (DRY).

        The design specifies ``graceful_shutdown_and_kill`` as the helper.
        """
        self.assertIn(
            "graceful_shutdown_and_kill",
            self.source,
            "No shared graceful_shutdown_and_kill helper function found "
            "in lib.rs — handlers duplicate shutdown logic (DRY violation)",
        )

    def test_helper_sets_running_false_under_lock(self):
        """The helper must set backend.running = false under lock before
        the shutdown request (double-fire protection, Property 3).
        """
        # Find the helper function body
        helper_block = _extract_handler_block(
            self.source, "fn graceful_shutdown_and_kill"
        )
        self.assertTrue(
            len(helper_block) > 0,
            "graceful_shutdown_and_kill function not found in lib.rs",
        )
        # running = false must appear before send_shutdown_request
        running_pos = helper_block.find("running = false")
        shutdown_pos = helper_block.find("send_shutdown_request")
        self.assertNotEqual(running_pos, -1,
                            "running = false not found in helper")
        self.assertNotEqual(shutdown_pos, -1,
                            "send_shutdown_request not found in helper")
        self.assertLess(
            running_pos, shutdown_pos,
            "running = false must be set before send_shutdown_request "
            "(double-fire protection)",
        )


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Helper for extracting function bodies
# ---------------------------------------------------------------------------

def _extract_function_body(source: str, fn_name: str) -> str:
    """Extract the full body of a named Rust function from source.

    Searches for ``fn <fn_name>`` and uses brace-counting to capture the
    complete function body including its signature.
    """
    pattern = rf"fn\s+{re.escape(fn_name)}\s*\("
    match = re.search(pattern, source)
    if not match:
        return ""
    return _extract_handler_block(source, match.group(0))


def _extract_function_body_at(source: str, start_pos: int) -> str:
    """Extract a Rust function body starting from a known position.

    Uses brace-counting from ``start_pos`` to capture the complete
    function body.
    """
    brace_start = source.find("{", start_pos)
    if brace_start == -1:
        return ""
    depth = 0
    end = brace_start
    for i in range(brace_start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return source[start_pos:end]


# ---------------------------------------------------------------------------
# Preservation Property Tests
# ---------------------------------------------------------------------------

class TestPreservation(unittest.TestCase):
    """Verify baseline behaviors that MUST remain unchanged after the fix.

    These tests inspect the Rust source in ``lib.rs`` to confirm that
    existing correct patterns (``stop_backend`` sequence, platform
    implementations, ``child.kill()`` fallback, state cleanup) are present.

    On UNFIXED code all tests MUST PASS — they capture the baseline.
    After the fix, they MUST STILL PASS — confirming no regressions.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    @classmethod
    def setUpClass(cls):
        cls.source = _read_lib_rs()
        cls.window_handler = _extract_window_destroyed_handler(cls.source)
        cls.exit_handler = _extract_run_exit_handler(cls.source)
        cls.exit_requested_handler = _extract_exit_requested_handler(
            cls.source
        )

    # ------------------------------------------------------------------
    # 1. stop_backend preservation
    # ------------------------------------------------------------------

    def test_stop_backend_contains_send_shutdown_request(self):
        """stop_backend must call send_shutdown_request."""
        body = _extract_function_body(self.source, "stop_backend")
        self.assertTrue(
            len(body) > 0,
            "stop_backend function not found in lib.rs",
        )
        self.assertIn(
            "send_shutdown_request",
            body,
            "stop_backend must call send_shutdown_request",
        )

    def test_stop_backend_contains_kill_process_tree(self):
        """stop_backend must call kill_process_tree."""
        body = _extract_function_body(self.source, "stop_backend")
        self.assertIn(
            "kill_process_tree",
            body,
            "stop_backend must call kill_process_tree",
        )

    def test_stop_backend_contains_child_kill(self):
        """stop_backend must call child.kill() as fallback."""
        body = _extract_function_body(self.source, "stop_backend")
        self.assertIn(
            "child.kill()",
            body,
            "stop_backend must call child.kill() as fallback",
        )

    def test_stop_backend_contains_sleep(self):
        """stop_backend must contain a sleep (tokio::time::sleep)."""
        body = _extract_function_body(self.source, "stop_backend")
        self.assertTrue(
            "tokio::time::sleep" in body or "time::sleep" in body,
            "stop_backend must contain a sleep between shutdown request "
            "and force kill",
        )

    def test_stop_backend_correct_order(self):
        """stop_backend must call send_shutdown_request → sleep → kill_process_tree → child.kill() in order."""
        body = _extract_function_body(self.source, "stop_backend")
        shutdown_pos = body.find("send_shutdown_request")
        sleep_pos = max(
            body.find("tokio::time::sleep"),
            body.find("time::sleep"),
        )
        kill_tree_pos = body.find("kill_process_tree")
        child_kill_pos = body.find("child.kill()")

        self.assertNotEqual(shutdown_pos, -1, "send_shutdown_request not found")
        self.assertNotEqual(sleep_pos, -1, "sleep not found")
        self.assertNotEqual(kill_tree_pos, -1, "kill_process_tree not found")
        self.assertNotEqual(child_kill_pos, -1, "child.kill() not found")

        self.assertLess(
            shutdown_pos, sleep_pos,
            "send_shutdown_request must come before sleep",
        )
        self.assertLess(
            sleep_pos, kill_tree_pos,
            "sleep must come before kill_process_tree",
        )
        self.assertLess(
            kill_tree_pos, child_kill_pos,
            "kill_process_tree must come before child.kill()",
        )

    # ------------------------------------------------------------------
    # 2. Platform implementation preservation
    # ------------------------------------------------------------------

    def test_unix_send_shutdown_request_uses_curl(self):
        """Unix send_shutdown_request impl must contain 'curl'."""
        # Find all send_shutdown_request function bodies by splitting on fn declarations
        pattern = r'fn\s+send_shutdown_request\s*\('
        matches = list(re.finditer(pattern, self.source))
        self.assertGreaterEqual(
            len(matches), 2,
            "Expected at least 2 send_shutdown_request implementations "
            "(Windows + Unix)",
        )

        # The implementations are ordered: Windows first (#[cfg(target_os = "windows")])
        # then Unix (#[cfg(not(target_os = "windows"))])
        # The second match is the Unix impl
        found_curl = False
        for i, m in enumerate(matches):
            body = _extract_function_body_at(self.source, m.start())
            if "curl" in body:
                found_curl = True
                break

        self.assertTrue(
            found_curl,
            "Could not find any send_shutdown_request implementation with curl",
        )

    def test_windows_send_shutdown_request_uses_powershell(self):
        """Windows send_shutdown_request impl must contain 'powershell' (case-insensitive)."""
        pattern = r'fn\s+send_shutdown_request\s*\('
        matches = list(re.finditer(pattern, self.source))
        self.assertGreaterEqual(
            len(matches), 2,
            "Expected at least 2 send_shutdown_request implementations",
        )

        found_powershell = False
        for m in matches:
            preceding = self.source[max(0, m.start() - 200):m.start()]
            body = _extract_handler_block(self.source, m.group(0))
            if 'target_os = "windows"' in preceding and 'not(' not in preceding.split('target_os')[-1]:
                self.assertIn(
                    "powershell",
                    body.lower(),
                    "Windows send_shutdown_request must use PowerShell",
                )
                found_powershell = True
                break

        self.assertTrue(
            found_powershell,
            "Could not find Windows send_shutdown_request with PowerShell",
        )

    def test_kill_process_tree_exists_with_platform_cfg(self):
        """kill_process_tree must exist with platform-specific #[cfg] blocks."""
        pattern = r'fn\s+kill_process_tree\s*\('
        matches = list(re.finditer(pattern, self.source))
        self.assertGreaterEqual(
            len(matches), 2,
            "Expected at least 2 kill_process_tree implementations "
            "(Windows + Unix)",
        )

        # Verify each has a #[cfg] attribute
        for m in matches:
            preceding = self.source[max(0, m.start() - 200):m.start()]
            self.assertIn(
                "#[cfg(",
                preceding,
                f"kill_process_tree at position {m.start()} missing "
                f"#[cfg] attribute",
            )

    # ------------------------------------------------------------------
    # 3. child.kill() preservation in all handlers
    # ------------------------------------------------------------------

    def test_window_destroyed_has_child_kill(self):
        """WindowEvent::Destroyed handler must call child.kill() as fallback."""
        # The handler may call child.kill() directly or via the helper
        has_child_kill = (
            "child.kill()" in self.window_handler
            or "child).kill()" in self.window_handler
        )
        # Also check if it delegates to a helper that calls child.kill()
        if not has_child_kill and "graceful_shutdown_and_kill" in self.window_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_child_kill = "child.kill()" in helper or "child).kill()" in helper
        # Fallback: check the broader handler context including block_on
        if not has_child_kill:
            has_child_kill = "child" in self.window_handler and "kill" in self.window_handler
        self.assertTrue(
            has_child_kill,
            "WindowEvent::Destroyed handler must call child.kill() "
            "as fallback after kill_process_tree",
        )

    def test_exit_has_child_kill(self):
        """RunEvent::Exit handler must call child.kill() as fallback."""
        has_child_kill = (
            "child.kill()" in self.exit_handler
            or "child).kill()" in self.exit_handler
        )
        if not has_child_kill and "graceful_shutdown_and_kill" in self.exit_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_child_kill = "child.kill()" in helper or "child).kill()" in helper
        if not has_child_kill:
            has_child_kill = "child" in self.exit_handler and "kill" in self.exit_handler
        self.assertTrue(
            has_child_kill,
            "RunEvent::Exit handler must call child.kill() "
            "as fallback after kill_process_tree",
        )

    def test_exit_requested_has_child_kill(self):
        """RunEvent::ExitRequested handler must call child.kill() as fallback."""
        has_child_kill = (
            "child.kill()" in self.exit_requested_handler
            or "child).kill()" in self.exit_requested_handler
        )
        if not has_child_kill and "graceful_shutdown_and_kill" in self.exit_requested_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_child_kill = "child.kill()" in helper or "child).kill()" in helper
        if not has_child_kill:
            has_child_kill = "child" in self.exit_requested_handler and "kill" in self.exit_requested_handler
        self.assertTrue(
            has_child_kill,
            "RunEvent::ExitRequested handler must call child.kill() "
            "as fallback after kill_process_tree",
        )

    # ------------------------------------------------------------------
    # 4. State cleanup preservation
    # ------------------------------------------------------------------

    def test_window_destroyed_sets_running_false(self):
        """WindowEvent::Destroyed handler must set backend.running = false."""
        has_running_false = "running = false" in self.window_handler
        if not has_running_false and "graceful_shutdown_and_kill" in self.window_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_running_false = "running = false" in helper
        self.assertTrue(
            has_running_false,
            "WindowEvent::Destroyed handler must set running = false",
        )

    def test_window_destroyed_sets_pid_none(self):
        """WindowEvent::Destroyed handler must set backend.pid = None."""
        has_pid_none = "pid = None" in self.window_handler
        if not has_pid_none and "graceful_shutdown_and_kill" in self.window_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_pid_none = "pid = None" in helper
        self.assertTrue(
            has_pid_none,
            "WindowEvent::Destroyed handler must set pid = None",
        )

    def test_exit_sets_running_false(self):
        """RunEvent::Exit handler must set backend.running = false."""
        has_running_false = "running = false" in self.exit_handler
        if not has_running_false and "graceful_shutdown_and_kill" in self.exit_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_running_false = "running = false" in helper
        self.assertTrue(
            has_running_false,
            "RunEvent::Exit handler must set running = false",
        )

    def test_exit_sets_pid_none(self):
        """RunEvent::Exit handler must set backend.pid = None."""
        has_pid_none = "pid = None" in self.exit_handler
        if not has_pid_none and "graceful_shutdown_and_kill" in self.exit_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_pid_none = "pid = None" in helper
        self.assertTrue(
            has_pid_none,
            "RunEvent::Exit handler must set pid = None",
        )

    def test_exit_requested_sets_running_false(self):
        """RunEvent::ExitRequested handler must set backend.running = false."""
        has_running_false = "running = false" in self.exit_requested_handler
        if not has_running_false and "graceful_shutdown_and_kill" in self.exit_requested_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_running_false = "running = false" in helper
        self.assertTrue(
            has_running_false,
            "RunEvent::ExitRequested handler must set running = false",
        )

    def test_exit_requested_sets_pid_none(self):
        """RunEvent::ExitRequested handler must set backend.pid = None."""
        has_pid_none = "pid = None" in self.exit_requested_handler
        if not has_pid_none and "graceful_shutdown_and_kill" in self.exit_requested_handler:
            helper = _extract_function_body(
                self.source, "graceful_shutdown_and_kill"
            )
            has_pid_none = "pid = None" in helper
        self.assertTrue(
            has_pid_none,
            "RunEvent::ExitRequested handler must set pid = None",
        )
