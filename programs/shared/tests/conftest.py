"""Shared-suite pytest configuration.

``_retired`` holds tests of shared modules that the current-only cleanup removed
(see ``_retired/README.md``).  They are kept for reference, never collected.
"""

collect_ignore = ["_retired"]
