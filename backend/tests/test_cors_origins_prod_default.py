"""Security test for CORS dev-origin gating (TT V2265734761, fix 4).

The production default CORS origin list used to include the dev-only browser
origins http://localhost:5173 (Vite) and http://localhost:3000 (CRA). A page
served on those origins could issue cross-origin PUT/POST to the daemon. These
tests pin that:
  - the production default (Settings.cors_origins) excludes the dev origins,
    while keeping the Tauri origins + localhost:1420 (the Tauri devUrl);
  - the main.py debug block re-adds the dev origins ONLY when settings.debug.
"""

from config import Settings


DEV_ONLY_ORIGINS = {"http://localhost:5173", "http://localhost:3000"}


def _class_default_origins():
    """The config.py CLASS-DEFAULT cors_origins, bypassing any local .env.

    A developer's gitignored backend/.env can set CORS_ORIGINS and override the
    class default; the packaged PRODUCTION daemon ships NO .env, so it uses the
    class default. These tests pin the class default (the value production runs),
    so pass ``_env_file=None`` to ignore a local .env.
    """
    return set(Settings(_env_file=None).cors_origins)


def test_production_default_excludes_dev_origins():
    """A default (non-debug) Settings must not ship the dev browser origins."""
    origins = _class_default_origins()
    assert not (origins & DEV_ONLY_ORIGINS), (
        f"production CORS default must exclude dev origins, found: "
        f"{origins & DEV_ONLY_ORIGINS}"
    )


def test_production_default_keeps_tauri_and_devurl():
    """The Tauri origins + the Tauri devUrl (1420) must remain — desktop needs them."""
    origins = _class_default_origins()
    assert "tauri://localhost" in origins
    assert "http://localhost:1420" in origins

# NOTE: a "debug block re-adds dev origins" test was intentionally NOT added here.
# main.py adds the dev origins under `if settings.debug:` at module-import time,
# which cannot be re-triggered in-process without re-importing main. A test that
# re-implements that set-union locally would be vacuous (RP47 test-theater — it
# would assert a constant it just constructed, staying green even if main.py's
# block were deleted). The production contract that matters — the class default
# excludes the dev origins — is covered by test_production_default_excludes_dev_origins
# against real config code. (Adversarial correctness specialist, run_8b1014af.)
