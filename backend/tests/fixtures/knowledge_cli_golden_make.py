"""Record today's knowledge-CLI output on a fixed fixture (run against MAIN's backend)."""
import contextlib, io, json, os, sys, tempfile
from pathlib import Path
backend = sys.argv[1]
sys.path.insert(0, backend)
tmp = Path(tempfile.mkdtemp(prefix="cogfix"))
os.environ["SWARM_WORKSPACE"] = str(tmp); os.environ["SWARMWS"] = str(tmp)
os.environ["SWARM_DATA_DIR"] = str(tmp / "data")
und = tmp / "Projects" / "P" / "2-understanding"; und.mkdir(parents=True)
(und / "TECH.md").write_text(
    "# TECH\n\n## Traps\n\n- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"
    "  <!-- ref:0 | last:none | decay:active | source:manual -->\n\n"
    "- [pitfall] **Wombat rule** — wombats dig at night, quokkas do not.\n", encoding="utf-8")
from core import knowledge_cli
out = {}
for name, argv in {
    "find": ["find", "--project", "P", "--query", "quokkas midnight"],
    "find_json": ["find", "--project", "P", "--query", "quokkas", "--json"],
    "nothing": ["find", "--project", "P", "--query", "zzzunmatched"],
    "unknown": ["find", "--project", "Nope", "--query", "x"],
    "map": ["map", "--project", "P"],
    "get_bad": ["get", "--project", "P", "deadbeef"],
    "lib_json": ["find", "--in", "library", "--query", "quokkas", "--json"],
}.items():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = knowledge_cli.main(argv)
    out[name] = [code, buf.getvalue().replace(str(tmp), "<WS>")]
print(json.dumps(out, ensure_ascii=False, indent=1))
