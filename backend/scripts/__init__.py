"""Backend scripts, run as ``"$SWARM_BACKEND_BIN" py -m scripts.<name>``.

A regular package (not a namespace one) on purpose: Python lets a regular package found
later on ``sys.path`` take precedence over a namespace package found earlier, so a
``scripts`` package in the working directory of a cloned repository would otherwise
replace these modules (``desktop_main.run_py``).
"""
