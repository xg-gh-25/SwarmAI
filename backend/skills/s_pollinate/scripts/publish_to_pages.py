#!/usr/bin/env python3
"""
Publish Pollinate outputs to the user's own GitHub Pages repo.

Usage:
    python publish_to_pages.py [--all]           # publish all unpublished outputs
    python publish_to_pages.py <dir>             # publish a specific Pollinate output dir
    python publish_to_pages.py --rebuild-index   # only regenerate index.html

Requires: git, gh CLI authenticated.

The target is the ``github_pages`` entry of the user's private channel config
~/.swarm-ai/pollinate-accounts.yaml (the same file publish_meta.py reads):

    github_pages:
      enabled: true
      repo: owner/name                      # the Pages repo to publish into
      url: https://owner.github.io/name     # optional; derived from repo if absent
      clone_dir: ~/.swarm-ai/pollinate-pages/name   # optional local clone

No entry, or enabled: false -> nothing is cloned or pushed; the script says so and
exits 0.

Architecture:
    Knowledge/Pollinate/{date-slug}/ → <pages repo>/content/{date-slug}/
    Auto-generates index.html (gallery page) on every publish.
"""

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
import os
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from brand_config import load_brand, signature  # noqa: E402
from publish_meta import load_accounts  # noqa: E402


def _swarmws() -> Path:
    """The SwarmWS root, resolved exactly as backend config.get_swarmws (a skill script
    cannot import backend config): SWARM_WORKSPACE, then SWARMWS, then
    $SWARM_DATA_DIR/SwarmWS, then ~/.swarm-ai/SwarmWS."""
    for var in ("SWARM_WORKSPACE", "SWARMWS"):
        value = os.environ.get(var, "").strip()
        if value:
            return Path(os.path.expanduser(value))
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai") / "SwarmWS"

def _app_data_dir() -> Path:
    """The app data dir, resolved exactly as backend config.get_app_data_dir (a skill
    script cannot import backend config): $SWARM_DATA_DIR, then ~/.swarm-ai."""
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai")


POLLINATE_DIR = _swarmws() / "Knowledge" / "Pollinate"
_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_][A-Za-z0-9._-]*$")

# Set by configure() from the user's github_pages entry before anything runs.
REPO_NAME: str = ""
REPO_URL: str = ""
PAGES_URL: str = ""
CLONE_DIR: Path = Path()
PUBLISHED_MANIFEST: Path = Path()


def pages_target(accounts: dict | None = None) -> dict | None:
    """The configured GitHub Pages target, or None when publishing is not set up."""
    entry = (load_accounts() if accounts is None else accounts).get("github_pages") or {}
    if not isinstance(entry, dict):
        return None
    enabled = entry.get("enabled", True)
    if enabled is not True and str(enabled).strip().lower() not in ("true", "yes", "on", "1"):
        return None  # only a clearly-on value publishes; anything else is off
    repo = str(entry.get("repo") or "").strip()
    if not repo:
        return None
    if not _REPO_RE.match(repo) or repo.split("/", 1)[1] in (".", ".."):
        raise ValueError(f"github_pages.repo must be owner/name, got {repo!r}")
    owner, name = repo.split("/", 1)
    url = str(entry.get("url") or f"https://{owner}.github.io/{name}").rstrip("/")
    clone = (Path(str(entry["clone_dir"])).expanduser() if entry.get("clone_dir")
             else _app_data_dir() / "pollinate-pages" / name)
    return {"repo": repo, "repo_url": f"https://github.com/{repo}.git", "pages_url": url,
            "clone_dir": clone}


def configure(target: dict) -> None:
    global REPO_NAME, REPO_URL, PAGES_URL, CLONE_DIR, PUBLISHED_MANIFEST
    REPO_NAME, REPO_URL, PAGES_URL = target["repo"], target["repo_url"], target["pages_url"]
    CLONE_DIR = target["clone_dir"]
    PUBLISHED_MANIFEST = CLONE_DIR / ".published.json"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _check_clone(clone: Path) -> None:
    """Refuse a clone_dir that is not its own git root with origin == the Pages repo.

    clone_dir is user-configurable, and everything under it is committed and uploaded:
    an existing folder that is some other repo (or sits inside one) must never be used.
    """
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=clone, check=False)
    origin = run(["git", "remote", "get-url", "origin"], cwd=clone, check=False)
    ok_origin = origin.stdout.strip() in (REPO_URL, REPO_URL[: -len(".git")],
                                          f"git@github.com:{REPO_NAME}.git")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != clone.resolve() or not ok_origin:
        raise SystemExit(f"Refusing to publish: {clone} is not a clone of {REPO_NAME} "
                         "(set github_pages.clone_dir to an empty or dedicated folder)")


def ensure_repo() -> Path:
    """Ensure local clone exists and is up to date."""
    if CLONE_DIR.exists():
        _check_clone(CLONE_DIR)
        run(["git", "pull", "--rebase"], cwd=CLONE_DIR, check=False)
    else:
        CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", REPO_URL, str(CLONE_DIR)])
    return CLONE_DIR


def load_manifest() -> dict:
    """Load the published manifest tracking what's already published."""
    if PUBLISHED_MANIFEST.exists():
        return json.loads(PUBLISHED_MANIFEST.read_text())
    return {"published": [], "last_updated": None}


def save_manifest(manifest: dict):
    """Save the published manifest."""
    manifest["last_updated"] = datetime.now().isoformat()
    PUBLISHED_MANIFEST.write_text(json.dumps(manifest, indent=2))


def find_publishable_content(source_dir: Path) -> list[dict]:
    """Find HTML and PNG files worth publishing from a Pollinate output dir."""
    items = []
    for f in sorted(source_dir.rglob("*")):
        if f.suffix in (".html", ".png", ".md") and not f.name.startswith("."):
            # Skip internal files
            if f.name in ("REPORT.md", "publish_dashboard.html", "review_results.md"):
                continue
            rel = f.relative_to(source_dir)
            items.append({
                "source": str(f),
                "relative": str(rel),
                "type": f.suffix[1:],
                "size": f.stat().st_size,
            })
    return items


def copy_content(source_dir: Path, dest_dir: Path) -> int:
    """Copy publishable content from source to destination."""
    items = find_publishable_content(source_dir)
    copied = 0
    for item in items:
        src = Path(item["source"])
        dst = dest_dir / item["relative"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied


def generate_index(repo_dir: Path):
    """Generate index.html gallery page."""
    content_dir = repo_dir / "content"
    if not content_dir.exists():
        content_dir.mkdir(parents=True)

    # Collect all content dirs sorted by date (newest first)
    dirs = sorted(
        [d for d in content_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )

    # Build gallery cards
    cards_html = ""
    for d in dirs:
        slug = d.name
        # Find poster HTMLs in this dir
        htmls = sorted(d.rglob("*.html"))
        pngs = sorted(d.rglob("*.png"))

        # Extract date and title from slug (YYYY-MM-DD-title-here)
        parts = slug.split("-", 3)
        date_str = "-".join(parts[:3]) if len(parts) >= 3 else slug
        title = parts[3].replace("-", " ").title() if len(parts) > 3 else slug

        # Build file links
        links = ""
        for h in htmls[:6]:  # max 6 links per card
            rel = h.relative_to(content_dir)
            name = h.stem.replace("-", " ").replace("_", " ")
            links += f'        <a href="{rel}" class="file-link">{name}</a>\n'

        img_preview = ""
        if pngs:
            first_png = pngs[0].relative_to(content_dir)
            img_preview = f'      <img src="{first_png}" class="preview" loading="lazy" />\n'

        cards_html += f"""    <article class="card">
      <div class="card-header">
        <span class="date">{date_str}</span>
        <h2>{title}</h2>
      </div>
{img_preview}      <div class="links">
{links}      </div>
      <div class="meta">{len(htmls)} HTML, {len(pngs)} PNG</div>
    </article>
"""

    name = str((load_brand().get("brand") or {}).get("name") or "").strip()
    gallery_title = html.escape(f"{name} Content Gallery" if name else "Content Gallery")
    link = signature()["link"]
    gallery_credit = "Generated by Pollinate"
    if name and link and urlparse(link).scheme in ("http", "https"):
        gallery_credit = (f'Generated by <a href="{html.escape(link, quote=True)}">'
                          f"{html.escape(name)}</a> with Pollinate")

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{gallery_title}</title>
    <meta name="description" content="Auto-published content: posters, articles, and media packages.">
    <style>
        :root {{
            --bg: #0a0a0f;
            --surface: #14141f;
            --border: #2a2a3a;
            --text: #e4e4ef;
            --muted: #8888aa;
            --accent: #c8a832;
            --accent-dim: #8a7420;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 2rem;
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            text-align: center;
            padding: 3rem 0 2rem;
            border-bottom: 1px solid var(--border);
            margin-bottom: 2rem;
        }}
        header h1 {{
            font-size: 2rem;
            font-weight: 300;
            letter-spacing: 0.05em;
            color: var(--accent);
        }}
        header p {{
            color: var(--muted);
            margin-top: 0.5rem;
            font-size: 0.9rem;
        }}
        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 1.5rem;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
            transition: border-color 0.2s;
        }}
        .card:hover {{ border-color: var(--accent-dim); }}
        .card-header {{ margin-bottom: 1rem; }}
        .card-header .date {{
            font-size: 0.75rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        .card-header h2 {{
            font-size: 1.1rem;
            font-weight: 500;
            margin-top: 0.25rem;
        }}
        .preview {{
            width: 100%;
            max-height: 200px;
            object-fit: cover;
            border-radius: 4px;
            margin-bottom: 1rem;
        }}
        .links {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
        }}
        .file-link {{
            font-size: 0.8rem;
            color: var(--accent);
            text-decoration: none;
            padding: 0.25rem 0.5rem;
            background: rgba(200, 168, 50, 0.1);
            border-radius: 4px;
            border: 1px solid var(--accent-dim);
        }}
        .file-link:hover {{
            background: rgba(200, 168, 50, 0.2);
        }}
        .meta {{
            font-size: 0.75rem;
            color: var(--muted);
        }}
        footer {{
            text-align: center;
            padding: 3rem 0 1rem;
            color: var(--muted);
            font-size: 0.8rem;
            border-top: 1px solid var(--border);
            margin-top: 2rem;
        }}
        footer a {{ color: var(--accent); text-decoration: none; }}
    </style>
</head>
<body>
    <header>
        <h1>{gallery_title}</h1>
        <p>Auto-published by Pollinate — Message First, Format Follows</p>
        <p style="margin-top: 0.25rem; font-size: 0.8rem;">{len(dirs)} collections • Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    </header>
    <div class="gallery">
{cards_html}    </div>
    <footer>
        <p>{gallery_credit}</p>
    </footer>
</body>
</html>"""

    (repo_dir / "index.html").write_text(index_html)


def publish_dir(source_dir: Path, repo_dir: Path, manifest: dict) -> bool:
    """Publish a single Pollinate output directory."""
    slug = source_dir.name

    if slug in manifest["published"]:
        print(f"  SKIP {slug} (already published)")
        return False

    dest = repo_dir / "content" / slug
    dest.mkdir(parents=True, exist_ok=True)

    copied = copy_content(source_dir, dest)
    if copied == 0:
        print(f"  SKIP {slug} (no publishable content)")
        return False

    manifest["published"].append(slug)
    print(f"  PUBLISH {slug} ({copied} files)")
    return True


def publish_all(repo_dir: Path):
    """Publish all unpublished Pollinate outputs."""
    manifest = load_manifest()
    published_count = 0

    for d in sorted(POLLINATE_DIR.iterdir()):
        if not d.is_dir():
            continue
        if publish_dir(d, repo_dir, manifest):
            published_count += 1

    if published_count > 0:
        generate_index(repo_dir)
        save_manifest(manifest)
        git_commit_and_push(repo_dir, f"publish: {published_count} new collections")
    else:
        print("Nothing new to publish.")

    return published_count


def publish_single(dir_path: Path, repo_dir: Path):
    """Publish a specific directory."""
    manifest = load_manifest()

    if not dir_path.exists():
        print(f"ERROR: {dir_path} does not exist")
        sys.exit(1)

    # Force re-publish (remove from manifest if exists)
    slug = dir_path.name
    if slug in manifest["published"]:
        manifest["published"].remove(slug)

    if publish_dir(dir_path, repo_dir, manifest):
        generate_index(repo_dir)
        save_manifest(manifest)
        git_commit_and_push(repo_dir, f"publish: {slug}")


def git_commit_and_push(repo_dir: Path, message: str):
    """Commit locally, then `git push origin main` (non-blocking on failure).

    Only a plain git push: it goes through the same controls as any push (the repo's
    own hooks, the organization's push policy, a non-fast-forward refusal). If the
    push is refused, the content stays committed in the local clone.
    """
    run(["git", "add", "-A"], cwd=repo_dir)

    # Check if there are changes to commit
    result = run(["git", "status", "--porcelain"], cwd=repo_dir)
    if not result.stdout.strip():
        print("No changes to commit.")
        return

    run(["git", "commit", "-m", message], cwd=repo_dir)

    result = run(["git", "push", "origin", "main"], cwd=repo_dir, check=False)
    if result.returncode != 0:
        print("  WARNING: Push refused or failed. Content is committed in the local clone.")
        print(f"  git said: {result.stderr.strip()[:300]}")
        print("  Fix the cause (repo permission, push policy, diverged clone), then re-run --all.")
        return

    print(f"  PUSHED: {message}")
    print(f"  URL: {PAGES_URL}/")


def main():
    parser = argparse.ArgumentParser(description="Publish Pollinate outputs to GitHub Pages")
    parser.add_argument("dir", nargs="?", help="Specific Pollinate dir to publish")
    parser.add_argument("--all", action="store_true", help="Publish all unpublished outputs")
    parser.add_argument("--rebuild-index", action="store_true", help="Only regenerate index.html")
    args = parser.parse_args()

    target = pages_target()
    if target is None:
        print("GitHub Pages publishing is not configured (no github_pages entry in "
              "~/.swarm-ai/pollinate-accounts.yaml) — skipped, nothing pushed.")
        return
    configure(target)
    repo_dir = ensure_repo()

    if args.rebuild_index:
        generate_index(repo_dir)
        git_commit_and_push(repo_dir, "chore: rebuild index")
    elif args.dir:
        source = Path(args.dir)
        if not source.is_absolute():
            source = POLLINATE_DIR / args.dir
        publish_single(source, repo_dir)
    elif args.all:
        count = publish_all(repo_dir)
        print(f"\nDone. {count} collections published to {PAGES_URL}/")
    else:
        # Default: publish all
        count = publish_all(repo_dir)
        print(f"\nDone. {count} collections published to {PAGES_URL}/")


if __name__ == "__main__":
    main()
