"""Run a subprocess whose stdin is a temp FILE instead of a pipe.

Use instead of ``subprocess.run(..., input=data)`` for a child that writes output
while it is still reading (``git check-ignore --stdin`` flushes one line per path).

Why (measured 2026-10-22, macOS under memory pressure): ``communicate()`` writes
``input`` in 512-byte chunks whenever poll() reports the stdin pipe writable, but
the kernel reported it writable with less room than that; ``os.write`` then blocked
while the child blocked writing its stdout, which nobody read. The ``timeout=`` is
checked between writes, so it never fired: the 300s code-intel reindex hung there,
and a watcher's ``git check-ignore`` was found still blocked 25 min later. A file
cannot fill, so the child reads at its own pace and the timeout works.

Public: ``run_with_stdin_file(cmd, data, **kwargs)`` — ``data`` is bytes, or str
(encoded UTF-8); ``kwargs`` go to ``subprocess.run`` (``capture_output``, ``text``,
``timeout``, ``cwd``, ...). Raises what ``subprocess.run`` raises, plus ``OSError``
when the temp file cannot be made.
"""
from __future__ import annotations

import subprocess
import tempfile
from typing import Any, Sequence, Union


def run_with_stdin_file(cmd: Sequence[str], data: Union[bytes, str],
                        **kwargs: Any) -> subprocess.CompletedProcess:
    if "input" in kwargs or "stdin" in kwargs:
        raise TypeError("run_with_stdin_file supplies stdin itself")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    with tempfile.TemporaryFile() as fh:
        fh.write(raw)
        fh.seek(0)
        return subprocess.run(list(cmd), stdin=fh, **kwargs)
