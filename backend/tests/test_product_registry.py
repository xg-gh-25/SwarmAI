"""Tests for the shared PRODUCT registry (Artifacts B′ Run 1).

Covers the 6 acceptance criteria from design_doc art_5c8787b5:
  AC1 — workspace-level products.json with flock+atomic, concurrent-write safe
  AC2 — derive_role delegates kind to needs_human_review, maps §8.2 cases
  AC3 — gitignored PRODUCT dir surfaces DESPITE verdict=process; secret does NOT
  AC4 — external ($HOME/Desktop, /tmp) paths are skipped
  AC5 — backfill_from_gitlog idempotent (keyed by path)
  AC6 — sync core callable off-loop; watcher hook calls register_batch on RAW paths

The registry is built on utils.file_lock + an atomic write-temp+rename primitive,
with a workspace-level flat keyspace.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path):
    """A workspace root with a Projects/ dir (mirrors SwarmWS layout)."""
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Knowledge").mkdir()
    return tmp_path


@pytest.fixture
def git_ws(tmp_path):
    """A workspace root that is a git repo with a .gitignore hiding Projects/*.

    Lets us prove AC3: a gitignored deck under Projects/*/assets still registers
    as a product, while a secret under Projects/* does not.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("Projects/*\n!Projects/SwarmAI/\n")
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Knowledge").mkdir()
    return tmp_path


# ── AC1: workspace-level store, flock+atomic, concurrent-write safe ─────────


class TestAC1WorkspaceStore:
    def test_writes_workspace_level_products_json(self, ws):
        from core.product_registry import ProductRegistry

        reg = ProductRegistry(ws)
        p = ws / "Knowledge" / "Designs" / "deck.html"
        p.parent.mkdir(parents=True)
        p.write_text("<html>")
        reg.register_product(str(p))

        store = ws / ".artifacts" / "products.json"
        assert store.is_file(), "products.json must live at WORKSPACE root .artifacts/"
        data = json.loads(store.read_text())
        assert data["version"] == 1
        paths = [e["path"] for e in data["products"]]
        assert "Knowledge/Designs/deck.html" in paths

    def test_concurrent_writes_lose_no_entry(self, ws):
        """flock serializes read-modify-write — N threads, N entries survive."""
        from core.product_registry import ProductRegistry

        reg = ProductRegistry(ws)
        d = ws / "Knowledge" / "Designs"
        d.mkdir(parents=True)
        n = 12
        for i in range(n):
            (d / f"f{i}.html").write_text("x")

        def worker(i):
            ProductRegistry(ws).register_product(str(d / f"f{i}.html"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = json.loads((ws / ".artifacts" / "products.json").read_text())
        assert len(data["products"]) == n, "lost-update race — an entry vanished"


# ── AC2: derive_role delegates kind, maps §8.2 cases ────────────────────────


class TestAC2DeriveRole:
    @pytest.mark.parametrize(
        "rel_path,repo,expected_role",
        [
            ("Projects/AIDLC/assets/deck.html", None, "Deliverables"),
            # Attachments is INPUT (chat attachments), not a product OUTPUT — excluded
            # entirely (run_fe228bc0). derive_role returns Other so _classify drops it.
            ("Attachments/2026-09-30/x.png", None, "Other"),
            ("Knowledge/Designs/2026-09-30-x.md", None, "Deliverables"),
            ("Projects/SwarmAI/2-understanding/TECH.md", None, "Knowledge"),
            ("Knowledge/Library/2026-09-11-x.md", None, "Knowledge"),
            ("Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md", None, "Pipeline"),
            ("Knowledge/DailyActivity/2026-09-30.md", None, "Activity"),
            ("Knowledge/Signals/2026-09-29.md", None, "Activity"),
            ("some/unclassified/thing.xyz", None, "Other"),
        ],
    )
    def test_role_mapping(self, rel_path, repo, expected_role):
        from core.product_registry import derive_role

        assert derive_role(rel_path, repo) == expected_role

    def test_delegates_kind_not_reimplemented(self):
        """derive_role must call needs_human_review._classify_kind, not fork it."""
        import core.product_registry as pr

        called = {}
        real = pr._classify_kind

        def spy(rel_path, repo):
            called["hit"] = True
            return real(rel_path, repo)

        pr._classify_kind = spy
        try:
            pr.derive_role("Projects/SwarmAI/2-understanding/TECH.md", None)
        finally:
            pr._classify_kind = real
        assert called.get("hit"), "derive_role must DELEGATE to _classify_kind"


# ── AC3: gitignored PRODUCT surfaces despite verdict=process; secret does not ─


class TestAC3GitignoredProducts:
    def test_gitignored_deck_registers_despite_process_verdict(self, git_ws):
        """A deck under a gitignored Projects/*/assets is verdict=process
        (review_worthy=False) — but the PRODUCT path is independent of that
        verdict, so it MUST still register."""
        from core.needs_human_review import needs_human_review
        from core.product_registry import ProductRegistry

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "deck.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")

        # Precondition: the surface verdict drops it (gitignored → process).
        v = needs_human_review(str(deck), swarmws_root=str(git_ws))
        assert v.review_worthy is False and v.kind == "process", (
            "test premise: a gitignored deck must be process/not-review_worthy"
        )

        # The product registry must catch it ANYWAY (decoupled from the verdict).
        ProductRegistry(git_ws).register_batch([str(deck)])
        data = json.loads((git_ws / ".artifacts" / "products.json").read_text())
        entry = next(
            (e for e in data["products"] if e["path"].endswith("assets/deck.html")),
            None,
        )
        assert entry is not None, "gitignored PRODUCT deck must register"
        assert entry["role"] == "Deliverables"
        assert entry["gitignored"] is True

    def test_secret_under_projects_does_not_register(self, git_ws):
        """The allowlist must be a narrow POSITIVE list (assets/Attachments) —
        a secret elsewhere under a gitignored Projects/* must NOT be surfaced."""
        from core.product_registry import ProductRegistry

        secret = git_ws / "Projects" / "AIDLC" / ".env"
        secret.parent.mkdir(parents=True)
        secret.write_text("SECRET=1")

        ProductRegistry(git_ws).register_batch([str(secret)])
        store = git_ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        assert not any(".env" in e["path"] for e in products), (
            "a gitignored secret must never be surfaced as a product"
        )

    def test_secret_not_surfaced_even_when_gitignore_check_errors(self, ws, monkeypatch):
        """RP50/RP55 fail-CLOSED: if git check-ignore CANNOT run (returns None,
        unknown), a non-product path (a .env → role Other) must still NOT register —
        an unknowable path might be a gitignored secret, so the safe side is drop."""
        from core.product_registry import ProductRegistry

        secret = ws / "Projects" / "AIDLC" / ".env"
        secret.parent.mkdir(parents=True)
        secret.write_text("SECRET=1")

        reg = ProductRegistry(ws)
        # Force the BATCHED gitignore check to UNKNOWN (git errored) — must not fail
        # open (Run 2 AC5: per-path _is_gitignored → batched _batch_gitignored).
        monkeypatch.setattr(
            reg, "_batch_gitignored", lambda tree, rels: {rp: None for rp in rels}
        )
        reg.register_batch([str(secret)])

        store = ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        assert not any(".env" in e["path"] for e in products), (
            "on git-check-ignore UNKNOWN, a .env must fail CLOSED (not register)"
        )

    @pytest.mark.parametrize(
        "secret_rel",
        [
            "Projects/AIDLC/assets/.env",
            "Projects/AIDLC/assets/id_rsa",
            "Projects/AIDLC/assets/service.key",
            "Projects/AIDLC/assets/credentials.json",
            "Attachments/.env",
            "Projects/X/node_modules/pkg/assets/secret.pem",
            # Red-Team: denylist-escaping secrets — closed by the positive
            # extension allowlist (none of these has a product extension).
            "Projects/AIDLC/assets/aws_credentials",
            "Projects/AIDLC/assets/token.txt",
            "Projects/AIDLC/assets/passwords.md",
            "Projects/AIDLC/assets/config.yaml",
            "Projects/AIDLC/assets/ID_RSA",
            "Projects/AIDLC/assets/id_rsa.txt",
            "Projects/X/target/assets/branding.png",  # vendored (Rust target/)
        ],
    )
    def test_secret_inside_product_dir_does_not_register(self, ws, secret_rel):
        """Gate-2 Security HIGH + Red-Team: a secret/config/vendored file INSIDE an
        allowlisted product dir must NOT surface. The fix is a POSITIVE
        product-extension allowlist (not a secret denylist, which Red-Team proved
        leaky) — only known media/document extensions under assets/Attachments
        surface; everything else falls to kind classification → Other → dropped."""
        from core.product_registry import ProductRegistry

        p = ws / secret_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("SECRET")
        ProductRegistry(ws).register_batch([str(p)])
        store = ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        base = secret_rel.rsplit("/", 1)[-1]
        assert not any(base in e["path"] for e in products), (
            f"{secret_rel} must NOT register — allowlist must not un-gate secrets/vendor"
        )

    def test_real_product_inside_assets_still_registers(self, ws):
        """The fix must not break the legit case: a deck MAIN product (.html/.pdf)
        under assets/ still surfaces. But (run_fe228bc0) a loose IMAGE under assets/
        is a deck intermediate PART (dropped), and an Attachments/ image is INPUT
        (dropped) — neither is a Deliverable."""
        from core.product_registry import ProductRegistry

        deck = ws / "Projects" / "AIDLC" / "assets" / "deck.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")
        att = ws / "Attachments" / "2026" / "x.png"  # INPUT — must NOT register
        att.parent.mkdir(parents=True)
        att.write_text("png")
        asset_img = ws / "Projects" / "AIDLC" / "assets" / "slide-2x.png"  # part — NOT
        asset_img.write_text("png")
        pdf = ws / "Projects" / "X" / "assets" / "report.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_text("%PDF")
        ProductRegistry(ws).register_batch([str(deck), str(att), str(asset_img), str(pdf)])
        store = ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"]
        roles = {e["path"].rsplit("/", 1)[-1]: e["role"] for e in products}
        assert roles.get("deck.html") == "Deliverables"
        assert roles.get("report.pdf") == "Deliverables", "deck MAIN products must surface"
        assert "x.png" not in roles, "an Attachments/ image is INPUT — must not register"
        assert "slide-2x.png" not in roles, "a loose image under assets/ is a deck PART — must not register"

    def test_other_role_files_are_not_products(self, ws):
        """The store holds PRODUCTS only — a tracked non-product file (role Other)
        must not be surfaced. Other is the classification catch-all, not a product."""
        from core.product_registry import ProductRegistry

        # A tracked config-ish file under SwarmWS root that maps to Other.
        f = ws / "randomconfig.xyz"
        f.write_text("x")
        ProductRegistry(ws).register_batch([str(f)])
        store = ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        assert not any("randomconfig" in e["path"] for e in products), (
            "role=Other files must not register as products"
        )


# ── AC4: external paths skipped ─────────────────────────────────────────────


class TestAC4ExternalSkipped:
    def test_home_desktop_and_tmp_skipped(self, ws):
        from core.product_registry import ProductRegistry

        reg = ProductRegistry(ws)
        reg.register_batch(
            [
                str(Path.home() / "Desktop" / "report.md"),
                "/tmp/scratch/deck.html",
            ]
        )
        store = ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        assert products == [], "external / tmp paths must be skipped (classify_source copy)"


# ── AC5: backfill idempotent ────────────────────────────────────────────────


class TestAC5BackfillIdempotent:
    def test_backfill_twice_no_dup(self, git_ws):
        from core.product_registry import ProductRegistry

        # A committed design deliverable (tracked → git-log will list it).
        d = git_ws / "Knowledge" / "Designs"
        d.mkdir(parents=True)
        (d / "deck.md").write_text("# deck")
        subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
        subprocess.run(["git", "commit", "-qm", "add deck"], cwd=git_ws, check=True)

        reg = ProductRegistry(git_ws)
        reg.backfill_from_gitlog(days=30)
        first = json.loads((git_ws / ".artifacts" / "products.json").read_text())
        reg.backfill_from_gitlog(days=30)
        second = json.loads((git_ws / ".artifacts" / "products.json").read_text())

        assert len(first["products"]) == len(second["products"]) > 0, (
            "backfill must be idempotent — running twice adds no duplicate"
        )


# ── AC6: sync core off-loop; watcher hook calls register_batch on RAW paths ──


class TestAC6OffLoopSeam:
    async def test_watcher_calls_register_batch_on_raw_paths_off_loop(self, ws, monkeypatch):
        """workspace_surface_watcher._handle_batch must call
        ProductRegistry.register_batch with the RAW batch paths, via the
        existing off-loop executors.run_in seam — NOT off the filtered
        verdicts.items() loop (Gate-1 finding)."""
        import core.workspace_surface_watcher as wsw

        seen = {}

        def fake_register_batch(self, paths):
            seen["paths"] = list(paths)

        monkeypatch.setattr(
            "core.product_registry.ProductRegistry.register_batch",
            fake_register_batch,
        )

        watcher = wsw.WorkspaceSurfaceWatcher(ws)
        raw = [str(ws / "Projects" / "AIDLC" / "assets" / "deck.html")]
        await watcher._handle_batch(raw)

        assert "paths" in seen, "_handle_batch must call register_batch"
        assert seen["paths"] == raw, (
            "register_batch must receive the RAW batch paths (incl. gitignored), "
            "not the review_worthy/content-filtered subset"
        )

    def test_sync_core_is_importable_and_callable(self, ws):
        """The write core must be a plain sync callable (so an async seam can
        run it via executors.run_in without the registry owning any async)."""
        from core.product_registry import ProductRegistry

        reg = ProductRegistry(ws)
        assert callable(reg.register_batch)
        # A no-op batch must not raise and must not create a bogus store entry.
        reg.register_batch([])


# ── Layer 4: Cross-Boundary E2E (cross_boundary=true, kind=event-bus) ────────
# Drives the REAL seam end-to-end: real _handle_batch → real ProductRegistry →
# real products.json. Does NOT mock the thing-under-change (the registry write).
# Only the far leaf (surface_injection.publish_file_event) is neutralized so the
# test doesn't need a live SSE bus.


class TestLayer4CrossBoundaryE2E:
    async def test_watcher_writes_gitignored_deck_to_real_store(self, git_ws, monkeypatch):
        """The full boundary: a gitignored deck flows through the REAL watcher hook
        into the REAL products.json — the seam this run adds, driven end-to-end."""
        import core.workspace_surface_watcher as wsw

        # Neutralize ONLY the far leaf (the SSE emit) — NOT the registry seam.
        monkeypatch.setattr(wsw.surface_injection, "publish_file_event", lambda ev: None)

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "deck.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")

        watcher = wsw.WorkspaceSurfaceWatcher(git_ws)
        await watcher._handle_batch([str(deck)])

        store = git_ws / ".artifacts" / "products.json"
        assert store.is_file(), "real seam must have written the real store"
        products = json.loads(store.read_text())["products"]
        assert any(e["path"].endswith("assets/deck.html") for e in products), (
            "the gitignored deck must reach products.json through the REAL "
            "_handle_batch → ProductRegistry seam (not a mock)"
        )

    async def test_mutation_reverting_the_hook_goes_red(self, git_ws, monkeypatch):
        """Mutation proof (non-vacuous): if the watcher did NOT call the registry
        on the raw batch (the contract line this run added), the store would be
        empty. We simulate the revert by pointing register_batch at a no-op and
        assert the deck is then ABSENT — proving the real hook is what writes it."""
        import core.workspace_surface_watcher as wsw

        monkeypatch.setattr(wsw.surface_injection, "publish_file_event", lambda ev: None)
        # REVERT the contract: register_batch becomes a no-op (as if the hook line
        # were removed). The store must then NOT contain the deck.
        monkeypatch.setattr(
            "core.product_registry.ProductRegistry.register_batch",
            lambda self, paths: None,
        )

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "deck.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")

        watcher = wsw.WorkspaceSurfaceWatcher(git_ws)
        await watcher._handle_batch([str(deck)])

        store = git_ws / ".artifacts" / "products.json"
        products = json.loads(store.read_text())["products"] if store.is_file() else []
        assert not any(e["path"].endswith("assets/deck.html") for e in products), (
            "with the hook reverted, the deck must NOT reach the store — "
            "proves the Layer-4 test is non-vacuous (green only because the real hook fires)"
        )


# ── Run 2 AC3: Role is a FROZEN enum, not a bare str alias ───────────────────


class TestRun2RoleEnum:
    def test_role_is_frozen_enum_not_bare_str(self):
        """Role must be a closed set (Enum or Literal), not `Role = str`. An
        invalid role string must not be a valid Role — the type is the guard."""
        import core.product_registry as pr

        # Role must NOT be the bare `str` builtin (the pre-Run-2 alias).
        assert pr.Role is not str, "Role must be a frozen enum/Literal, not `Role = str`"

    def test_derive_role_returns_only_known_roles(self):
        """Every derive_role output is one of the closed role set — no free-form
        strings leak out."""
        from core.product_registry import derive_role

        known = {"Deliverables", "Knowledge", "Pipeline", "Activity", "Other"}
        samples = [
            ("Knowledge/Designs/x.html", None),
            ("Projects/SwarmAI/2-understanding/TECH.md", None),
            ("Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md", None),
            ("Knowledge/DailyActivity/2026-09-30.md", None),
            ("randomconfig.xyz", None),
        ]
        for rel, repo in samples:
            r = derive_role(rel, repo)
            assert str(r) in known or getattr(r, "value", r) in known, (
                f"derive_role({rel!r}) returned {r!r} — not in the closed role set"
            )


# ── Run 2 AC5: git check-ignore is BATCHED (one subprocess per owning tree) ──


class TestRun2BatchCheckIgnore:
    def test_batch_runs_one_check_ignore_per_tree(self, git_ws, monkeypatch):
        """register_batch of N paths in ONE owning tree must run git check-ignore
        at most ONCE (batched via --stdin), not once per path."""
        import core.product_registry as pr

        calls = {"check_ignore": 0}
        real_run = subprocess.run

        def counting_run(cmd, *a, **kw):
            if isinstance(cmd, (list, tuple)) and "check-ignore" in cmd:
                calls["check_ignore"] += 1
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(pr.subprocess, "run", counting_run)

        # 4 product files (real product extensions) in the ONE git_ws tree.
        paths = []
        for i in range(4):
            p = git_ws / "Knowledge" / "Designs" / f"d{i}.html"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("<html>")
            paths.append(str(p))

        pr.ProductRegistry(git_ws).register_batch(paths)

        assert calls["check_ignore"] <= 1, (
            f"check-ignore ran {calls['check_ignore']}× for 4 paths in one tree — "
            "must be batched (one --stdin subprocess per tree), not per-path"
        )

    def test_batch_cjk_gitignored_path_matches(self, git_ws):
        """Gate-2 Correctness HIGH: a CJK-named gitignored deck under assets/ must be
        recorded gitignored=True. WITHOUT `-c core.quotepath=false`, git C-quotes the
        non-ASCII path in --verbose output and the raw rel_path key never matches →
        gitignored=False (wrong). This locks the quotepath fix."""
        from core.product_registry import ProductRegistry

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "中概互联ETF.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")
        ProductRegistry(git_ws).register_batch([str(deck)])
        products = json.loads((git_ws / ".artifacts" / "products.json").read_text())["products"]
        cjk = [e for e in products if e["path"].endswith("ETF.html")]
        assert len(cjk) == 1, "the CJK-named gitignored deck must surface"
        assert cjk[0]["gitignored"] is True, (
            "a gitignored CJK path must be recorded gitignored=True — the check-ignore "
            "match must survive non-ASCII (core.quotepath=false)"
        )

    def test_tracked_product_matching_ignore_pattern_not_dropped(self, git_ws, monkeypatch):
        """Gate-2 Red-Team: check-ignore must be INDEX-AWARE (no --no-index). A TRACKED
        Designs product that matches a broad ignore pattern must NOT be marked gitignored
        and must NOT be dropped by GUARD-2 — a committed file overrides .gitignore. With
        --no-index it would false-report ignored and drop a real committed product."""
        import subprocess as _sp
        # Add a broad ignore rule that WOULD match a tracked Designs product, then TRACK it.
        gi = git_ws / ".gitignore"
        gi.write_text(gi.read_text() + "\n*.html\n")
        spec = git_ws / "Knowledge" / "Designs" / "spec.html"
        spec.parent.mkdir(parents=True)
        spec.write_text("<html>")
        _sp.run(["git", "add", "-f", "Knowledge/Designs/spec.html", ".gitignore"], cwd=git_ws, check=True)
        _sp.run(["git", "commit", "-qm", "track spec despite *.html"], cwd=git_ws, check=True)

        from core.product_registry import ProductRegistry
        ProductRegistry(git_ws).register_batch([str(spec)])
        products = json.loads((git_ws / ".artifacts" / "products.json").read_text())["products"]
        by_base = {e["path"].rsplit("/", 1)[-1]: e for e in products}
        assert "spec.html" in by_base, (
            "a TRACKED Designs product matching an ignore pattern must NOT be dropped — "
            "check-ignore must be index-aware (no --no-index)"
        )
        assert by_base["spec.html"]["gitignored"] is False, (
            "a committed file overrides .gitignore → gitignored must be False"
        )

    def test_batch_gitignored_deck_still_surfaces(self, git_ws):
        """The batch path must preserve AC3: a gitignored deck under assets/ still
        surfaces (batching must not regress the gitignore→product decision)."""
        from core.product_registry import ProductRegistry

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "deck.html"
        deck.parent.mkdir(parents=True)
        deck.write_text("<html>")
        tracked = git_ws / "Knowledge" / "Designs" / "spec.html"
        tracked.parent.mkdir(parents=True)
        tracked.write_text("<html>")

        ProductRegistry(git_ws).register_batch([str(deck), str(tracked)])
        products = json.loads((git_ws / ".artifacts" / "products.json").read_text())["products"]
        by_base = {e["path"].rsplit("/", 1)[-1]: e for e in products}
        assert "deck.html" in by_base, "gitignored deck must still surface after batching"
        assert by_base["deck.html"]["gitignored"] is True, "deck must be marked gitignored"
        assert "spec.html" in by_base, "tracked product must also surface"
        assert by_base["spec.html"]["gitignored"] is False


# ── Run 2 AC4: products.json size cap + oldest-first eviction ────────────────


class TestRun2CapEviction:
    def test_store_capped_and_evicts_oldest(self, ws, monkeypatch):
        """Registering past MAX_PRODUCTS must cap the store and evict the OLDEST
        by last_touched, never grow unbounded."""
        import core.product_registry as pr

        # Shrink the cap for a fast, deterministic test.
        monkeypatch.setattr(pr, "MAX_PRODUCTS", 5, raising=False)

        reg = pr.ProductRegistry(ws)
        # Register MAX+3 distinct product files, each a real Deliverable.
        made = []
        for i in range(8):
            p = ws / "Knowledge" / "Designs" / f"deck{i}.html"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("<html>")
            reg.register_batch([str(p)])  # one at a time → distinct last_touched order
            made.append(f"deck{i}.html")

        products = json.loads((ws / ".artifacts" / "products.json").read_text())["products"]
        assert len(products) <= 5, f"store must be capped at MAX_PRODUCTS, got {len(products)}"
        bases = {e["path"].rsplit("/", 1)[-1] for e in products}
        # The 3 oldest (deck0/1/2) must have been evicted; the newest survive.
        assert "deck7.html" in bases, "newest product must survive"
        assert "deck0.html" not in bases, "oldest product must be evicted"

    def test_cap_default_is_generous(self):
        """The default cap must exist and be a sane bound (not tiny, not unbounded)."""
        import core.product_registry as pr

        assert hasattr(pr, "MAX_PRODUCTS"), "a MAX_PRODUCTS cap constant must exist"
        assert 100 <= pr.MAX_PRODUCTS <= 10000, "cap should be a generous but finite bound"


# ── Run-3: data-layer root-fix (AC1 real time / AC2 garbage / AC3 Library / AC4 fs-scan) ──


class TestRun3GarbageFilter:
    """AC2: backup/temp files never register as products (component-aware)."""

    @pytest.mark.parametrize(
        "rel",
        [
            "Knowledge/Library/deck.html.bak-1788437117",  # trailing epoch-bak
            "Knowledge/Library/deck.bak.html",             # double-ext: .bak mid-name
            "Knowledge/Library/deck.html.broken-hostclose",
            "Knowledge/Library/notes.md.tmp",
            "Knowledge/Library/data.corrupt-20260930",
            "Knowledge/Library/draft.html~",               # trailing ~
        ],
    )
    def test_garbage_suffix_never_registers(self, ws, rel):
        from core.product_registry import ProductRegistry

        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        ProductRegistry(ws).register_batch([str(p)])
        products = ProductRegistry(ws).list_products()
        bases = {e.path for e in products}
        assert rel not in bases, f"garbage file must NOT register: {rel}"

    def test_real_deck_next_to_garbage_still_registers(self, ws):
        from core.product_registry import ProductRegistry

        d = ws / "Knowledge" / "Library"
        d.mkdir(parents=True)
        good = d / "2026-08-30-ai-native-deck.html"
        bad = d / "2026-08-30-ai-native-deck.html.bak-1788437117"
        good.write_text("x")
        bad.write_text("x")
        ProductRegistry(ws).register_batch([str(good), str(bad)])
        bases = {e.path for e in ProductRegistry(ws).list_products()}
        assert "Knowledge/Library/2026-08-30-ai-native-deck.html" in bases
        assert "Knowledge/Library/2026-08-30-ai-native-deck.html.bak-1788437117" not in bases


class TestRun3LibraryDeliverable:
    """AC3: Library/Pollinate decks classify as Deliverables, not Knowledge."""

    @pytest.mark.parametrize(
        "rel",
        [
            "Knowledge/Library/2026-08-30-ai-native-ee-oe-deck.html",
            "Knowledge/Library/2026-08-30-ai-native-ee-oe-deck-en.pdf",
            "Knowledge/Pollinate/2026-07-13-ai-native-transformation-deck/index.html",
        ],
    )
    def test_library_pollinate_deck_is_deliverable(self, ws, rel):
        from core.product_registry import ProductRegistry, Role

        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        ProductRegistry(ws).register_batch([str(p)])
        products = ProductRegistry(ws).list_products()
        match = [e for e in products if e.path == rel]
        assert match, f"deck must register: {rel}"
        assert match[0].role == Role.DELIVERABLES.value, f"{rel} must be Deliverables"


class TestRun3RealTimestamp:
    """AC1: backfill sets time from git commit date (fallback mtime), not now()."""

    def test_backfill_uses_git_commit_date_not_now(self, git_ws):
        import subprocess
        from datetime import datetime, timezone
        from core.product_registry import ProductRegistry

        # Commit a deliverable at a KNOWN back-date via GIT_*_DATE env.
        deck = git_ws / "Knowledge" / "Library" / "old-deck.html"
        deck.parent.mkdir(parents=True, exist_ok=True)
        deck.write_text("x")
        old_date = "2026-06-04T01:13:16"
        env = {
            "GIT_AUTHOR_DATE": f"{old_date} +0000",
            "GIT_COMMITTER_DATE": f"{old_date} +0000",
        }
        import os
        full_env = {**os.environ, **env}
        subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "old deck"],
            cwd=git_ws, check=True, env=full_env,
        )
        ProductRegistry(git_ws).backfill_from_gitlog(days=3650)
        products = ProductRegistry(git_ws).list_products()
        match = [e for e in products if e.path == "Knowledge/Library/old-deck.html"]
        assert match, "backfilled deck must be present"
        # Its last_touched must reflect the 2026-06 commit date, NOT the backfill instant.
        assert match[0].last_touched.startswith("2026-06-04"), (
            f"timestamp must be the commit date, got {match[0].last_touched}"
        )
        assert match[0].first_produced.startswith("2026-06-04"), (
            f"first_produced must be the commit date, got {match[0].first_produced}"
        )


class TestRun3FsScanGitignoredDecks:
    """AC4: fs-scan surfaces gitignored product-dir decks, extension-gated (no secrets)."""

    def test_gitignored_deck_scanned_and_surfaced(self, git_ws):
        from core.product_registry import ProductRegistry

        # A gitignored AIDLC deck (Projects/* is gitignored) — git-log never lists it.
        deck = git_ws / "Projects" / "AIDLC" / "assets" / "AIDLC-Deck-EN.pdf"
        deck.parent.mkdir(parents=True, exist_ok=True)
        deck.write_text("%PDF-x")
        ProductRegistry(git_ws).scan_product_dirs()
        bases = {e.path for e in ProductRegistry(git_ws).list_products()}
        assert "Projects/AIDLC/assets/AIDLC-Deck-EN.pdf" in bases, (
            "gitignored deck under a product dir must be fs-scanned in"
        )

    def test_secret_in_product_dir_not_scanned(self, git_ws):
        from core.product_registry import ProductRegistry

        # A secret with a NON-product extension under a scanned dir must never surface.
        env = git_ws / "Projects" / "AIDLC" / "assets" / "secret.env"
        key = git_ws / "Projects" / "AIDLC" / "assets" / "id_rsa"
        csv = git_ws / "Projects" / "AIDLC" / "assets" / "secrets_export.csv"
        env.parent.mkdir(parents=True, exist_ok=True)
        for f in (env, key, csv):
            f.write_text("SECRET")
        ProductRegistry(git_ws).scan_product_dirs()
        bases = {e.path for e in ProductRegistry(git_ws).list_products()}
        assert not any("secret" in b or "id_rsa" in b for b in bases), (
            f"no secret may be fs-scanned into products; got {bases}"
        )

    def test_scanned_files_carry_correct_gitignored_flag(self, git_ws):
        from core.product_registry import ProductRegistry

        deck = git_ws / "Projects" / "AIDLC" / "assets" / "deck.pdf"
        deck.parent.mkdir(parents=True, exist_ok=True)
        deck.write_text("%PDF-x")
        ProductRegistry(git_ws).scan_product_dirs()
        match = [e for e in ProductRegistry(git_ws).list_products()
                 if e.path == "Projects/AIDLC/assets/deck.pdf"]
        assert match, "deck must be scanned in"
        assert match[0].gitignored is True, "scanned gitignored deck must carry gitignored=True"


class TestRun3Gate2SecurityHardening:
    """Gate-2 HIGH: document-formatted secrets under scanned gitignored dirs."""

    @pytest.mark.parametrize(
        "name",
        [
            "aws_credentials_export.pdf",
            "session_tokens.html",
            "internal-secret-deck.html",
            "id_rsa.pdf",
            "my_password_list.docx",
            "cluster.pem.pdf",
            "prod.env.html",
        ],
    )
    def test_secret_named_product_extension_not_scanned(self, git_ws, name):
        from core.product_registry import ProductRegistry

        f = git_ws / "Projects" / "AIDLC" / "assets" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("SECRET")
        ProductRegistry(git_ws).scan_product_dirs()
        bases = {e.path.rsplit("/", 1)[-1] for e in ProductRegistry(git_ws).list_products()}
        assert name not in bases, f"secret-named product-ext file must NOT be scanned in: {name}"

    def test_legit_deck_still_scanned_alongside_secret(self, git_ws):
        from core.product_registry import ProductRegistry

        d = git_ws / "Projects" / "AIDLC" / "assets"
        d.mkdir(parents=True)
        (d / "AIDLC-Deck-EN.pdf").write_text("%PDF")
        (d / "aws_credentials.pdf").write_text("SECRET")
        ProductRegistry(git_ws).scan_product_dirs()
        bases = {e.path.rsplit("/", 1)[-1] for e in ProductRegistry(git_ws).list_products()}
        assert "AIDLC-Deck-EN.pdf" in bases, "the legit deck must still surface"
        assert "aws_credentials.pdf" not in bases, "the secret must not"


class TestRun3Gate2CommitFilenameParse:
    """Gate-2 Correctness F1: a file named 'COMMIT ...' must not poison the git parse."""

    def test_commit_prefixed_filename_does_not_poison_timestamps(self, git_ws):
        import subprocess, os
        from core.product_registry import ProductRegistry

        # Two decks committed together; one basename literally starts with "COMMIT ".
        d = git_ws / "Knowledge" / "Library"
        d.mkdir(parents=True)
        good = d / "real-deck.html"
        trap = d / "COMMIT summary.html"
        good.write_text("x")
        trap.write_text("x")
        old = "2026-06-04T01:13:16"
        env = {**os.environ,
               "GIT_AUTHOR_DATE": f"{old} +0000", "GIT_COMMITTER_DATE": f"{old} +0000"}
        subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "decks"], cwd=git_ws, check=True, env=env)
        ProductRegistry(git_ws).backfill_from_gitlog(days=3650)
        prods = {e.path: e for e in ProductRegistry(git_ws).list_products()}
        # The real deck must carry the REAL commit date, not a poisoned filename-string.
        real = prods.get("Knowledge/Library/real-deck.html")
        assert real, "real deck must be backfilled"
        assert real.last_touched.startswith("2026-06-04"), (
            f"a COMMIT-prefixed sibling filename must not poison the timestamp; "
            f"got {real['last_touched']}"
        )


class TestRun3Gate2VersionedBackfill:
    """Gate-2 meta Finding 1: a version bump re-runs backfill to correct stale data."""

    def test_stale_v1_store_re_backfills_and_corrects_timestamp(self, git_ws):
        import subprocess, os, json as _json
        from core.product_registry import ProductRegistry, _BACKFILL_VERSION

        # Commit a deck at a known OLD date.
        deck = git_ws / "Knowledge" / "Library" / "old-deck.html"
        deck.parent.mkdir(parents=True, exist_ok=True)
        deck.write_text("x")
        old = "2026-06-04T01:13:16"
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{old} +0000", "GIT_COMMITTER_DATE": f"{old} +0000"}
        subprocess.run(["git", "add", "-A"], cwd=git_ws, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "deck"], cwd=git_ws, check=True, env=env)

        reg = ProductRegistry(git_ws)
        # Simulate a v1 store: a row stamped with a WRONG (fetch-instant) time + an
        # old backfill marker with NO version (the pre-Run-3 shape).
        store = git_ws / ".artifacts" / "products.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(_json.dumps({
            "version": 1,
            "backfilled_at": "2026-09-30T08:19:58+00:00",  # v1 marker, no backfill_version
            "products": [{
                "path": "Knowledge/Library/old-deck.html", "role": "Deliverables",
                "kind": "content", "gitignored": False,
                "first_produced": "2026-09-30T08:19:58+00:00",  # WRONG (fetch instant)
                "last_touched": "2026-09-30T08:19:58+00:00",
            }],
        }))
        # has_backfilled must be FALSE for a v1 store under the new version.
        assert reg.has_backfilled() is False, "a v1-backfilled store must re-qualify for backfill"
        # Re-run backfill → the stale timestamp must be CORRECTED to the real commit date.
        reg.backfill_from_gitlog(days=3650)
        row = [e for e in ProductRegistry(git_ws).list_products()
               if e.path == "Knowledge/Library/old-deck.html"][0]
        assert row.last_touched.startswith("2026-06-04"), (
            f"re-backfill must correct last_touched to the real commit date, got {row.last_touched}")
        assert row.first_produced.startswith("2026-06-04"), (
            f"re-backfill must correct first_produced too, got {row.first_produced}")
        # And the marker must now carry the current version → no perpetual re-run.
        assert ProductRegistry(git_ws).has_backfilled() is True, "post-re-backfill must be marked done"
        data = _json.loads(store.read_text())
        assert data["backfill_version"] == _BACKFILL_VERSION


# -- run_fe228bc0: noise-cleanup exclusions (dot-folder / assets-image / Attachments) --


class TestNoiseCleanupExclusions:
    """The registry surfaces PRODUCTS only. Three exclusion classes must drop at the
    shared classification seam (derive_role -> Other, and _classify -> None):
    (1) any dot-folder segment (.claude/.git/any .xxx), (2) an image nested under an
    assets/ dir (deck intermediate part), (3) the entire Attachments/ tree (input).
    A deck MAIN product (.html/.pdf/.pptx/.md) under assets/ is KEPT.
    """

    @pytest.mark.parametrize(
        "rel_path",
        [
            ".claude/skills/s_pollinate/brand/assets/logo/qr-github.png",
            "Projects/AIDLC/.git/assets/x.png",
            "Knowledge/.hidden/report.html",
            "Projects/AIDLC/assets/2026-07-28-slides/slide-rocky-2x.png",
            "Projects/AIDLC/assets/slide2.svg",
            "Knowledge/Notes/assets/cntech-landscape.jpg",
            "Attachments/2026-09-30/screenshot.png",
            "Attachments/2026-06-29/x.pdf",
            "attachments/2026-09-30/lowercase.png",  # case-insensitive (Gate-2 LOW)
        ],
    )
    def test_excluded_paths_derive_to_other(self, rel_path):
        from core.product_registry import derive_role

        assert derive_role(rel_path, None) == "Other", f"{rel_path} should be excluded"

    @pytest.mark.parametrize(
        "rel_path,expected",
        [
            ("Projects/AIDLC/assets/2026-08-30-ai-native-deck.html", "Deliverables"),
            ("Projects/AIDLC/assets/deck.pdf", "Deliverables"),
            ("Projects/AIDLC/assets/slides.pptx", "Deliverables"),
            ("Knowledge/Designs/2026-09-30-x.md", "Deliverables"),
        ],
    )
    def test_deck_main_products_kept(self, rel_path, expected):
        from core.product_registry import derive_role

        assert derive_role(rel_path, None) == expected, f"{rel_path} should be kept"


class TestPurgeExcludedOnRebackfill:
    """run_fe228bc0 Gate-2 HIGH: register_batch never REMOVES, so a version-bump
    re-backfill must PURGE stored rows that a new exclusion rule now rejects — else
    the pre-existing noise (assets images / .claude / Attachments) survives forever
    and the read path returns it verbatim. _purge_excluded reconciles the store.
    """

    def _seed_store(self, ws, rows):
        import json as _json
        store = ws / ".artifacts"
        store.mkdir(parents=True, exist_ok=True)
        (store / "products.json").write_text(_json.dumps({
            "version": 1,
            "products": [
                {"path": p, "role": "Deliverables", "kind": "content",
                 "gitignored": False, "first_produced": "2026-01-01T00:00:00+00:00",
                 "last_touched": "2026-01-01T00:00:00+00:00"} for p in rows
            ],
        }))

    def test_purge_removes_now_excluded_rows_keeps_real(self, ws):
        import json as _json
        from core.product_registry import ProductRegistry

        self._seed_store(ws, [
            "Attachments/2026-09-30/x.png",              # excluded (input)
            "Projects/AIDLC/assets/slide-2x.png",        # excluded (assets image part)
            ".claude/skills/s/brand/logo.png",           # excluded (dot-folder)
            "Knowledge/Library/real-deck.html",          # KEPT (real deliverable)
            "Projects/AIDLC/assets/deck.pdf",            # KEPT (deck main product)
        ])
        removed = ProductRegistry(ws).            _purge_excluded()  # noqa: E501
        assert removed == 3, f"must purge exactly the 3 noise rows, removed {removed}"
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        paths = {e["path"] for e in data["products"]}
        assert paths == {"Knowledge/Library/real-deck.html", "Projects/AIDLC/assets/deck.pdf"}

    def test_purge_is_idempotent_noop_on_clean_store(self, ws):
        from core.product_registry import ProductRegistry

        self._seed_store(ws, ["Knowledge/Library/real-deck.html"])
        assert ProductRegistry(ws)._purge_excluded() == 0
        assert ProductRegistry(ws)._purge_excluded() == 0

    def test_purge_KEEPS_pipeline_report_run_cb127db1_regression(self, ws):
        """run_cb127db1: _purge_excluded called _is_excluded_path DIRECTLY, which
        returns True for any .artifacts/ dot-folder path — deleting every Pipeline
        REPORT even though derive_role classifies it Role.PIPELINE (kept). Two doors
        disagreed. The fix routes purge through derive_role (single door, P8) so a
        REPORT survives. Mutation-proof: reverting to _is_excluded_path makes this RED.
        """
        import json as _json
        from core.product_registry import ProductRegistry

        self._seed_store(ws, [
            "Projects/SwarmAI/.artifacts/runs/run_abc123/REPORT.md",  # KEPT: Role.PIPELINE
            "Projects/AIDLC/assets/slide-2x.png",                     # excluded (assets image)
            "Knowledge/Library/real-deck.html",                       # KEPT (real deliverable)
        ])
        removed = ProductRegistry(ws)._purge_excluded()
        assert removed == 1, f"must purge ONLY the assets image, removed {removed}"
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        paths = {e["path"] for e in data["products"]}
        assert "Projects/SwarmAI/.artifacts/runs/run_abc123/REPORT.md" in paths, \
            "a Pipeline REPORT must NOT be purged (regression)"
        assert "Projects/AIDLC/assets/slide-2x.png" not in paths

    def test_purge_RE_DERIVES_not_trusts_stale_stored_role(self, ws):
        """The purge must RE-derive each row via derive_role, NOT trust the stored role.

        DISCRIMINATES the single-door fix (run_cb127db1): _seed_store writes role
        'Deliverables' for EVERY row. A REPORT under .artifacts/runs/ carries that stale
        Deliverables role, yet derive_role short-circuits it to PIPELINE → it must SURVIVE;
        meanwhile the OLD predicate (`_is_excluded_path`) would have PURGED that same REPORT
        (dot-folder), so this case goes RED under the reverted fix — it is mutation-sound for
        the single-door change (not merely duplicating the assets/Attachments drop, which
        BOTH predicates handle identically). Also asserts a stale-role assets image is still
        dropped (re-derive beats stored role).
        """
        import json as _json
        from core.product_registry import ProductRegistry

        self._seed_store(ws, [
            "Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md",  # stale role=Deliverables; derive_role→PIPELINE → KEPT (old predicate would PURGE it)
            "Projects/AIDLC/assets/preview-1.jpg",               # stale role=Deliverables; derives OTHER → dropped
            "Attachments/2026-01-01/shot.png",                   # stale role=Deliverables; derives OTHER → dropped
            "Knowledge/Designs/mock.html",                       # genuinely a Deliverable → kept
        ])
        removed = ProductRegistry(ws)._purge_excluded()
        assert removed == 2, f"re-derive must drop 2 stale-role noise rows (keep REPORT + real deck), removed {removed}"
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        paths = {e["path"] for e in data["products"]}
        assert paths == {
            "Projects/SwarmAI/.artifacts/runs/run_x/REPORT.md",  # re-derive keeps it despite stale role
            "Knowledge/Designs/mock.html",
        }


class TestStarred:
    """run_2b7230be: the `starred` favorite field + set_starred toggle.

    Persistence lives in products.json (skeptic-rejected a sidecar). The field
    carries a `= False` default so Product(**legacy_row) never crashes; set_starred
    toggles the boolean on an EXISTING matched row via the flock-guarded _mutate
    (no append on an unknown path); starred survives a re-register; and a starred
    row is NEVER evicted by the MAX_PRODUCTS cap (a favorite is permanent).
    """

    def _seed(self, ws, rows):
        """rows: list of (path, starred). Writes a store directly."""
        import json as _json
        store = ws / ".artifacts"
        store.mkdir(parents=True, exist_ok=True)
        (store / "products.json").write_text(_json.dumps({
            "version": 1,
            "products": [
                {"path": p, "role": "Deliverables", "kind": "content",
                 "gitignored": False, "first_produced": "2026-01-01T00:00:00+00:00",
                 "last_touched": "2026-01-01T00:00:00+00:00",
                 **({"starred": s} if s is not None else {})}
                for p, s in rows
            ],
        }))

    def test_legacy_row_without_starred_key_loads(self, ws):
        """AC1: a LEGACY row (no starred key) must load — Product(**e) with the
        `= False` default supplies it, never a TypeError."""
        from core.product_registry import ProductRegistry
        self._seed(ws, [("Knowledge/Designs/x.html", None)])  # None => no starred key
        products = ProductRegistry(ws).list_products()
        assert len(products) == 1
        assert products[0].starred is False  # default applied, no crash

    def test_set_starred_matches_existing_path(self, ws):
        """AC2: set_starred flips the boolean on a matched path, returns True, persists."""
        import json as _json
        from core.product_registry import ProductRegistry
        self._seed(ws, [("Knowledge/Designs/x.html", False)])
        reg = ProductRegistry(ws)
        assert reg.set_starred("Knowledge/Designs/x.html", True) is True
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        assert data["products"][0]["starred"] is True
        # toggle back off
        assert reg.set_starred("Knowledge/Designs/x.html", False) is True
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        assert data["products"][0]["starred"] is False

    def test_set_starred_unknown_path_is_noop_never_appends(self, ws):
        """AC2: an unknown path returns False and does NOT append a row (no create)."""
        import json as _json
        from core.product_registry import ProductRegistry
        self._seed(ws, [("Knowledge/Designs/x.html", False)])
        reg = ProductRegistry(ws)
        assert reg.set_starred("Projects/evil/../../etc/passwd", True) is False
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        assert len(data["products"]) == 1  # no append
        assert data["products"][0]["path"] == "Knowledge/Designs/x.html"

    def test_starred_survives_reregister(self, ws):
        """AC3: a live re-touch (register_batch) of a starred row keeps starred=True."""
        import json as _json
        from core.product_registry import ProductRegistry
        (ws / "Knowledge" / "Designs").mkdir(parents=True, exist_ok=True)
        f = ws / "Knowledge" / "Designs" / "deck.html"
        f.write_text("<html></html>")
        reg = ProductRegistry(ws)
        reg.register_batch([str(f)])
        assert reg.set_starred("Knowledge/Designs/deck.html", True) is True
        # re-touch the same path (live watcher path — no historical ts)
        reg.register_batch([str(f)])
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        row = next(e for e in data["products"] if e["path"] == "Knowledge/Designs/deck.html")
        assert row["starred"] is True, "a re-register must preserve the star (favorite is durable)"

    def test_starred_row_is_never_purged(self, ws):
        """run_2b7230be meta-review: _purge_excluded must NOT drop a starred row even
        when its path now classifies to OTHER (noise) — a favorite is permanent, the
        same invariant eviction honors (P8: the two row-dropping doors agree). A user
        can unstar genuine noise; the system never revokes a deliberate favorite."""
        import json as _json
        from core.product_registry import ProductRegistry
        # an assets image (derive_role → OTHER, normally purged) but STARRED
        self._seed(ws, [
            ("Projects/AIDLC/assets/slide-2x.png", True),   # noise-by-path BUT starred → kept
            ("Projects/AIDLC/assets/other.png", False),     # noise-by-path, unstarred → purged
            ("Knowledge/Designs/real.html", False),         # real product → kept
        ])
        removed = ProductRegistry(ws)._purge_excluded()
        assert removed == 1, f"only the unstarred noise row is purged, removed {removed}"
        data = _json.loads((ws / ".artifacts" / "products.json").read_text())
        paths = {e["path"] for e in data["products"]}
        assert "Projects/AIDLC/assets/slide-2x.png" in paths, "a starred row must survive purge"
        assert "Projects/AIDLC/assets/other.png" not in paths
        assert "Knowledge/Designs/real.html" in paths

    def test_starred_row_is_never_evicted(self, ws):
        """AC7 (Gate-1 finding): the MAX_PRODUCTS cap evicts oldest-by-last_touched,
        but a STARRED row must survive even if it is the oldest/untouched — a favorite
        is permanent. Seed a starred OLD row + fill past the cap with newer rows;
        the starred row must remain."""
        import json as _json
        import core.product_registry as pr
        from core.product_registry import ProductRegistry
        # shrink the cap for the test
        old_cap = pr.MAX_PRODUCTS
        pr.MAX_PRODUCTS = 5
        try:
            store = ws / ".artifacts"
            store.mkdir(parents=True, exist_ok=True)
            rows = [{"path": "Knowledge/fav.html", "role": "Deliverables", "kind": "content",
                     "gitignored": False, "first_produced": "2020-01-01T00:00:00+00:00",
                     "last_touched": "2020-01-01T00:00:00+00:00", "starred": True}]  # OLDEST + starred
            (store / "products.json").write_text(_json.dumps({"version": 1, "products": rows}))
            # register 10 fresh files (all newer) → forces eviction past cap=5
            (ws / "Knowledge" / "Designs").mkdir(parents=True, exist_ok=True)
            fresh = []
            for i in range(10):
                f = ws / "Knowledge" / "Designs" / f"n{i}.html"
                f.write_text("x")
                fresh.append(str(f))
            ProductRegistry(ws).register_batch(fresh)
            data = _json.loads((store / "products.json").read_text())
            paths = {e["path"] for e in data["products"]}
            assert "Knowledge/fav.html" in paths, "a starred row must NEVER be evicted, even as oldest"
        finally:
            pr.MAX_PRODUCTS = old_cap



class TestOneRuleSwarmWSOnly:
    """design 2026-10-26-artifacts-one-rule.md: a product is a SwarmWS file; a bound
    repo's file is never one, whatever its name; a gitignored file is one only under a
    product dir (is_ignored_product, no git call)."""

    def test_a_bound_repo_file_is_not_a_product(self, ws, tmp_path, monkeypatch):
        from core import product_registry as pr
        repo = tmp_path / "repo"
        f = repo / "Knowledge" / "Designs" / "d.md"
        f.parent.mkdir(parents=True); f.write_text("x")
        monkeypatch.setattr(pr, "_owning_tree",
                            lambda p, root: (repo, str(Path(p).relative_to(repo)), "repo")
                            if str(p).startswith(str(repo)) else None)
        reg = pr.ProductRegistry(ws)
        assert reg.lookup([str(f)])[str(f)]["product"] is False
        reg.register_batch([str(f)])
        assert reg.list_products() == []

    @pytest.mark.parametrize("rel,want", [
        ("Projects/X/assets/deck.html", True),
        ("Knowledge/Library/report.pdf", True),
        ("Projects/X/notes.md", False),
        (".context/x.md", False),
        ("Projects/X/assets/.env", False),
    ])
    def test_is_ignored_product(self, ws, rel, want):
        from core.product_registry import ProductRegistry
        assert ProductRegistry(ws).is_ignored_product(str(ws / rel)) is want

    def test_is_ignored_product_outside_swarmws_is_false(self, ws, tmp_path):
        from core.product_registry import ProductRegistry
        assert ProductRegistry(ws).is_ignored_product(str(tmp_path.parent / "elsewhere-not-ws" / "assets" / "d.html")) is False
