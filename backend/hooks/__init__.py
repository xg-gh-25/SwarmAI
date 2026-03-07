"""Session lifecycle hooks for post-session-close behaviors.

This package contains hook implementations that register with the
``SessionLifecycleHookManager`` and execute when sessions close.

Hooks:

- ``DailyActivityExtractionHook``  — Extracts conversation summaries
                                      into DailyActivity files.
- ``WorkspaceAutoCommitHook``      — Smart git commit with conventional
                                      commit messages from diffs.
- ``DistillationTriggerHook``      — Checks undistilled file count and
                                      writes a flag for next session.
"""
