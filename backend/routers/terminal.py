"""WebSocket terminal endpoint -- spawns an interactive PTY shell.

Provides a real embedded terminal (like VS Code integrated terminal)
inside the SwarmAI desktop app. Each WebSocket connection gets its
own PTY session with a login shell.

Protocol:
  Client -> Server:
    - JSON {"type": "init", "cols": N, "rows": N}  (first message)
    - JSON {"type": "resize", "cols": N, "rows": N}
    - Raw text (keystrokes)
  Server -> Client:
    - Raw bytes (terminal output)
"""

import asyncio
import fcntl
import json
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["terminal"])


class PTYSession:
    """Manages a single PTY session (shell process + master fd)."""

    def __init__(self) -> None:
        self.master_fd: Optional[int] = None
        self.proc: Optional[subprocess.Popen] = None

    def start(self, cols: int = 80, rows: int = 24) -> None:
        """Spawn a login shell attached to a new PTY."""
        master_fd, slave_fd = pty.openpty()

        # Set initial window size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

        # Build clean env for the shell
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.setdefault("LC_ALL", "en_US.UTF-8")
        env.setdefault("LANG", "en_US.UTF-8")

        shell = os.environ.get("SHELL", "/bin/zsh")

        self.proc = subprocess.Popen(
            [shell, "-l"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            env=env,
        )
        os.close(slave_fd)  # Parent doesn't need the slave side
        self.master_fd = master_fd

        logger.info(
            "PTY started: pid=%d shell=%s size=%dx%d",
            self.proc.pid, shell, cols, rows,
        )

    def resize(self, cols: int, rows: int) -> None:
        """Update PTY window size (triggers SIGWINCH in shell)."""
        if self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def write(self, data: str) -> None:
        """Write text input to the PTY."""
        if self.master_fd is not None:
            os.write(self.master_fd, data.encode("utf-8"))

    def write_bytes(self, data: bytes) -> None:
        """Write raw bytes to the PTY."""
        if self.master_fd is not None:
            os.write(self.master_fd, data)

    def is_alive(self) -> bool:
        """Check if the shell process is still running."""
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def close(self) -> None:
        """Terminate the shell and close the PTY."""
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

        if self.proc is not None:
            pid = self.proc.pid
            try:
                os.killpg(os.getpgid(pid), signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            self.proc = None
            logger.info("PTY closed: pid=%d", pid)


@router.websocket("/ws/terminal")
async def terminal_websocket(ws: WebSocket) -> None:
    """Interactive terminal over WebSocket."""
    await ws.accept()
    session = PTYSession()
    reader_task: Optional[asyncio.Task] = None

    try:
        # 1. Wait for init message with terminal dimensions
        init_raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        init_msg = json.loads(init_raw)
        cols = init_msg.get("cols", 80)
        rows = init_msg.get("rows", 24)

        session.start(cols, rows)
        master_fd = session.master_fd
        assert master_fd is not None

        loop = asyncio.get_event_loop()

        # 2. Background task: PTY output -> WebSocket
        async def read_pty() -> None:
            while True:
                try:
                    # Block up to 20ms in thread pool waiting for PTY output
                    readable = await loop.run_in_executor(
                        None,
                        lambda: select.select([master_fd], [], [], 0.02)[0],
                    )
                    if readable:
                        data = os.read(master_fd, 65536)
                        if not data:
                            break  # EOF -- shell exited
                        await ws.send_bytes(data)

                    # Check if shell process exited
                    if not session.is_alive():
                        # Drain remaining output
                        try:
                            while True:
                                leftover = os.read(master_fd, 65536)
                                if not leftover:
                                    break
                                await ws.send_bytes(leftover)
                        except OSError:
                            pass
                        break
                except (OSError, IOError, ValueError):
                    break

        reader_task = asyncio.create_task(read_pty())

        # 3. Main loop: WebSocket input -> PTY
        while True:
            msg = await ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            if "text" in msg:
                text: str = msg["text"]

                # JSON control messages start with '{'
                if text.startswith("{"):
                    try:
                        ctrl = json.loads(text)
                        msg_type = ctrl.get("type")
                        if msg_type == "resize":
                            session.resize(ctrl["cols"], ctrl["rows"])
                            continue
                        if msg_type == "init":
                            continue  # Already handled above
                    except (json.JSONDecodeError, KeyError):
                        pass  # Not JSON -- treat as raw input

                # Regular keystroke input
                session.write(text)

            elif "bytes" in msg:
                session.write_bytes(msg["bytes"])

    except WebSocketDisconnect:
        logger.debug("Terminal WebSocket disconnected by client")
    except asyncio.TimeoutError:
        logger.warning("Terminal WebSocket: no init message within timeout")
    except Exception as e:
        logger.error("Terminal WebSocket error: %s", e, exc_info=True)
    finally:
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
        session.close()
