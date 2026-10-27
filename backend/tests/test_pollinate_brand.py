"""Pollinate's brand and publish target come from the user's own config (P7).

- brand_config: shipped neutral defaults, user's pollinate-brand.yaml deep-merged on top;
  the signature resolves link_text and QR paths;
- convergence_gate L7 and pollinate_validator checks 2-3 require exactly the configured
  signature: nothing configured -> nothing required;
- publish_to_pages: no github_pages entry -> skipped, nothing cloned or pushed; the repo
  name is validated; the gallery title comes from the brand.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "s_pollinate" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import brand_config  # noqa: E402
import convergence_gate  # noqa: E402
import pollinate_validator  # noqa: E402
import publish_to_pages  # noqa: E402

MINE = """
brand:
  name: "Acme"
signature:
  watermark: "Made by Acme"
  link: "https://acme.example/blog/"
  qr:
    dark_on_light: "qr/acme-dark.png"
"""


@pytest.fixture
def no_brand(tmp_path, monkeypatch):
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(tmp_path / "absent.yaml"))


@pytest.fixture
def my_brand(tmp_path, monkeypatch):
    f = tmp_path / "pollinate-brand.yaml"
    f.write_text(MINE)
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(f))
    return f


def test_shipped_default_carries_no_brand(no_brand):
    sig = brand_config.signature()
    assert sig["watermark"] is None and sig["link"] is None and sig["link_text"] is None
    assert brand_config.qr_basenames(sig) == []
    assert not brand_config.load_brand()["brand"]["name"]


def test_user_file_merges_over_defaults(my_brand):
    brand = brand_config.load_brand()
    assert brand["brand"]["name"] == "Acme"
    assert brand["colors"]["primary"] == "#FF6B35"  # untouched default survives the merge
    sig = brand_config.signature()
    assert sig["link_text"] == "acme.example/blog"
    assert sig["qr"]["dark_on_light"] == str(my_brand.parent / "qr" / "acme-dark.png")
    assert sig["qr"]["light_on_dark"] is None
    assert brand_config.qr_basenames(sig) == ["acme-dark.png"]


def test_l7_requires_nothing_without_a_brand(no_brand):
    assert convergence_gate.check_l7_brand("<html><body>poster</body></html>") == []


def test_l7_requires_each_configured_element(my_brand):
    errors = convergence_gate.check_l7_brand("<html><body>poster</body></html>")
    assert len(errors) == 3
    full = '<p>Made by Acme</p><img src="acme-dark.png"><p>acme.example/blog</p>'
    assert convergence_gate.check_l7_brand(full) == []


def _delivery(root: Path, text: str) -> None:
    (root / "tracks" / "poster").mkdir(parents=True)
    (root / "delivery.md").write_text("Platform matrix\n" + text)


def _brand_errors(result: dict) -> list[str]:
    return [e for e in result["errors"] if "QR" in e or "link" in e]


def test_validator_skips_brand_checks_without_a_brand(no_brand, tmp_path):
    root = tmp_path / "content"
    _delivery(root, "no link here")
    assert _brand_errors(pollinate_validator.validate_delivery(str(root))) == []


def test_validator_requires_configured_link_and_qr(my_brand, tmp_path):
    root = tmp_path / "content"
    _delivery(root, "no link here")
    assert len(_brand_errors(pollinate_validator.validate_delivery(str(root)))) == 2
    (root / "delivery.md").write_text("Platform matrix\nsee acme.example/blog")
    (root / "acme-dark.png").write_bytes(b"png")
    assert _brand_errors(pollinate_validator.validate_delivery(str(root))) == []


def test_pages_target_needs_an_enabled_repo(tmp_path):
    assert publish_to_pages.pages_target({}) is None
    assert publish_to_pages.pages_target({"github_pages": {"enabled": False, "repo": "a/b"}}) is None
    with pytest.raises(ValueError):
        publish_to_pages.pages_target({"github_pages": {"repo": "a/b; rm -rf ~"}})
    t = publish_to_pages.pages_target({"github_pages": {"repo": "me/site"}})
    assert t["repo_url"] == "https://github.com/me/site.git"
    assert t["pages_url"] == "https://me.github.io/site"
    # the default clone lives under the app data dir (SWARM_DATA_DIR in tests), not ~
    assert t["clone_dir"] == publish_to_pages._app_data_dir() / "pollinate-pages" / "site"
    t = publish_to_pages.pages_target({"github_pages": {"repo": "me/site", "clone_dir": "~/x"}})
    assert t["clone_dir"] == Path("~/x").expanduser()


def test_publish_without_target_clones_and_pushes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(publish_to_pages, "load_accounts", lambda: {})
    touched = []
    monkeypatch.setattr(publish_to_pages, "ensure_repo", lambda: touched.append("clone"))
    monkeypatch.setattr(publish_to_pages, "run", lambda *a, **k: touched.append("run"))
    monkeypatch.setattr(sys, "argv", ["publish_to_pages.py", "--all"])
    publish_to_pages.main()
    assert touched == []
    assert "not configured" in capsys.readouterr().out


def test_gallery_title_follows_the_brand(my_brand, tmp_path):
    (tmp_path / "content").mkdir()
    publish_to_pages.generate_index(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert "<title>Acme Content Gallery</title>" in page
    assert 'href="https://acme.example/blog/"' in page
    assert "SwarmAI" not in page


# ── review round 1: broken config, matching, publish target safety ─────────────

def test_broken_brand_file_fails_the_gates_instead_of_crashing(tmp_path, monkeypatch):
    for body in ("signature: {qr: just-a-string.png}", "signature: [", "- a list"):
        f = tmp_path / "pollinate-brand.yaml"
        f.write_text(body)
        monkeypatch.setenv("POLLINATE_BRAND_FILE", str(f))
        errors = convergence_gate.check_l7_brand("<html></html>")
        assert len(errors) == 1 and "brand config" in errors[0]
        root = tmp_path / f"c{abs(hash(body))}"
        _delivery(root, "x")
        result = pollinate_validator.validate_delivery(str(root))
        assert result["valid"] is False
        assert any("BRAND CONFIG" in e for e in result["errors"])


def test_l7_matches_entities_and_qr_names_at_a_boundary(my_brand):
    page = '<p>Made by Acme</p><img src="myacme-dark.png"><p>acme.example/blog</p>'
    assert any("QR" in e for e in convergence_gate.check_l7_brand(page))
    page = '<p>Made&#32;by Acme</p><img src="./img/acme-dark.png"><p>acme.example/blog</p>'
    assert convergence_gate.check_l7_brand(page) == []


def test_validator_needs_the_qr_file_not_a_mention(my_brand, tmp_path):
    root = tmp_path / "content"
    _delivery(root, "acme.example/blog and acme-dark.png")
    assert any("QR" in e for e in pollinate_validator.validate_delivery(str(root))["errors"])


@pytest.mark.parametrize("repo", ["me/..", "me/.", "me/.hidden", "-me/site", "me-/site", "me/-x"])
def test_pages_target_rejects_unsafe_repo_names(repo):
    with pytest.raises(ValueError):
        publish_to_pages.pages_target({"github_pages": {"repo": repo}})


@pytest.mark.parametrize("enabled", [None, "", "off", "disabled", "n", False, 0])
def test_only_a_clearly_on_value_enables_publishing(enabled):
    assert publish_to_pages.pages_target({"github_pages": {"repo": "me/site", "enabled": enabled}}) is None


@pytest.mark.parametrize("enabled", [True, "true", "yes", "on", 1])
def test_clearly_on_values_enable_publishing(enabled):
    assert publish_to_pages.pages_target({"github_pages": {"repo": "me/site", "enabled": enabled}})


def test_existing_clone_dir_must_be_a_clone_of_the_pages_repo(tmp_path):
    import subprocess
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    publish_to_pages.configure({"repo": "me/site", "repo_url": "https://github.com/me/site.git",
                                "pages_url": "https://me.github.io/site", "clone_dir": other / "sub"})
    (other / "sub").mkdir()
    with pytest.raises(SystemExit, match="Refusing"):
        publish_to_pages._check_clone(other / "sub")  # inside another repo
    with pytest.raises(SystemExit, match="Refusing"):
        publish_to_pages._check_clone(other)  # own root, wrong origin
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin",
                    "https://github.com/me/site.git"], check=True)
    publish_to_pages._check_clone(other)  # a real clone passes


def test_gallery_links_only_http_urls(tmp_path, monkeypatch):
    f = tmp_path / "b.yaml"
    f.write_text('brand: {name: "Acme"}\nsignature: {link: "javascript:alert(1)"}\n')
    monkeypatch.setenv("POLLINATE_BRAND_FILE", str(f))
    (tmp_path / "repo" / "content").mkdir(parents=True)
    publish_to_pages.generate_index(tmp_path / "repo")
    assert "javascript:" not in (tmp_path / "repo" / "index.html").read_text()


def test_publish_pushes_with_plain_git_only(monkeypatch, tmp_path, capsys):
    """No GitHub API path (it bypassed push controls and force-updated the ref)."""
    import subprocess
    calls = []

    def fake_run(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        out = " M index.html\n" if cmd[:2] == ["git", "status"] else ""
        rc = 1 if cmd[:2] == ["git", "push"] else 0
        return subprocess.CompletedProcess(cmd, rc, out, "! [rejected] main -> main (non-fast-forward)")

    def no_raw_subprocess(*a, **k):
        calls.append(["subprocess.run", *map(str, a[0] if a else [])])
        return subprocess.CompletedProcess(a[0] if a else [], 0, "{}", "")

    monkeypatch.setattr(publish_to_pages, "run", fake_run)
    monkeypatch.setattr(publish_to_pages.subprocess, "run", no_raw_subprocess)
    publish_to_pages.git_commit_and_push(tmp_path, "publish: x")
    assert [c[:2] for c in calls] == [["git", "add"], ["git", "status"], ["git", "commit"], ["git", "push"]]
    assert "--force" not in calls[-1] and "-f" not in calls[-1]
    assert "non-fast-forward" in capsys.readouterr().out
