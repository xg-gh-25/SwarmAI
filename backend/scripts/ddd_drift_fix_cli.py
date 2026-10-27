"""List and undo the drift fixes the weekly DDD self-audit applied (core/ddd_drift_fix).

    "$SWARM_BACKEND_BIN" py -m scripts.ddd_drift_fix_cli list <project>
    "$SWARM_BACKEND_BIN" py -m scripts.ddd_drift_fix_cli undo <project> <index>

``list`` prints one line per fix still in force (index, doc, old → new). ``undo`` puts one
fix back and records it, so the self-audit never applies the same fix again. Output is
JSON; exit 0 on success, 1 when the undo was refused, 2 on bad use.
"""
from __future__ import annotations

import json
import sys

from core.ddd_drift_fix import past_fixes, undo_drift_fix
from jobs.paths import SWARMWS


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("list", "undo") or (argv[0] == "undo" and len(argv) != 3):
        print(__doc__, file=sys.stderr)
        return 2
    project = SWARMWS / "Projects" / argv[1]
    if "/" in argv[1] or argv[1].startswith(".") or not project.is_dir():
        print(json.dumps({"ok": False, "reason": f"no project {argv[1]}"}))
        return 2
    if argv[0] == "list":
        rows = [{k: r.get(k) for k in ("index", "target_doc", "old", "new", "run", "undone")}
                for r in past_fixes(project)]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    try:
        index = int(argv[2])
    except ValueError:
        print(__doc__, file=sys.stderr)
        return 2
    res = undo_drift_fix(project, index)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
