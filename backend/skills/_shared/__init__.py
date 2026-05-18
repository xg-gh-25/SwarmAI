"""Shared utilities for CMHK skill scripts.

This package provides common functions that all skill generators need,
avoiding hardcoded project names scattered across 6+ files.

Import pattern (from any skill script):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
    from project_paths import get_output_dir, CMHK_PROJECT
"""
