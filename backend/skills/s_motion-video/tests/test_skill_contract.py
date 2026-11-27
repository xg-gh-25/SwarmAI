"""The skill as a public product: one runtime contract, declared dependencies, portable paths, recorded licences.
Run: python3 -m pytest tests/test_skill_contract.py -q   (from the skill directory)"""
import ast
import os
import re
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIRD_PARTY = {"edge_tts", "playwright", "numpy", "PIL", "jsonschema", "boto3", "botocore"}


def read(*p):
    return open(os.path.join(SKILL, *p), encoding="utf-8").read()


def frontmatter_list(key):
    head = read("SKILL.md").split("\n---", 1)[0]
    m = re.search(rf"^{key}:\s*\[([^\]]*)\]", head, re.M)
    return [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []


def test_skill_md_states_the_runtime_contract():
    text = read("SKILL.md")
    assert '"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/' in text
    assert not re.search(r'(?<![\w/.$-])python3?\s+"?\$SKILL_DIR', text), "scripts run through SwarmAI's py, not python3"
    deps, optional = frontmatter_list("python-deps"), frontmatter_list("python-optional-deps")
    assert set(deps) == {"boto3", "PIL"} and not set(deps) & set(optional)
    used = set()
    for name in os.listdir(os.path.join(SKILL, "scripts")):
        if name.endswith(".py"):
            for node in ast.walk(ast.parse(read("scripts", name))):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                        [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else [])
                used |= {m.split(".")[0] for m in mods} & THIRD_PARTY
    used.discard("botocore")   # ships with boto3
    # render.py drives SwarmAI's pinned browser runtime (render_driver.mjs): no script imports Python Playwright, and
    # SKILL.md declares none
    declared = set(deps) | set(optional)
    assert "playwright" not in used and "playwright" not in declared
    assert used == declared, f"imports {sorted(used)} vs declared {sorted(declared)}"
    for word in ("ffmpeg", "Polly", "edge-tts", "--engine auto"):
        assert word in text


def test_workspace_path_follows_swarm_workspace():
    for f in (("references", "project-contract.md"), ("references", "stylegen.md")):
        text = read(*f)
        assert "${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/Services/motion-video/" in text, f
        assert "~/.swarm-ai/SwarmWS" not in text, f


def test_licence_and_terms_are_recorded():
    gsap = read("runtime", "GSAP-LICENSE.txt")
    assert "https://gsap.com/standard-license" in gsap and "DECISION" in gsap
    assert "Prohibited Uses" in gsap and "Webflow" in gsap and "3.15.1" in gsap
    head = open(os.path.join(SKILL, "runtime", "gsap.min.js"), encoding="utf-8").read(400)
    assert "GSAP 3.15.1" in head and "standard-license" in head   # vendored unmodified, notice kept
    notice = " ".join(read("NOTICE.md").split())
    for must in ("https://github.com/rany2/edge-tts", "LGPL", "https://www.microsoft.com/en-us/servicesagreement",
                 "https://aws.amazon.com/service-terms/", "references/styles/fonts/FONTS.md", "SIL Open Font License"):
        assert must in notice, must
    assert "macOS system fonts" not in notice


def test_tts_imports_nothing_third_party_at_module_level():
    """So `py tts.py` reaches its own one-line exit-2 messages instead of the interpreter's import error."""
    tree = ast.parse(read("scripts", "tts.py"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {(a.name if isinstance(n, ast.Import) else n.module).split(".")[0] for n in top for a in n.names}
    assert not names - set(sys.stdlib_module_names), sorted(names - set(sys.stdlib_module_names))
