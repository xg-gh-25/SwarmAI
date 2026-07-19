import re, glob, os
from pathlib import Path

SK = Path("backend/skills")
rows = []
for skdir in sorted(SK.glob("s_*")):
    scripts = list(skdir.glob("scripts/*.py"))
    if not scripts:
        continue
    # collect argparse long-flags across all py scripts in this skill
    flags = set()
    has_argparse = False
    for py in scripts:
        try:
            txt = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "add_argument" in txt or "ArgumentParser" in txt:
            has_argparse = True
        for m in re.finditer(r'add_argument\(\s*["\'](--[a-zA-Z0-9][\w-]*)["\']', txt):
            flags.add(m.group(1))
    if not has_argparse:
        rows.append((skdir.name, "no-argparse", 0, 0, []))
        continue
    # gather doc text (INSTRUCTIONS + SKILL)
    doc = ""
    for d in ["INSTRUCTIONS.md", "SKILL.md"]:
        p = skdir / d
        if p.exists():
            doc += p.read_text(encoding="utf-8", errors="replace")
    documented = {f for f in flags if f in doc}
    missing = sorted(flags - documented)
    rows.append((skdir.name, "argparse", len(flags), len(missing), missing))

print(f"{'skill':32} {'flags':>5} {'undoc':>5}  missing-in-docs")
print("-"*90)
tot_flags = tot_missing = 0
for name, kind, nflags, nmiss, miss in rows:
    if kind == "no-argparse":
        print(f"{name:32} {'—':>5} {'—':>5}  (no argparse / not a CLI)")
        continue
    tot_flags += nflags; tot_missing += nmiss
    show = ", ".join(miss[:6]) + (" …" if len(miss) > 6 else "")
    flag_icon = "OK" if nmiss == 0 else "DRIFT"
    print(f"{name:32} {nflags:>5} {nmiss:>5}  [{flag_icon}] {show}")
print("-"*90)
print(f"TOTAL argparse flags: {tot_flags} | undocumented (drift): {tot_missing} | drift rate: {tot_missing/tot_flags*100:.0f}%" if tot_flags else "no flags")
