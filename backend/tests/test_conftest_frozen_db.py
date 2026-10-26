"""No module keeps a mocked ``database.db`` after the test that mocked it.

What is tested: a module first imported while a test has ``patch("database.db")``
active binds the MagicMock with ``from database import db`` and keeps it after the
patch exits (core.session_manager did, after test_sse_disconnect_chain; every later
test that stored a session then failed: test_steer_send's 21 SESSION_BUSY). The
conftest sweep puts the real ``database.db`` back after each test. The two tests run
in file order: the first leaves a module holding a mock, the second sees it repaired.
"""
import sys
import types
from unittest.mock import MagicMock

PROBE = "core._zz_frozen_db_probe"


def test_a_module_imported_under_a_db_patch_keeps_the_mock():
    mod = types.ModuleType(PROBE)
    mod.db = MagicMock(name="db")          # what `from database import db` binds under a patch
    sys.modules[PROBE] = mod


def test_the_next_test_sees_the_real_db():
    import database
    try:
        assert sys.modules[PROBE].db is database.db
    finally:
        sys.modules.pop(PROBE, None)
